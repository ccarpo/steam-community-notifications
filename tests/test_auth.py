import base64
import json
import os
import time
from pathlib import Path

import pytest

from steam_feed_notifier.auth import (
    AuthManager,
    SteamAuthError,
    SteamAuthExpiredError,
    TokenStore,
    begin_qr_session,
    jwt_claims,
    login_via_qr,
    mint_access_token,
)
from steam_feed_notifier.cli import _format_expiry, _parser
from steam_feed_notifier.fetcher import SteamFeed, SteamFeedError


def token(**claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


class FakeResponse:
    def __init__(self, value, headers=None, text=None):
        self.value = value
        self.headers = headers or {"x-eresult": "1"}
        self.text = json.dumps(value) if text is None else text

    def json(self):
        return self.value

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []
        self.headers = {}

    def post(self, url, data, timeout):
        self.post_calls.append((url, data, timeout))
        return self.posts.pop(0)

    def get(self, url, cookies, timeout):
        self.get_calls.append((url, cookies, timeout))
        return self.gets.pop(0)


def test_jwt_claims_decodes_unpadded_payload():
    value = token(sub="76561198000000001", exp=123)
    assert jwt_claims(value) == {"sub": "76561198000000001", "exp": 123}


def test_token_store_missing_and_atomic_private_save(tmp_path):
    path = Path(tmp_path) / "nested" / "auth.json"
    store = TokenStore(str(path))
    assert store.load() is None

    store.save({"steamid": "1", "refresh_token": "refresh", "access_token": "access"})
    assert store.load() == {
        "steamid": "1",
        "refresh_token": "refresh",
        "access_token": "access",
    }
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert list(path.parent.glob(".*.auth.json.*")) == []


@pytest.mark.parametrize("eresult", ["5", "15"])
def test_mint_rejected_refresh_token_is_expired(eresult):
    session = FakeSession([FakeResponse({"response": {}}, {"x-eresult": eresult})])
    with pytest.raises(SteamAuthExpiredError, match="re-run login"):
        mint_access_token(session, "refresh", "1")


def test_auth_api_missing_token_and_other_eresult_are_errors():
    session = FakeSession([FakeResponse({"response": {}}, {"x-eresult": "42"})])
    with pytest.raises(SteamAuthError, match="eresult 42"):
        mint_access_token(session, "refresh", "1")

    session = FakeSession([FakeResponse({"response": {}})])
    with pytest.raises(SteamAuthError, match="access token"):
        mint_access_token(session, "refresh", "1")


def test_begin_qr_session_uses_mobile_app_platform():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "response": {
                        "client_id": "client",
                        "challenge_url": "https://qr",
                        "request_id": "request",
                    }
                }
            )
        ]
    )

    begin_qr_session(session, "device")

    assert session.post_calls[0][1] == {
        "device_friendly_name": "device",
        "platform_type": "3",
        "website_id": "Community",
    }


def test_qr_login_polls_and_handles_rotated_challenge(monkeypatch):
    refresh = token(sub="76561198000000001", exp=int(time.time()) + 100000)
    session = FakeSession(
        [
            FakeResponse(
                {
                    "response": {
                        "client_id": "client",
                        "challenge_url": "https://qr/one",
                        "request_id": "request",
                        "interval": 1,
                    }
                }
            ),
            FakeResponse(
                {
                    "response": {
                        "new_client_id": "rotated-client",
                        "new_challenge_url": "https://qr/two",
                    }
                }
            ),
            FakeResponse(
                {"response": {"refresh_token": refresh, "access_token": "access"}}
            ),
        ]
    )
    challenges = []
    monkeypatch.setattr("steam_feed_notifier.auth.time.sleep", lambda _: None)

    result = login_via_qr(session, "test-device", challenges.append, timeout=10)

    assert result == ("76561198000000001", refresh, "access")
    assert challenges == ["https://qr/one", "https://qr/two"]
    assert session.post_calls[1][1]["client_id"] == "client"
    assert session.post_calls[2][1]["client_id"] == "rotated-client"


def test_auth_manager_mints_near_expiry_access_token(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    session = FakeSession(
        [
            FakeResponse(
                {
                    "response": {
                        "access_token": token(exp=int(time.time()) + 3600),
                    }
                }
            )
        ]
    )
    store = TokenStore(str(Path(tmp_path) / "auth.json"))
    store.save(
        {
            "steamid": "1",
            "refresh_token": refresh,
            "access_token": token(exp=int(time.time()) + 100),
        }
    )
    manager = AuthManager(store, session)

    assert manager.cookie().startswith("1%7C%7C")
    assert session.post_calls[0][1]["renewal_type"] == "0"


def test_auth_manager_renews_refresh_token_and_persists_rotation(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 24 * 60 * 60)
    rotated = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    access = token(exp=int(time.time()) + 3600)
    session = FakeSession(
        [
            FakeResponse(
                {"response": {"access_token": access, "refresh_token": rotated}}
            )
        ]
    )
    store = TokenStore(str(Path(tmp_path) / "auth.json"))
    store.save({"steamid": "1", "refresh_token": refresh, "access_token": ""})
    manager = AuthManager(store, session)

    manager.cookie()

    assert session.post_calls[0][1]["renewal_type"] == "1"
    assert store.load()["refresh_token"] == rotated
    assert store.load()["access_token"] == access


def test_auth_manager_status_with_and_without_access_token(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    access = token(exp=int(time.time()) + 3600)
    path = Path(tmp_path) / "auth.json"
    store = TokenStore(str(path))
    store.save({"steamid": "1", "refresh_token": refresh, "access_token": access})
    status = AuthManager(store, FakeSession()).status()
    assert status["steamid"] == "1"
    assert status["access_expiry"] == jwt_claims(access)["exp"]
    assert status["refresh_expiry"] == jwt_claims(refresh)["exp"]
    assert status["renewal_window"] is False

    store.save({"steamid": "1", "refresh_token": refresh})
    status = AuthManager(store, FakeSession()).status()
    assert status["access_expiry"] == 0
    assert status["refresh_expiry"] == jwt_claims(refresh)["exp"]


def test_login_parser_defaults_to_ten_minute_timeout():
    args = _parser().parse_args(["login"])
    assert args.timeout == 600
    assert _parser().parse_args(["login", "--timeout", "42"]).timeout == 42
    assert _format_expiry(0) == "unknown"
    assert _format_expiry(None) == "unknown"


def test_fetcher_refreshes_once_after_logged_out_response():
    class Provider:
        def __init__(self):
            self.refreshes = 0

        def __call__(self):
            return f"cookie-{self.refreshes}"

        def force_refresh(self):
            self.refreshes += 1

    provider = Provider()
    session = FakeSession(
        gets=[
            FakeResponse({}, text=""),
            FakeResponse({"success": True, "blotter_html": "<p>ok</p>"}),
        ]
    )
    result = SteamFeed("example", provider, session=session).fetch()

    assert result == [("https://steamcommunity.com/id/example/ajaxgetusernews/?l=english", "<p>ok</p>")]
    assert provider.refreshes == 1
    assert len(session.get_calls) == 2


def test_fetcher_static_cookie_keeps_logged_out_error():
    session = FakeSession(gets=[FakeResponse({}, text="")])
    with pytest.raises(SteamFeedError, match="grab a fresh steamLoginSecure"):
        SteamFeed("example", "steamLoginSecure=static", session=session).fetch()
