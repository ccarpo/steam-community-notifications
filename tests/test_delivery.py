from pathlib import Path

from steam_feed_notifier import cli
from steam_feed_notifier.config import Config
from steam_feed_notifier.notifier import NotificationError
from steam_feed_notifier.state import SeenState


def test_delivery_failure_does_not_repeat_successful_events(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    failed_id: str | None = None

    def fake_notify(events, urls, dry_run=False, on_success=None):
        nonlocal failed_id
        calls.append([event.id for event in events])
        failures = []
        for event in events:
            if failed_id is None and len(events) > 1:
                failed_id = events[1].id
            if event.id == failed_id:
                failures.append(event)
            else:
                on_success(event)
        if failures:
            raise NotificationError("one delivery failed", failures)

    monkeypatch.setattr(cli, "notify", fake_notify)
    config = Config(
        profile="example",
        state_file=str(tmp_path / "seen.json"),
        include_kinds=["group_announcement"],
        max_notifications_per_poll=2,
        seed_days=1,
    )
    fixture_dir = str(Path(__file__).parent / "fixtures")

    cli.run_once(config, first_run_notify=True, fixture_dir=fixture_dir)
    first = SeenState(config.state_file)
    first.load()
    assert len(first.ids) == 2

    cli.run_once(config, first_run_notify=True, fixture_dir=fixture_dir)
    second = SeenState(config.state_file)
    second.load()
    assert len(second.ids) == 4
    assert calls[0][0] not in calls[1]
    assert calls[0][0] not in calls[2]
    assert failed_id not in calls[2]
