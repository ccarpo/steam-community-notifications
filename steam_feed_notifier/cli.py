from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from .auth import (
    AuthManager,
    SteamAuthError,
    SteamAuthExpiredError,
    TokenStore,
    cookie_expiry,
    jwt_claims,
    login_via_qr,
    mint_web_cookie,
    mobile_cookie,
)
from .config import Config
from .fetcher import SteamFeed, SteamFeedError
from .notifier import NotificationError, notify, send_message
from .parser import parse_events
from .state import SeenState


def _format_expiry(value: int | str | None) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if timestamp <= 0:
        return "unknown"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


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
    login = sub.add_parser("login")
    login.add_argument("--device-name", default="steam-feed-notifier")
    login.add_argument("--timeout", type=int, default=600)
    login.add_argument("--platform", choices=("mobile", "web"), default="web")
    sub.add_parser("auth-status")
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
    session = requests.Session()
    store = TokenStore(config.auth_file)
    try:
        stored = store.load()
    except SteamAuthError:
        stored = None
    if stored and stored.get("steamid") and stored.get("refresh_token"):
        provider = AuthManager(
            store,
            session,
            on_refresh=lambda expiry: print(
                "minted new steamLoginSecure "
                f"(expires {expiry.isoformat() if expiry else 'unknown'})",
                flush=True,
            ),
        )
    elif config.steam_login_secure:
        provider = config.steam_login_secure
    else:
        raise SteamAuthError(
            "no usable authentication configured; run the login command "
            "or set steam_login_secure"
        )
    return [
        text for _, text in SteamFeed(config.profile, provider, session=session).fetch(days or 1)
    ]


