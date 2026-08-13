import apprise

from .parser import Event


def notify(events: list[Event], urls: list[str], dry_run: bool = False) -> None:
    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)
    failures: list[str] = []
    for event in events:
        title = f"Steam · {event.actor or 'Activity'}"
        body = event.summary + (f"\n{event.link}" if event.link else "")
        if dry_run:
            print(f"{title} | {event.summary} | {event.link}")
            continue
        if not urls:
            failures.append("No apprise_urls configured")
            continue
        try:
            delivered = apobj.notify(body=body, title=title)
        except Exception as exc:  # noqa: BLE001 - isolate one failed event delivery
            failures.append(f"{title}: {exc}")
        else:
            if not delivered:
                failures.append(f"{title}: Apprise returned failure")
    if failures:
        raise RuntimeError("Notification failures: " + " | ".join(failures))
