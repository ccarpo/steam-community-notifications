from pathlib import Path

from bs4 import BeautifulSoup

from steam_feed_notifier.parser import parse_events

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "day0.html": (4, {"rollup_played": 1, "rollup_wishlist": 1, "rollup_achievement": 2}),
    "day1.html": (16, {"rollup_played": 3, "rollup_wishlist": 6, "rollup_achievement": 5,
                       "game_purchase": 1, "group_announcement": 1}),
    "day2.html": (18, {"rollup_played": 1, "rollup_wishlist": 6, "rollup_achievement": 8,
                       "game_purchase": 2, "group_announcement": 1}),
    "day3.html": (16, {"rollup_played": 2, "rollup_wishlist": 3, "rollup_achievement": 9,
                       "game_purchase": 2}),
    "day4.html": (29, {"rollup_played": 6, "rollup_wishlist": 6, "rollup_achievement": 14,
                       "game_purchase": 3}),
    "day5.html": (10, {"rollup_played": 2, "rollup_wishlist": 3, "rollup_achievement": 5}),
    "day6.html": (34, {"rollup_played": 5, "rollup_wishlist": 8, "rollup_achievement": 11,
                       "game_purchase": 5, "group_announcement": 4, "screenshot": 1}),
    "day7.html": (28, {"rollup_played": 3, "rollup_wishlist": 5, "rollup_achievement": 9,
                       "game_purchase": 3, "group_announcement": 8}),
}


def test_real_corpus_counts_and_kinds():
    for filename, (expected_count, expected_kinds) in EXPECTED.items():
        html = (FIXTURES / filename).read_text()
        events = parse_events(html)
        counts = {kind: sum(event.kind == kind for event in events) for kind in expected_kinds}
        assert len(events) == expected_count
        assert counts == expected_kinds


def test_real_corpus_does_not_drop_candidates():
    for path in FIXTURES.glob("day[0-7].html"):
        soup = BeautifulSoup(path.read_text(), "html.parser")
        candidate_count = sum(
            len(soup.select(f".{class_name}"))
            for class_name in (
                "blotter_daily_rollup_line",
                "blotter_gamepurchase",
                "blotter_userstatus",
                "blotter_screenshot",
            )
        )
        assert len(parse_events(path.read_text())) >= candidate_count


def test_real_corpus_exact_links_achievements_and_personas():
    events = parse_events((FIXTURES / "day0.html").read_text())
    assert events[0].actor == "Friend 001"
    assert events[0].link == "https://steamcommunity.com/app/2381590"
    assert "Underwater Adventure" in events[2].summary
    assert "( Alias " not in events[1].summary


def test_real_corpus_structured_notification_fields():
    events = [
        event
        for path in FIXTURES.glob("day[0-7].html")
        for event in parse_events(path.read_text())
    ]

    played = next(event for event in events if event.kind == "rollup_played")
    assert played.notification_title == "Friend 001 · Only Up!"
    assert played.summary == "Played for the first time."
    assert played.link == "https://steamcommunity.com/app/2381590"

    wishlist = next(event for event in events if event.kind == "rollup_wishlist")
    assert wishlist.notification_title == "Friend 002 · Wishlist"
    assert wishlist.summary == "Hole Is Mine, The Drifter"
    assert wishlist.link == "https://steamcommunity.com/app/4508020"

    achievement = next(event for event in events if event.kind == "rollup_achievement")
    assert achievement.notification_title == (
        "Friend 003 · PARKSIDE: DECAYED SOUL MANIPULATION"
    )
    assert achievement.summary == "Underwater Adventure Flood Ozz."
    assert achievement.link == "https://steamcommunity.com/app/2530250"

    purchase = next(
        event
        for event in events
        if event.kind == "game_purchase"
        and event.link.startswith("https://store.steampowered.com/app/")
    )
    assert purchase.notification_title.startswith("Friend ")
    assert purchase.summary.startswith("Now owns it.")
    assert purchase.link.startswith("https://store.steampowered.com/app/")
    bundle_purchase = next(
        event
        for event in events
        if event.kind == "game_purchase" and event.actor == "Friend 005"
    )
    assert bundle_purchase.link == "https://store.steampowered.com/sub/1746527/"
    assert "steamcommunity.com/id/" not in bundle_purchase.link

    announcement = next(
        event
        for event in events
        if event.kind == "group_announcement"
        and event.notification_title == "Dune: Awakening · Announcement"
    )
    assert announcement.notification_title == "Dune: Awakening · Announcement"
    assert announcement.summary.startswith(
        "Dune: Awakening - 1.4.10.5 Hotfix Patch Notes "
    )
    assert len(announcement.summary) <= 350
    assert "/announcements/detail/" in announcement.link

    screenshot = next(event for event in events if event.kind == "screenshot")
    assert screenshot.notification_title == "Friend 037 · Warframe"
    assert screenshot.summary == "Uploaded 3 screenshots."
    assert screenshot.link == (
        "https://steamcommunity.com/sharedfiles/filedetails/?id=3779145017"
    )
    assert all(len(event.notification_title) <= 60 for event in events)


def test_structured_notifications_keep_unknown_activity_fallback():
    html = """
    <div class="blotter_day" id="blotter_day_1700000000">
      <div class="blotter_block"><div class="unknown">Unclassified activity remains visible.</div></div>
    </div>
    """
    event = parse_events(html)[0]
    assert event.kind == "other"
    assert event.notification_title == "Activity"
    assert event.summary == "Unclassified activity remains visible."


def test_real_corpus_long_bodies_exclude_interaction_chrome():
    interaction_text = ("Rate up", "Post Comment", "View all comments", "Jethias")
    events = [
        event
        for path in FIXTURES.glob("day[0-7].html")
        for event in parse_events(path.read_text())
        if event.kind in {"group_announcement", "game_purchase"}
    ]
    assert events
    for event in events:
        assert len(event.summary) <= 400
        assert not any(text in event.summary for text in interaction_text)


def test_other_fallback_does_not_drop_unknown_activity():
    html = """
    <div class="blotter_day" id="blotter_day_1700000000">
      <div class="blotter_block"><div class="unknown">Unclassified activity remains visible.</div></div>
    </div>
    """
    events = parse_events(html)
    assert len(events) == 1
    assert events[0].kind == "other"
    assert "Unclassified activity" in events[0].summary


def test_ids_are_stable():
    html = (FIXTURES / "day0.html").read_text()
    assert [event.id for event in parse_events(html)] == [
        event.id for event in parse_events(html)
    ]
