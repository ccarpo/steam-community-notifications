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
        assert len(event.summary) <= 1000
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
