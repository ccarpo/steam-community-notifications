---
name: testing-steam-feed-notifier
description: How to run and end-to-end test the Steam activity feed notifier (live feed, apprise/ntfy pushes, Docker Compose hot-reload) without spamming a real phone.
---

# Testing steam-feed-notifier

## Running it
- Deps (`requests`, `beautifulsoup4`, `apprise`, `PyYAML`) may already be present system-wide; otherwise
  `pip install -e '.[dev]'`. Run without installing the console script via
  `python3 -m steam_feed_notifier.cli --config <cfg> [--dry-run] [--notify-first-run] [--fixture-dir DIR] {once|watch|debug}`.
- Global flags must come BEFORE the subcommand (`... --fixture-dir tests/fixtures debug`, not after) or argparse errors.
- `debug` prints parsed events as JSON (ids, kinds, actor, link) — the fastest way to know what a poll would send.
- `--fixture-dir tests/fixtures` replays committed anonymized feed HTML, so parser/notifier paths can be
  exercised offline and deterministically (incl. long group announcements and purchases).

## Config / credentials
- `cp config.example.yaml config.yaml` (gitignored). Required: `profile` (vanity id) and `steam_login_secure`.
  The cookie value may be pasted with or without the `steamLoginSecure=` prefix; the loader strips it.
- Env overrides win over YAML: `STEAM_LOGIN_SECURE`, `STEAM_FEED_STATE_FILE`.
- The cookie is a live session token. Build the config by piping the cookie file through `sed` so the value is
  never echoed, `chmod 600` the file, and only ever send it to steamcommunity.com.
- Test the "expired cookie" path by replacing the cookie value with garbage — Steam answers HTTP 200 with an
  EMPTY body when logged out, which surfaces as
  `poll failed: Steam feed is logged out: cookie expired, grab a fresh steamLoginSecure`. Never invalidate the
  real session.

## Verifying pushes without a phone
- Use ntfy with an invented random topic: `apprise_urls: ["ntfy://ntfy.sh/<random-topic>"]`; no account needed.
- Read delivery with `curl -s "https://ntfy.sh/<topic>/json?poll=1"` (one JSON line per message; gives title,
  message, length) and show it visually at `https://ntfy.sh/<topic>` in the browser.
- ntfy rejects messages whose body exceeds ~4096 bytes. Long Steam group announcements can exceed that, so a
  push may legitimately fail; check `len(message)` when a delivery failure appears.

## Controlling notification volume during tests
- First run (state file absent) seeds silently; `--notify-first-run` sends the backlog instead.
- Cap the blast radius with `max_notifications_per_poll: 2..3` and `include_kinds: [...]`.
- To simulate "new activity" without waiting for friends: delete a few ids from the state JSON — the next poll
  treats those real events as unseen and pushes them.

## Docker Compose path
- `mkdir -p state && docker compose up -d --build`; compose mounts `./config.yaml` read-only at
  `/config/config.yaml` and `./state` at `/state`, and runs as `${UID:-1000}:${GID:-1000}`.
- Hot reload: the `watch` loop re-reads the config at the top of every poll. Set `poll_interval: 15-20` for
  tests, edit `config.yaml` on the host, and watch for `config reloaded (...)` in `docker compose logs`.
  Confirm no restart happened with
  `docker inspect -f '{{.State.Pid}} {{.RestartCount}}' <container>` (PID must be unchanged) — a restart would
  also make hot reload look like it worked.
- Malformed YAML should log `config reload failed: ...; using last good config` and keep polling. Note that
  after a failed poll the loop applies exponential backoff (delay doubles up to 1h), so the next reload log can
  take minutes — wait for it instead of concluding it never happened.
- State persistence: compare `sha256sum state/seen.json` across `docker compose down && up` and assert the logs
  contain no new `Seeded ... events silently.` line.

## Known risk areas to re-check after changes
- `notify()` raises on ANY failed delivery and `run_once` only writes state afterwards, so a single failing push
  (e.g. an oversized announcement body) discards the whole batch's seen-ids and the successfully delivered
  events get pushed again on every poll. Test with a batch containing one oversized event.
- Announcement/purchase summaries are scraped from whole blocks and can include entire comment threads, so push
  bodies can be thousands of characters; check body length, not just presence.
- `max_notifications_per_poll` truncation should not lose events: run `once` repeatedly and assert the union of
  delivered events equals the unseen set with no repeats.
