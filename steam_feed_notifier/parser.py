from __future__ import annotations

import hashlib
import re
from copy import deepcopy
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
    notification_title: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


MAX_SUMMARY_LENGTH = 400
LEGACY_SUMMARY_LENGTH = 1000
MAX_ANNOUNCEMENT_BODY_LENGTH = 350
MAX_NOTIFICATION_TITLE_LENGTH = 60
_INTERACTION_SELECTORS = (
    ".commentthread_area",
    ".blotter_comment_thread",
    ".blotter_viewallcomments_container",
    ".blotter_control_container",
    ".blotter_voters_names",
    ".blotter_group_announcement_rating_controls",
)


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


def _link(node: Tag, kind: str = "") -> str:
    if kind == "group_announcement":
        return _announcement_fields(node)[2]
    if kind == "game_purchase":
        return _purchase_link(node)
    if kind == "screenshot":
        screenshot = node.select_one(".modalContentLink.ugc[href]")
        if screenshot:
            return urljoin("https://steamcommunity.com", screenshot["href"])
    preferred = node.select_one(
        ".blotter_group_announcement_headline a, .blotter_gamepurchase_text a, "
        ".blotter_gamepurchase_details a, a[href*='/status/'], a[href*='/app/'], "
        ".blotter_screenshot_title a"
    )
    if not preferred:
        preferred = node.select_one("a[href]")
    return urljoin("https://steamcommunity.com", preferred.get("href", "")) if preferred else ""


def _legacy_link(node: Tag) -> str:
    preferred = node.select_one(
        ".blotter_group_announcement_headline a, .blotter_gamepurchase_text a, "
        ".blotter_gamepurchase_details a, a[href*='/status/'], a[href*='/app/'], "
        ".blotter_screenshot_title a"
    )
    if not preferred:
        preferred = node.select_one("a[href]")
    return urljoin("https://steamcommunity.com", preferred.get("href", "")) if preferred else ""