def reload_config(path: str, previous: Config | None = None) -> Config:
    try:
        return Config.load(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        if previous is None:
            raise
        print(f"config reload failed: {exc}; using last good config", flush=True)
        return previous


def _send_watch_message(config: Config, title: str, body: str) -> None:
    if config.dry_run:
        print(f"{title} | {body}", flush=True)
        return
    try:
        send_message(title, body, config.apprise_urls)
    except Exception as exc:  # noqa: BLE001 - notification failures must not stop polling
        print(f"error notification failed: {type(exc).__name__}", flush=True)


def _handle_poll_error(
    config: Config,
    error: Exception,
    last_error: str | None,
) -> str:
    message = str(error)
    if config.notify_errors and message != last_error:
        body = message
        if isinstance(error, SteamAuthExpiredError):
            body += (
                "\nRe-run: docker compose run --rm steam-feed-notifier "
                "--config /config/config.yaml login"
            )
        _send_watch_message(config, "[Error] Steam feed notifier", body)
    return message


def _handle_poll_recovery(config: Config, last_error: str | None) -> None:
    if last_error is not None and config.notify_errors:
        _send_watch_message(
            config,
            "[Recovered] Steam feed notifier",
            "Polling works again.",
        )


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
    filtered_unseen = []
    for event in unseen:
        if event.kind == "rollup_achievement" and event.achievements:
            key = f"{event.day_timestamp}|{event.actor_profile or event.actor}|{event.game}"
            fresh = state.unseen_achievements(key, event.achievements)
            if not fresh:
                state.add([event.id])
                continue
            event.summary = "; ".join(fresh)
            event.achievements = fresh
        filtered_unseen.append(event)
    unseen = filtered_unseen
    if is_first_run and not first_run_notify:
        state.add([e.id for e in events])
        for event in events:
            if event.kind == "rollup_achievement" and event.achievements:
                key = f"{event.day_timestamp}|{event.actor_profile or event.actor}|{event.game}"
                state.add_achievements(key, event.achievements)
        print(f"Seeded {len(events)} events silently.")
        return
    selected = unseen[: config.max_notifications_per_poll]
    pending = selected
    def on_success(event):
        state.add([event.id])
        if event.kind == "rollup_achievement" and event.achievements:
            key = f"{event.day_timestamp}|{event.actor_profile or event.actor}|{event.game}"
            state.add_achievements(key, event.achievements)

    for attempt in range(2):
        if not pending:
            break
        try:
            notify(
                pending,
                config.apprise_urls,
                config.dry_run,
                on_success=on_success,
                prefixes=config.title_prefixes,
            )
        except NotificationError as exc:
            pending = exc.failed_events
            if attempt == 1:
                state.add([event.id for event in pending])
                print(
                    f"Giving up on {len(pending)} notification(s) after 2 attempts; "
                    "marked them seen.",
                    flush=True,
                )
        else:
            pending = []
    print(f"Processed {len(selected)} new events ({len(events)} fetched).")


def main() -> None:
    args = _parser().parse_args()
    config = reload_config(args.config)
    if args.command == "login":
        if args.platform == "mobile":
            print(
                "Warning: MobileApp login makes this server look like a new Android Steam device; "
                "Steam may flag it as account theft and restrict your account. Prefer the default "
                "web login.",
                file=sys.stderr,
                flush=True,
            )

        def show_challenge(url: str) -> None:
            print(f"Scan this QR code in the Steam mobile app:\n{url}", flush=True)
            try:
                import qrcode

                qr = qrcode.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                for row in qr.get_matrix():
                    print("".join("██" if cell else "  " for cell in row))
            except (ImportError, OSError, ValueError):
                print("QR rendering is unavailable; use the URL above.", flush=True)

        session = requests.Session()
        steamid, refresh_token, access_token = login_via_qr(
            session,
            args.device_name,
            show_challenge,
            timeout=args.timeout,
            platform=args.platform,
        )
        store = TokenStore(config.auth_file)
        if args.platform == "mobile":
            if not access_token:
                raise SteamAuthError("Steam did not return an access token")
            cookie = mobile_cookie(steamid, access_token)
        else:
            store.save(
                {
                    "platform": "web",
                    "steamid": steamid,
                    "refresh_token": refresh_token,
                }
            )
            cookie, rotated_refresh = mint_web_cookie(session, refresh_token, steamid)
            if rotated_refresh:
                refresh_token = rotated_refresh
        store.save(
            {
                "platform": args.platform,
                "steamid": steamid,
                "refresh_token": refresh_token,
                "cookie": cookie,
                "cookie_expiry": cookie_expiry(cookie),
            }
        )
        refresh_expiry = jwt_claims(refresh_token).get("exp")
        print(f"Logged in SteamID: {steamid}")
        print(f"Platform: {args.platform}")
        print(
            "Login verified: fetched a steamLoginSecure cookie "
            f"(expires {_format_expiry(cookie_expiry(cookie))})"
        )
        print(f"Refresh-token expiry: {_format_expiry(refresh_expiry)}")
        return
    if args.command == "auth-status":
        manager = AuthManager(TokenStore(config.auth_file), requests.Session())
        status = manager.status()
        print(f"SteamID: {status['steamid']}")
        print(f"Platform: {status['platform']}")
        print(f"Cookie expiry: {_format_expiry(status['cookie_expiry'])}")
        print(f"Refresh-token expiry: {_format_expiry(status['refresh_expiry'])}")
        return
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
    last_error: str | None = None
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
            _handle_poll_recovery(config, last_error)
            last_error = None
            delay = config.poll_interval
            first_run_notify = False
        except SteamAuthExpiredError as exc:
            print(f"poll failed: authentication expired; {exc}; run login again", flush=True)
            last_error = _handle_poll_error(config, exc, last_error)
            delay = min(delay * 2, 3600)
        except (SteamFeedError, OSError, RuntimeError) as exc:
            print(f"poll failed: {exc}", flush=True)
            last_error = _handle_poll_error(config, exc, last_error)
            delay = min(delay * 2, 3600)
        time.sleep(delay + random.uniform(0, min(30, delay * 0.1)))


if __name__ == "__main__":
    main()
