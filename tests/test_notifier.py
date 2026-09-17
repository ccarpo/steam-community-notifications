import pytest

from steam_feed_notifier import notifier
from steam_feed_notifier.notifier import (
    MAX_NOTIFICATION_BODY_LENGTH,
    NotificationError,
    _body,
    _send_ntfy,
    notify,
)
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


def test_send_ntfy_uses_bearer_auth_and_event_click(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        reason = "OK"

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    _send_ntfy(
        "ntfy://token@ntfy.sh/topic",
        "Ä title",
        "body",
        "https://steamcommunity.com/app/1",
    )

    assert calls[0][0] == ("http://ntfy.sh/topic",)
    assert calls[0][1]["headers"]["Authorization"] == "Bearer token"
    assert calls[0][1]["headers"]["X-Click"] == "https://steamcommunity.com/app/1"
    assert calls[0][1]["headers"]["X-Title"].startswith("=?UTF-8?B?")
    assert calls[0][1]["data"] == b"body"


def test_send_ntfy_uses_basic_auth_priority_and_default_topic(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        reason = "OK"

    monkeypatch.setattr(
        notifier.requests,
        "post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(),
    )
    _send_ntfy("ntfys://user:pass@example.test:8443/topic?priority=high", "Title", "body", "")
    _send_ntfy("ntfy://my-topic", "Title", "body", "")

    assert calls[0][0] == ("https://example.test:8443/topic",)
    assert calls[0][1]["auth"] == ("user", "pass")
    assert calls[0][1]["headers"]["X-Priority"] == "high"
    assert calls[1][0] == ("https://ntfy.sh/my-topic",)


def test_ntfy_failure_marks_event_failed_while_apprise_still_receives(monkeypatch):
    apprise_calls = []

    class FakeApprise:
        def add(self, url):
            self.url = url

        def notify(self, **kwargs):
            apprise_calls.append(kwargs)
            return True

    class Response:
        status_code = 429
        reason = "Too Many Requests"

    monkeypatch.setattr(notifier.apprise, "Apprise", FakeApprise)
    monkeypatch.setattr(notifier.requests, "post", lambda *args, **kwargs: Response())
    event = Event(
        id="event",
        kind="other",
        actor="Friend",
        actor_profile="",
        summary="Activity",
        link="https://steamcommunity.com/app/1",
        day_timestamp=0,
    )

    with pytest.raises(NotificationError) as raised:
        notify([event], ["ntfy://ntfy.sh/topic", "discord://example"])

    assert raised.value.failed_events == [event]
    assert apprise_calls[0]["body"] == "Activity\nhttps://steamcommunity.com/app/1"


def test_notify_applies_kind_and_first_played_prefix(capsys):
    event = Event(
        id="event",
        kind="rollup_played",
        actor="Friend",
        actor_profile="",
        summary="Played for the first time.",
        link="",
        day_timestamp=0,
        notification_title="Friend · Game",
    )

    notify(
        [event],
        [],
        dry_run=True,
        prefixes={"rollup_played": "Played", "first_played": "First played"},
    )

    assert capsys.readouterr().out.startswith("[First played] Friend · Game |")
