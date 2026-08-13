from pathlib import Path

from steam_feed_notifier.cli import run_once
from steam_feed_notifier.config import Config
from steam_feed_notifier.state import SeenState


def test_seen_state_is_idempotent(tmp_path):
    path = str(tmp_path / "seen.json")
    state = SeenState(path)
    state.load()
    state.add(["a", "b", "a"])
    again = SeenState(path)
    again.load()
    assert again.ids == ["a", "b"]
    again.add(["a", "b"])
    final = SeenState(path)
    final.load()
    assert final.ids == ["a", "b"]


def test_notification_cap_defers_overflow(tmp_path):
    config = Config(
        profile="example",
        state_file=str(tmp_path / "seen.json"),
        dry_run=True,
        max_notifications_per_poll=2,
        seed_days=1,
    )
    fixture_dir = str(Path(__file__).parent / "fixtures")
    run_once(config, first_run_notify=True, fixture_dir=fixture_dir)
    state = SeenState(config.state_file)
    state.load()
    assert len(state.ids) == 2
