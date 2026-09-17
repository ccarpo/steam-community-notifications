import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_TITLE_PREFIXES = {
    "group_announcement": "Announcement",
    "rollup_achievement": "Achievement",
    "game_purchase": "New game",
    "rollup_played": "Played",
    "rollup_wishlist": "Wishlist",
    "screenshot": "Screenshots",
    "first_played": "First played",
}


@dataclass
class Config:
    profile: str
    steam_login_secure: str = ""
    apprise_urls: list[str] = field(default_factory=list)
    poll_interval: int = 300
    state_file: str = "~/.local/state/steam-feed-notifier/seen.json"
    auth_file: str | None = None
    include_kinds: list[str] = field(default_factory=list)
    exclude_kinds: list[str] = field(default_factory=list)
    max_notifications_per_poll: int = 20
    dry_run: bool = False
    seed_days: int = 2
    title_prefixes: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_TITLE_PREFIXES)
    )

    def __post_init__(self):
        if not self.auth_file:
            self.auth_file = str(Path(self.state_file).expanduser().parent / "auth.json")
        if not isinstance(self.title_prefixes, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.title_prefixes.items()
        ):
            raise ValueError("title_prefixes must be a mapping of strings to strings")
        merged = dict(DEFAULT_TITLE_PREFIXES)
        merged.update(self.title_prefixes)
        self.title_prefixes = merged

    @classmethod
    def load(cls, path: str) -> "Config":
        raw: dict[str, Any] = yaml.safe_load(Path(path).expanduser().read_text()) or {}
        if os.getenv("STEAM_LOGIN_SECURE"):
            raw["steam_login_secure"] = os.environ["STEAM_LOGIN_SECURE"]
        if os.getenv("STEAM_FEED_STATE_FILE"):
            raw["state_file"] = os.environ["STEAM_FEED_STATE_FILE"]
        if os.getenv("STEAM_FEED_AUTH_FILE"):
            raw["auth_file"] = os.environ["STEAM_FEED_AUTH_FILE"]
        profile = raw.get("profile", raw.get("profile_url", raw.get("vanity_id")))
        if not profile:
            raise ValueError("config must define profile (a Steam vanity ID or profile URL)")
        state_file = str(raw.get("state_file", cls.state_file))
        auth_file = str(
            raw.get("auth_file", Path(state_file).expanduser().parent / "auth.json")
        )
        title_prefixes = raw.get("title_prefixes", {})
        if not isinstance(title_prefixes, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in title_prefixes.items()
        ):
            raise ValueError("title_prefixes must be a mapping of strings to strings")
        return cls(
            profile=str(profile),
            steam_login_secure=str(raw.get("steam_login_secure", "")).removeprefix(
                "steamLoginSecure="
            ),
            apprise_urls=list(raw.get("apprise_urls", [])),
            poll_interval=int(raw.get("poll_interval", 300)),
            state_file=state_file,
            auth_file=auth_file,
            include_kinds=list(raw.get("include_kinds", raw.get("event_kinds", {}).get("include", []))),
            exclude_kinds=list(raw.get("exclude_kinds", raw.get("event_kinds", {}).get("exclude", []))),
            max_notifications_per_poll=int(raw.get("max_notifications_per_poll", 20)),
            dry_run=bool(raw.get("dry_run", False)),
            seed_days=int(raw.get("seed_days", raw.get("initial_days", 2))),
            title_prefixes=title_prefixes,
        )
