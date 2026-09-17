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


def test_seen_achievements_round_trip_and_incremental_names(tmp_path):
    path = str(tmp_path / "seen.json")
    state = SeenState(path)
    state.load()
    assert state.unseen_achievements("key", ["A", "B"]) == ["A", "B"]
    state.add_achievements("key", ["A"])

    restored = SeenState(path)
    restored.load()
    assert restored.unseen_achievements("key", ["B", "A"]) == ["B"]
    restored.add_achievements("key", ["B", "A"])
    final = SeenState(path)
    final.load()
    assert final.achievements == {"key": ["A", "B"]}


def test_seen_achievements_keeps_last_500_keys(tmp_path):
    state = SeenState(str(tmp_path / "seen.json"))
    for index in range(501):
        state.add_achievements(str(index), [str(index)])
    state.load()
    assert len(state.achievements) == 500
    assert "0" not in state.achievements
    assert "500" in state.achievements


def test_achievement_rollup_only_notifies_new_names(tmp_path, monkeypatch):
    from steam_feed_notifier.parser import Event

    events = [
        Event("one", "rollup_achievement", "Friend", "profile", "A", "", 1, "", ["A"], "Game"),
        Event("two", "rollup_achievement", "Friend", "profile", "B; A", "", 1, "", ["B", "A"], "Game"),
        Event("three", "rollup_achievement", "Friend", "profile", "B; A", "", 1, "", ["B", "A"], "Game"),
    ]
    calls = []
    monkeypatch.setattr("steam_feed_notifier.cli._load_html", lambda *args, **kwargs: ["ignored"])
    monkeypatch.setattr("steam_feed_notifier.cli.parse_events", lambda _: [events.pop(0)])

    def fake_notify(pending, urls, dry_run=False, on_success=None, prefixes=None):
        calls.append([(event.id, event.summary) for event in pending])
        for event in pending:
            on_success(event)

    monkeypatch.setattr("steam_feed_notifier.cli.notify", fake_notify)
    config = Config(profile="example", state_file=str(tmp_path / "seen.json"))

    run_once(config, first_run_notify=True)
    run_once(config, first_run_notify=True)
    run_once(config, first_run_notify=True)

    assert calls == [[("one", "A")], [("two", "B")]]
