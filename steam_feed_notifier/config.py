import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    profile: str
    steam_login_secure: str = ""
    apprise_urls: list[str] = field(default_factory=list)
    poll_interval: int = 300
    state_file: str = "~/.local/state/steam-feed-notifier/seen.json"
    include_kinds: list[str] = field(default_factory=list)
    exclude_kinds: list[str] = field(default_factory=list)
    max_notifications_per_poll: int = 20
    dry_run: bool = False
    seed_days: int = 2

    @classmethod
    def load(cls, path: str) -> "Config":
        raw: dict[str, Any] = yaml.safe_load(Path(path).expanduser().read_text()) or {}
        if os.getenv("STEAM_LOGIN_SECURE"):
            raw["steam_login_secure"] = os.environ["STEAM_LOGIN_SECURE"]
        profile = raw.get("profile", raw.get("profile_url", raw.get("vanity_id")))
        if not profile:
            raise ValueError("config must define profile (a Steam vanity ID or profile URL)")
        return cls(
            profile=str(profile),
            steam_login_secure=str(raw.get("steam_login_secure", "")).removeprefix(
                "steamLoginSecure="
            ),
            apprise_urls=list(raw.get("apprise_urls", [])),
            poll_interval=int(raw.get("poll_interval", 300)),
            state_file=str(raw.get("state_file", cls.state_file)),
            include_kinds=list(raw.get("include_kinds", raw.get("event_kinds", {}).get("include", []))),
            exclude_kinds=list(raw.get("exclude_kinds", raw.get("event_kinds", {}).get("exclude", []))),
            max_notifications_per_poll=int(raw.get("max_notifications_per_poll", 20)),
            dry_run=bool(raw.get("dry_run", False)),
            seed_days=int(raw.get("seed_days", raw.get("initial_days", 2))),
        )