def _short(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _title(*parts: str) -> str:
    values = [part.strip() for part in parts if part.strip()]
    return _short(" · ".join(values), MAX_NOTIFICATION_TITLE_LENGTH)


def _app_anchors(node: Tag) -> list[Tag]:
    return [
        anchor
        for anchor in node.select("a[href*='/app/']")
        if _text(anchor)
    ]


def _purchase_game(node: Tag) -> tuple[str, str]:
    anchors = [
        anchor
        for anchor in node.select(
            ".blotter_author_block a[href*='store.steampowered.com/'], "
            ".blotter_gamepurchase_logo[href*='store.steampowered.com/'], "
            ".blotter_gamepurchase_details a[href*='store.steampowered.com/']"
        )
        if _text(anchor)
    ]
    if not anchors:
        anchors = _app_anchors(node)
    if not anchors:
        return "", ""
    return _text(anchors[0]), urljoin("https://steamcommunity.com", anchors[0]["href"])


def _purchase_link(node: Tag) -> str:
    store = node.select_one(
        ".blotter_author_block a[href*='store.steampowered.com/'], "
        ".blotter_gamepurchase_logo[href*='store.steampowered.com/'], "
        ".blotter_gamepurchase_details a[href*='store.steampowered.com/']"
    )
    if store:
        return urljoin("https://steamcommunity.com", store["href"])
    _, app_link = _purchase_game(node)
    return app_link


def _announcement_fields(node: Tag) -> tuple[str, str, str]:
    game_anchor = node.select_one(".blotter_group_announcement_header_text a[href]")
    headline_anchor = node.select_one(".blotter_group_announcement_headline a[href]")
    game = _text(game_anchor) if game_anchor else ""
    headline = _text(headline_anchor) if headline_anchor else ""
    link = (
        urljoin("https://steamcommunity.com", headline_anchor["href"])
        if headline_anchor
        else ""
    )
    return game, headline, link


def _structured_fields(node: Tag, kind: str, actor: str) -> tuple[str, str, str]:
    if kind == "rollup_played":
        apps = _app_anchors(node)
        game = _text(apps[0]) if apps else ""
        text = _text(node)
        if actor:
            text = re.sub(rf"^{re.escape(actor)}\s+", "", text)
        if game:
            text = text.replace(game, "", 1)
        body = re.sub(r"\s+", " ", text).strip()
        body = body[:1].upper() + body[1:] if body else "Played."
        return _title(actor, game), body, ""

    if kind == "rollup_wishlist":
        games = [_text(anchor) for anchor in _app_anchors(node)]
        return _title(actor, "Wishlist"), ", ".join(games), ""

    if kind == "rollup_achievement":
        apps = _app_anchors(node)
        game = _text(apps[0]) if apps else ""
        achievements = [
            re.sub(r"\s+", " ", image["title"]).strip()
            for image in node.select("img[title]")
            if image.get("title", "").strip()
        ]
        return _title(actor, game), "; ".join(achievements), ""

    if kind == "game_purchase":
        game, app_link = _purchase_game(node)
        description_node = node.select_one(".blotter_gamepurchase_text")
        description = _text(description_node) if description_node else ""
        body = "Now owns it."
        if description:
            body += f" {_short(description, 200)}"
        return _title(actor, game), body, _purchase_link(node) or app_link

    if kind == "group_announcement":
        game, headline, announcement_link = _announcement_fields(node)
        content = node.select_one(".group_announcement_auto_collapse")
        excerpt = _text(content) if content else ""
        body = headline
        if excerpt:
            body += f" {excerpt}"
        return _title(game, "Announcement"), _short(body, MAX_ANNOUNCEMENT_BODY_LENGTH), announcement_link

    if kind == "screenshot":
        game_anchors = [
            anchor
            for anchor in node.select(".blotter_author_block a[href*='/app/']")
            if _text(anchor)
        ]
        game = _text(game_anchors[0]) if game_anchors else ""
        match = re.search(
            r"\buploaded\s+(\d+)\s+screenshots?\b", _text(node), re.IGNORECASE
        )
        count = match.group(1) if match else ""
        body = f"Uploaded {count} screenshots." if count else "Uploaded screenshots."
        screenshot = node.select_one(".modalContentLink.ugc[href]")
        link = (
            urljoin("https://steamcommunity.com", screenshot["href"])
            if screenshot
            else ""
        )
        return _title(actor, game), body, link

    return _title(actor or "Activity"), "", ""


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


def _summary(node: Tag, kind: str, max_length: int = MAX_SUMMARY_LENGTH) -> str:
    clean = deepcopy(node)
    for selector in _INTERACTION_SELECTORS:
        for element in clean.select(selector):
            element.decompose()
    if kind == "group_announcement":
        content = clean.select_one(".blotter_group_announcement_content")
        summary = _text(content or clean)
    else:
        summary = _text(clean)
    if kind == "rollup_achievement":
        titles = [
            re.sub(r"\s+", " ", image["title"]).strip()
            for image in node.select("img[title]")
            if image.get("title", "").strip()
        ]
        additions = [title for title in titles if title not in summary]
        if additions:
            summary = f"{summary} {'; '.join(additions)}"
    if len(summary) <= max_length:
        return summary
    return summary[: max_length - 1].rstrip() + "…"


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
            legacy_summary = _summary(node, kind, LEGACY_SUMMARY_LENGTH)
            legacy_link = _legacy_link(node)
            if not legacy_summary:
                continue
            actor, profile = _actor(node)
            title, structured_summary, structured_link = _structured_fields(node, kind, actor)
            summary = structured_summary or legacy_summary
            if not structured_summary:
                title = _title(actor or "Activity")
            link = structured_link or _link(node, kind)
            normalized = f"{timestamp}|{legacy_summary}|{legacy_link}"
            event_id = node.get("id") or hashlib.sha256(normalized.encode()).hexdigest()[:24]
            events.append(
                Event(
                    event_id,
                    kind,
                    actor,
                    profile,
                    summary,
                    link,
                    timestamp,
                    title,
                )
            )
    return events
