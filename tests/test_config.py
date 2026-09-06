from pathlib import Path

import pytest

from steam_feed_notifier import cli
from steam_feed_notifier.auth import SteamAuthError, TokenStore
from steam_feed_notifier.cli import reload_config
from steam_feed_notifier.config import Config


def test_reload_uses_new_config_and_keeps_last_good_on_partial_yaml(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("STEAM_LOGIN_SECURE", raising=False)
    path = Path(tmp_path) / "config.yaml"
    path.write_text("profile: ccarpo\npoll_interval: 10\nsteam_login_secure: old\n")
    previous = Config.load(str(path))

    path.write_text("profile: [half-written")
    fallback = reload_config(str(path), previous)
    assert fallback == previous
    assert "using last good config" in capsys.readouterr().out

    path.write_text("profile: ccarpo\npoll_interval: 2\nsteam_login_secure: fresh\n")
    updated = reload_config(str(path), fallback)
    assert updated.poll_interval == 2
    assert updated.steam_login_secure == "fresh"


def test_state_file_environment_override(tmp_path, monkeypatch):
    path = Path(tmp_path) / "config.yaml"
    path.write_text("profile: ccarpo\nstate_file: /image/default.json\n")
    monkeypatch.setenv("STEAM_FEED_STATE_FILE", "/state/seen.json")
    assert Config.load(str(path)).state_file == "/state/seen.json"


def test_auth_file_defaults_beside_state_and_has_environment_override(tmp_path, monkeypatch):
    path = Path(tmp_path) / "config.yaml"
    path.write_text("profile: ccarpo\nstate_file: /state/seen.json\n")
    assert Config.load(str(path)).auth_file == "/state/auth.json"
    assert Config(profile="ccarpo", state_file="/state/seen.json").auth_file == "/state/auth.json"

    monkeypatch.setenv("STEAM_FEED_AUTH_FILE", "/custom/auth.json")
    assert Config.load(str(path)).auth_file == "/custom/auth.json"


def test_auth_store_takes_precedence_over_static_cookie(tmp_path, monkeypatch):
    auth_file = Path(tmp_path) / "auth.json"
    TokenStore(str(auth_file)).save(
        {"steamid": "1", "refresh_token": "refresh", "cookie": "cookie", "cookie_expiry": 0}
    )
    config = Config(
        profile="ccarpo",
        steam_login_secure="static",
        auth_file=str(auth_file),
    )
    captured = {}

    class FakeFeed:
        def __init__(self, profile, provider, session):
            captured["provider"] = provider

        def fetch(self, days):
            return [("", "")]

    monkeypatch.setattr(cli, "SteamFeed", FakeFeed)
    assert cli._load_html(config) == [""]
    assert captured["provider"].tokens["refresh_token"] == "refresh"


def test_missing_authentication_names_login_command(tmp_path):
    config = Config(
        profile="ccarpo",
        auth_file=str(Path(tmp_path) / "missing.json"),
    )
    with pytest.raises(SteamAuthError, match="login"):
        cli._load_html(config)
