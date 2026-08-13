from __future__ import annotations

import time
from urllib.parse import urljoin, urlparse

import requests


class SteamFeedError(RuntimeError):
    pass


class SteamFeed:
    def __init__(self, profile: str, cookie: str, session: requests.Session | None = None):
        parsed = urlparse(profile)
        self.profile = parsed.path.rstrip("/").rsplit("/", 1)[-1] if parsed.scheme else profile
        self.cookie = cookie.removeprefix("steamLoginSecure=")
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "steam-feed-notifier/0.1 (+polite personal scraper)"})

    def fetch(self, days: int = 1) -> list[tuple[str, str]]:
        url = f"https://steamcommunity.com/id/{self.profile}/ajaxgetusernews/?l=english"
        result = []
        for index in range(max(1, days)):
            if index:
                time.sleep(0.25)
            response = self.session.get(
                url, cookies={"steamLoginSecure": self.cookie}, timeout=30
            )
            if not response.text.strip():
                raise SteamFeedError(
                    "Steam feed is logged out: cookie expired, grab a fresh steamLoginSecure"
                )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise SteamFeedError("Steam returned invalid feed JSON") from exc
            result.append((url, payload.get("blotter_html", "")))
            if not payload.get("success") or not payload.get("next_request"):
                break
            url = urljoin("https://steamcommunity.com", payload["next_request"])
            if "l=" not in url:
                url += "&l=english"
        return result
