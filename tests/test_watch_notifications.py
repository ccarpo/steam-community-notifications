from steam_feed_notifier import cli
from steam_feed_notifier.auth import SteamAuthExpiredError
from steam_feed_notifier.config import Config


def test_poll_errors_notify_once_per_message_and_recovery_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "send_message",
        lambda title, body, urls: calls.append((title, body, urls)),
    )
    config = Config(profile="example", apprise_urls=["ntfy://ntfy.sh/topic"])

    last_error = cli._handle_poll_error(config, RuntimeError("network down"), None)
    last_error = cli._handle_poll_error(
        config, RuntimeError("network down"), last_error
    )
    last_error = cli._handle_poll_error(
        config, RuntimeError("Steam unavailable"), last_error
    )

    assert [call[0] for call in calls] == [
        "[Error] Steam feed notifier",
        "[Error] Steam feed notifier",
    ]
    assert calls[0][1] == "network down"
    assert calls[1][1] == "Steam unavailable"

    cli._handle_poll_recovery(config, last_error)
    last_error = None
    cli._handle_poll_recovery(config, last_error)

    assert calls[-1] == (
        "[Recovered] Steam feed notifier",
        "Polling works again.",
        config.apprise_urls,
    )
    assert len(calls) == 3


def test_auth_expiry_notification_includes_compose_login_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "send_message",
        lambda title, body, urls: calls.append((title, body, urls)),
    )
    error = SteamAuthExpiredError("Steam authentication failed (eresult 15)")

    cli._handle_poll_error(Config(profile="example"), error, None)

    assert calls[0][0] == "[Error] Steam feed notifier"
    assert calls[0][1] == (
        "Steam authentication failed (eresult 15)\n"
        "Re-run: docker compose run --rm steam-feed-notifier "
        "--config /config/config.yaml login"
    )


def test_error_notification_failure_does_not_propagate_or_print_exception(
    monkeypatch, capsys
):
    def fail(*args, **kwargs):
        raise RuntimeError("cookie=private-value")

    monkeypatch.setattr(cli, "send_message", fail)

    assert cli._handle_poll_error(
        Config(profile="example"), RuntimeError("network down"), None
    ) == "network down"
    output = capsys.readouterr().out
    assert "error notification failed: RuntimeError" in output
    assert "private-value" not in output


def test_notify_errors_can_be_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "send_message",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    cli._handle_poll_error(
        Config(profile="example", notify_errors=False),
        RuntimeError("network down"),
        None,
    )

    assert calls == []
