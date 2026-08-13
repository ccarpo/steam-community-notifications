import apprise

from .parser import Event

MAX_NOTIFICATION_BODY_LENGTH = 1200


class NotificationError(RuntimeError):
    def __init__(self, message: str, failed_events: list[Event]):
        super().__init__(message)
        self.failed_events = failed_events


def _body(event: Event) -> str:
    link = f"\n{event.link}" if event.link else ""
    max_summary = MAX_NOTIFICATION_BODY_LENGTH - len(link)
    summary = event.summary
    if len(summary) > max_summary:
        summary = summary[: max_summary - 1].rstrip() + "…"
    return summary + link


def notify(
    events: list[Event],
    urls: list[str],
    dry_run: bool = False,
    on_success=None,
) -> None:
    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)
    failures: list[str] = []
    failed_events: list[Event] = []
    for event in events:
        title = f"Steam · {event.actor or 'Activity'}"
        body = _body(event)
        if dry_run:
            print(f"{title} | {event.summary} | {event.link}")
            if on_success:
                on_success(event)
            continue
        if not urls:
            failures.append(f"{title}: No apprise_urls configured")
            failed_events.append(event)
            continue
        try:
            delivered = apobj.notify(body=body, title=title)
        except Exception as exc:  # noqa: BLE001 - isolate one failed event delivery
            failures.append(f"{title}: {exc}")
            failed_events.append(event)
        else:
            if not delivered:
                failures.append(f"{title}: Apprise returned failure")
                failed_events.append(event)
            elif on_success:
                on_success(event)
    if failures:
        raise NotificationError("Notification failures: " + " | ".join(failures), failed_events)
