from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import replace
from pathlib import Path

import yaml

from .config import Config
from .fetcher import SteamFeed, SteamFeedError
from .notifier import notify
from .parser import parse_events
from .state import SeenState


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="steam-feed-notifier")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--notify-first-run", action="store_true")
    p.add_argument("--fixture-dir", help="read day*.html fixtures instead of the live feed")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("once", "watch"):
        sub.add_parser(name)
    debug = sub.add_parser("debug")
    debug.add_argument("--html", help="read raw HTML from this file")
    debug.add_argument("--raw", action="store_true", help="include raw HTML in JSON output")
    return p


def _load_html(
    config: Config,
    fixture_dir: str | None = None,
    html: str | None = None,
    days: int | None = None,
):
    if html:
        return [Path(html).read_text()]
    if fixture_dir:
        return [p.read_text() for p in sorted(Path(fixture_dir).glob("day*.html"))]
    return [text for _, text in SteamFeed(config.profile, config.steam_login_secure).fetch(days or 1)]


def reload_config(path: str, previous: Config | None = None) -> Config:
    try:
        return Config.load(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        if previous is None:
            raise
        print(f"config reload failed: {exc}; using last good config", flush=True)
        return previous


def run_once(config: Config, first_run_notify: bool = False, fixture_dir: str | None = None) -> None:
    state = SeenState(config.state_file)
    is_first_run = not state.path.exists()
    state.load()
    htmls = _load_html(
        config,
        fixture_dir=fixture_dir,
        days=config.seed_days if is_first_run else 1,
    )
    events = [event for html in htmls for event in parse_events(html)]
    events = [e for e in events if (not config.include_kinds or e.kind in config.include_kinds)
              and e.kind not in config.exclude_kinds]
    unseen = [e for e in events if e.id not in set(state.ids)]
    if is_first_run and not first_run_notify:
        state.add([e.id for e in events])
        print(f"Seeded {len(events)} events silently.")
        return
    selected = unseen[: config.max_notifications_per_poll]
    notify(selected, config.apprise_urls, config.dry_run)
    state.add([e.id for e in selected])
    print(f"Processed {len(selected)} new events ({len(events)} fetched).")


def main() -> None:
    args = _parser().parse_args()
    config = reload_config(args.config)
    if args.command == "debug":
        htmls = _load_html(config, args.fixture_dir, args.html)
        payload = []
        for html in htmls:
            payload.extend(e.as_dict() for e in parse_events(html))
        result = {"events": payload}
        if args.raw:
            result["blotter_html"] = htmls
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if args.command == "once":
        config.dry_run = config.dry_run or args.dry_run
        run_once(config, first_run_notify=args.notify_first_run, fixture_dir=args.fixture_dir)
        return
    delay = config.poll_interval
    first_run_notify = args.notify_first_run
    loaded = config
    while True:
        previous, loaded = loaded, reload_config(args.config, loaded)
        if loaded != previous:
            print(
                f"config reloaded (poll_interval={loaded.poll_interval}, dry_run={loaded.dry_run})",
                flush=True,
            )
        config = replace(loaded, dry_run=loaded.dry_run or args.dry_run)
        try:
            run_once(config, first_run_notify=first_run_notify)
            delay = config.poll_interval
            first_run_notify = False
        except (SteamFeedError, OSError, RuntimeError) as exc:
            print(f"poll failed: {exc}", flush=True)
            delay = min(delay * 2, 3600)
        time.sleep(delay + random.uniform(0, min(30, delay * 0.1)))


if __name__ == "__main__":
    main()
