import base64
from urllib.parse import parse_qs, unquote, urlparse

import apprise
import requests

from .parser import Event

MAX_NOTIFICATION_BODY_LENGTH = 400


class NotificationError(RuntimeError):
    def __init__(self, message: str, failed_events: list[Event]):
        super().__init__(message)
        self.failed_events = failed_events


def _body(event: Event, include_link: bool = True) -> str:
    link = f"\n{event.link}" if include_link and event.link else ""
    max_summary = MAX_NOTIFICATION_BODY_LENGTH - len(link)
    summary = event.summary
    if len(summary) > max_summary:
        summary = summary[: max_summary - 1].rstrip() + "…"
    return summary + link


def _is_ntfy(url: str) -> bool:
    return urlparse(url).scheme.lower() in {"ntfy", "ntfys"}


def _ntfy_parts(url: str) -> tuple[str, str, tuple[str, str] | None, str | None, dict[str, str]]:
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme.lower() == "ntfys" else "http"
    hostname = parsed.hostname or ""
    username = unquote(parsed.username or "")
    password = unquote(parsed.password) if parsed.password is not None else None
    auth = None
    bearer = None
    if username and password is None:
        bearer = username
    elif username:
        auth = (username, password or "")
    query = parse_qs(parsed.query)
    headers = {"X-Priority": query["priority"][0]} if query.get("priority") else {}
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        topic = unquote(path_parts[0])
        server = f"{scheme}://{parsed.netloc.rsplit('@', 1)[-1]}"
    else:
        topic = hostname
        server = "https://ntfy.sh"
    if not topic or not hostname:
        raise ValueError("ntfy URL must include a topic")
    return server, topic, auth, bearer, headers


def _ntfy_title(title: str) -> str:
    if title.isascii():
        return title
    encoded = base64.b64encode(title.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{encoded}?="


def _send_ntfy(url: str, title: str, body: str, click: str) -> None:
    server, topic, auth, bearer, headers = _ntfy_parts(url)
    headers["X-Title"] = _ntfy_title(title)
    if click:
        headers["X-Click"] = click
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    response = requests.post(
        f"{server.rstrip('/')}/{topic}",
        data=body.encode("utf-8"),
        headers=headers,
        auth=auth,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        reason = getattr(response, "reason", "")
        raise RuntimeError(f"ntfy returned HTTP {response.status_code}: {reason}".rstrip())


def _prefix(event: Event, prefixes: dict[str, str] | None) -> str:
    if not prefixes:
        return ""
    if event.kind == "rollup_played" and "for the first time" in event.summary.lower():
        return prefixes.get("first_played", "")
    return prefixes.get(event.kind, "")


def notify(
    events: list[Event],
    urls: list[str],
    dry_run: bool = False,
    on_success=None,
    prefixes: dict[str, str] | None = None,
) -> None:
    ntfy_urls = [url for url in urls if _is_ntfy(url)]
    apprise_urls = [url for url in urls if not _is_ntfy(url)]
    apobj = apprise.Apprise()
    for url in apprise_urls:
        apobj.add(url)
    failures: list[str] = []
    failed_events: list[Event] = []
    for event in events:
        title = event.notification_title or event.actor or "Activity"
        prefix = _prefix(event, prefixes)
        if prefix:
            title = f"[{prefix}] {title}"
        if dry_run:
            print(f"{title} | {_body(event)}")
            if on_success:
                on_success(event)
            continue
        if not urls:
            failures.append(f"{title}: No apprise_urls configured")
            failed_events.append(event)
            continue
        event_failures: list[str] = []
        for url in ntfy_urls:
            try:
                _send_ntfy(url, title, _body(event, include_link=False), event.link)
            except Exception as exc:  # noqa: BLE001 - isolate one failed event delivery
                event_failures.append(f"{url}: {exc}")
        if apprise_urls:
            try:
                delivered = apobj.notify(body=_body(event), title=title)
            except Exception as exc:  # noqa: BLE001 - isolate one failed event delivery
                event_failures.append(str(exc))
            else:
                if not delivered:
                    event_failures.append("Apprise returned failure")
        if event_failures:
            failures.append(f"{title}: {' | '.join(event_failures)}")
            failed_events.append(event)
        elif on_success:
            on_success(event)
    if failures:
        raise NotificationError("Notification failures: " + " | ".join(failures), failed_events)
