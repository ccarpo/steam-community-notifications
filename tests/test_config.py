from pathlib import Path

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
