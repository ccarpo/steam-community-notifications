from steam_feed_notifier.notifier import MAX_NOTIFICATION_BODY_LENGTH, _body
from steam_feed_notifier.parser import Event


def test_notification_body_cap_preserves_link():
    event = Event(
        id="event",
        kind="other",
        actor="Friend",
        actor_profile="",
        summary="x" * 1000,
        link="https://steamcommunity.com/app/123",
        day_timestamp=0,
    )
    body = _body(event)
    summary, link = body.rsplit("\n", 1)
    assert link == event.link
    assert len(summary) == MAX_NOTIFICATION_BODY_LENGTH - len(link) - 1
    assert summary.endswith("…")
