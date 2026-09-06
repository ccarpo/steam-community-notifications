import base64
import json
import os
import time
from pathlib import Path

import pytest

from steam_feed_notifier.auth import (
    AUTH_HEADERS,
    AuthManager,
    SteamAuthExpiredError,
    TokenStore,
    begin_qr_session,
    jwt_claims,
    login_via_qr,
    mint_web_cookie,
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

    def post(self, url, data=None, files=None, headers=None, timeout=None):
        self.post_calls.append(
            {
                "url": url,
                "data": data,
                "files": files,
                "headers": headers,
                "timeout": timeout,
            }
        )
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

    value = {
        "steamid": "1",
        "refresh_token": "refresh",
        "cookie": "cookie",
        "cookie_expiry": 123,
    }
    store.save(value)
    assert store.load() == value
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert list(path.parent.glob(".*.auth.json.*")) == []


@pytest.mark.parametrize("eresult", ["5", "15"])
def test_auth_api_rejected_refresh_token_is_expired(eresult):
    session = FakeSession([FakeResponse({"response": {}}, {"x-eresult": eresult})])
    with pytest.raises(SteamAuthExpiredError, match="re-run login"):
        begin_qr_session(session, "device")


def test_begin_qr_session_uses_web_browser_platform_and_headers():
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

    call = session.post_calls[0]
    assert call["data"] == {
        "device_friendly_name": AUTH_HEADERS["User-Agent"],
        "platform_type": "2",
        "website_id": "Community",
    }
    assert call["headers"] == AUTH_HEADERS


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
            FakeResponse({"response": {"refresh_token": refresh}}),
        ]
    )
    challenges = []
    monkeypatch.setattr("steam_feed_notifier.auth.time.sleep", lambda _: None)

    result = login_via_qr(session, "test-device", challenges.append, timeout=10)

    assert result == ("76561198000000001", refresh)
    assert challenges == ["https://qr/one", "https://qr/two"]
    assert session.post_calls[1]["data"]["client_id"] == "client"
    assert session.post_calls[2]["data"]["client_id"] == "rotated-client"


def test_mint_web_cookie_uses_multipart_and_extracts_rotation(monkeypatch):
    rotated = token(sub="1", exp=int(time.time()) + 100000)
    cookie_token = token(sub="1", exp=int(time.time()) + 3600)
    cookie = f"1%7C%7C{cookie_token}"
    session = FakeSession(
        [
            FakeResponse(
                {"transfer_info": [{"url": "https://steamcommunity.com/login", "params": {"a": "b"}}]},
                {"set-cookie": [f"steamRefresh_steam=1%7C%7C{rotated}; Path=/"]},
            ),
            FakeResponse(
                {"result": 1},
                {"set-cookie": [f"steamLoginSecure={cookie}; Path=/"]},
            ),
        ]
    )
    monkeypatch.setattr("steam_feed_notifier.auth.time.sleep", lambda _: None)

    result = mint_web_cookie(session, "refresh", "1")

    assert result == (cookie, rotated)
    finalize = session.post_calls[0]
    assert finalize["url"].endswith("/jwt/finalizelogin")
    assert finalize["files"]["nonce"] == (None, "refresh")
    assert finalize["files"]["redir"] == (None, "https://steamcommunity.com/login/home/?goto=")
    assert finalize["headers"] == AUTH_HEADERS
    transfer = session.post_calls[1]
    assert transfer["files"]["steamID"] == (None, "1")
    assert transfer["files"]["a"] == (None, "b")


def test_mint_web_cookie_error_8_is_expired():
    session = FakeSession([FakeResponse({"success": False, "error": 8})])
    with pytest.raises(SteamAuthExpiredError, match="re-run login"):
        mint_web_cookie(session, "refresh", "1")


def test_auth_manager_mints_near_expiry_cookie_and_persists_cookie(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    cookie = f"1%7C%7C{token(sub='1', exp=int(time.time()) + 100)}"
    new_cookie = f"1%7C%7C{token(sub='1', exp=int(time.time()) + 7200)}"
    session = FakeSession(
        [
            FakeResponse(
                {"transfer_info": [{"url": "https://steamcommunity.com/login", "params": {}}]}
            ),
            FakeResponse(
                {"result": 1},
                {"set-cookie": [f"steamLoginSecure={new_cookie}; Path=/"]},
            ),
        ]
    )
    store = TokenStore(str(Path(tmp_path) / "auth.json"))
    store.save(
        {
            "steamid": "1",
            "refresh_token": refresh,
            "cookie": cookie,
            "cookie_expiry": int(time.time()) + 100,
        }
    )
    manager = AuthManager(store, session)

    assert manager.cookie() == new_cookie
    assert store.load()["cookie"] == new_cookie
    assert store.load()["cookie_expiry"] == jwt_claims(new_cookie.split("%7C%7C")[1])["exp"]


def test_auth_manager_persists_rotated_refresh_token(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 3600)
    rotated = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    new_cookie = f"1%7C%7C{token(sub='1', exp=int(time.time()) + 7200)}"
    session = FakeSession(
        [
            FakeResponse(
                {"transfer_info": [{"url": "https://steamcommunity.com/login", "params": {}}]},
                {"set-cookie": [f"steamRefresh_steam=1%7C%7C{rotated}; Path=/"]},
            ),
            FakeResponse({"result": 1}, {"set-cookie": [f"steamLoginSecure={new_cookie}; Path=/"]}),
        ]
    )
    store = TokenStore(str(Path(tmp_path) / "auth.json"))
    store.save({"steamid": "1", "refresh_token": refresh})

    AuthManager(store, session).cookie()

    assert store.load()["refresh_token"] == rotated


def test_auth_manager_status_reports_cookie_and_refresh_expiry(tmp_path):
    refresh = token(sub="1", exp=int(time.time()) + 90 * 24 * 60 * 60)
    cookie = f"1%7C%7C{token(sub='1', exp=int(time.time()) + 3600)}"
    path = Path(tmp_path) / "auth.json"
    store = TokenStore(str(path))
    store.save({"steamid": "1", "refresh_token": refresh, "cookie": cookie})
    status = AuthManager(store, FakeSession()).status()
    assert status == {
        "steamid": "1",
        "cookie_expiry": jwt_claims(cookie.split("%7C%7C")[1])["exp"],
        "refresh_expiry": jwt_claims(refresh)["exp"],
    }

    store.save({"steamid": "1", "refresh_token": refresh})
    assert AuthManager(store, FakeSession()).status()["cookie_expiry"] == 0


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

    assert result == [
        ("https://steamcommunity.com/id/example/ajaxgetusernews/?l=english", "<p>ok</p>")
    ]
    assert provider.refreshes == 1
    assert len(session.get_calls) == 2


def test_fetcher_static_cookie_keeps_logged_out_error():
    session = FakeSession(gets=[FakeResponse({}, text="")])
    with pytest.raises(SteamFeedError, match="grab a fresh steamLoginSecure"):
        SteamFeed("example", "steamLoginSecure=static", session=session).fetch()
