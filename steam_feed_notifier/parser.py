from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass
class Event:
    id: str
    kind: str
    actor: str
    actor_profile: str
    summary: str
    link: str
    day_timestamp: int

    def as_dict(self) -> dict:
        return asdict(self)


def _text(node: Tag) -> str:
    visible = [
        string.strip()
        for string in node.strings
        if string.strip()
        and not any("nickname_block" in (parent.get("class") or []) for parent in string.parents)
    ]
    return re.sub(r"\s+", " ", " ".join(visible)).strip()


def _persona(node: Tag) -> str:
    direct = "".join(str(child) for child in node.children if isinstance(child, NavigableString))
    return re.sub(r"\s+", " ", direct).strip() or _text(node)


def _actor(node: Tag) -> tuple[str, str]:
    author = node.select_one(".blotter_author_block a[data-miniprofile][href]")
    if not author:
        author = node.select_one("span a[data-miniprofile][href]")
    if not author:
        author = node.select_one(".blotter_rollup_avatar a[href*='steamcommunity.com']")
        if author:
            profile = author.get("href", "")
            text_author = node.select_one("a[data-miniprofile][href]")
            return (_text(text_author) if text_author else "", profile)
    if not author:
        return "", ""
    return _persona(author), urljoin("https://steamcommunity.com", author.get("href", ""))


def _link(node: Tag) -> str:
    preferred = node.select_one(
        ".blotter_group_announcement_headline a, .blotter_gamepurchase_text a, "
        ".blotter_gamepurchase_details a, a[href*='/status/'], a[href*='/app/'], "
        ".blotter_screenshot_title a"
    )
    if not preferred:
        preferred = node.select_one("a[href]")
    return urljoin("https://steamcommunity.com", preferred.get("href", "")) if preferred else ""


def _kind(node: Tag, text: str) -> str:
    classes = set(node.get("class", []))
    if "blotter_daily_rollup_line" in classes:
        if "wishlist" in text.lower() or "added" in text.lower():
            return "rollup_wishlist"
        if "achieved" in text.lower() or "achievement" in text.lower():
            return "rollup_achievement"
        if "played" in text.lower():
            return "rollup_played"
    if "blotter_gamepurchase" in classes:
        return "game_purchase"
    if "blotter_screenshot" in classes:
        return "screenshot"
    if "blotter_userstatus" in classes:
        if node.select_one(".blotter_group_announcement_headline, .blotter_group_announcement_header_text"):
            return "group_announcement"
        return "user_status"
    if node.select_one(".blotter_group_announcement_headline"):
        return "group_announcement"
    return "other"


def _summary(node: Tag, kind: str) -> str:
    summary = _text(node)
    if kind == "rollup_achievement":
        titles = [
            re.sub(r"\s+", " ", image["title"]).strip()
            for image in node.select("img[title]")
            if image.get("title", "").strip()
        ]
        additions = [title for title in titles if title not in summary]
        if additions:
            summary = f"{summary} {'; '.join(additions)}"
    return summary


def parse_events(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []
    for day in soup.select(".blotter_day"):
        try:
            timestamp = int((day.get("id") or "").rsplit("_", 1)[1])
        except (IndexError, ValueError):
            timestamp = 0
        blocks: list[Tag] = []
        for block in day.select(":scope > .blotter_block"):
            rollups = block.select(".blotter_daily_rollup_line")
            candidates = [
                child for child in block.find_all(recursive=False)
                if set(child.get("class", []))
                & {"blotter_gamepurchase", "blotter_userstatus", "blotter_screenshot"}
            ]
            blocks.extend(rollups or candidates or [block])
        for node in blocks:
            kind = _kind(node, _text(node))
            summary = _summary(node, kind)
            if not summary:
                continue
            actor, profile = _actor(node)
            link = _link(node)
            normalized = f"{timestamp}|{summary}|{link}"
            event_id = node.get("id") or hashlib.sha256(normalized.encode()).hexdigest()[:24]
            events.append(Event(event_id, kind, actor, profile, summary, link, timestamp))
    return events
