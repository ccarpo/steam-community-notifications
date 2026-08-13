# Steam Feed Notifier

This small Python service polls Steam's logged-in home activity feed and sends
new friend activity as phone notifications. Steam does not expose this feed in
the official Web API, so the tool uses the internal endpoint
`/ajaxgetusernews/` with the account's `steamLoginSecure` session cookie. It is
read-only: links open Steam so replies and comments still happen there.

## Setup

```sh
python3.10 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp config.example.yaml config.yaml
# edit config.yaml; config.yaml is ignored by git
steam-feed-notifier --config config.yaml once
```

To get the cookie in Chrome: open Steam Community while logged in, press
DevTools (`F12`), choose **Application → Cookies → https://steamcommunity.com**,
copy the `steamLoginSecure` value, and paste it into `config.yaml`. It is a
live session token, not a permanent API key; it will expire or be revoked and
must then be refreshed. Prefer `STEAM_LOGIN_SECURE` in the environment when
possible. Never commit or log it.

The included `ntfy` example is a convenient phone target: install the ntfy
app, subscribe to a private topic, and set
`ntfy://ntfy.sh/your-private-topic` in `apprise_urls`. Other Apprise URLs
support Telegram, Pushover, Discord, and many more.

The first run seeds existing activity silently. Use `--notify-first-run` if
backlog notifications are desired. Normal polling uses only the current day;
`seed_days` controls the small number of older days fetched only during the
first-run seed.

## Commands

```sh
steam-feed-notifier --config config.yaml once
steam-feed-notifier --config config.yaml --dry-run once
steam-feed-notifier --config config.yaml watch
steam-feed-notifier --config config.yaml debug --fixture-dir tests/fixtures
```

`watch` uses a minutes-scale interval, jitter, and exponential backoff for
transient failures. Empty HTTP-200 bodies are treated as logged out and report
“cookie expired, grab a fresh steamLoginSecure”.

## Continuous operation

Example systemd user unit (`~/.config/systemd/user/steam-feed-notifier.service`):

```ini
[Unit]
Description=Steam friend activity notifications
[Service]
WorkingDirectory=/path/to/steam-feed-notifier
ExecStart=/path/to/steam-feed-notifier/.venv/bin/steam-feed-notifier --config /path/to/config.yaml watch
Restart=on-failure
[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now steam-feed-notifier
```

## Docker Compose

Create the host-mounted configuration and state directory:

```sh
cp config.example.yaml config.yaml
mkdir -p state
# edit config.yaml and add the steamLoginSecure cookie
docker compose up -d --build
```

The repository's `docker-compose.yml` mounts `config.yaml` read-only at
`/config/config.yaml`, runs `watch`, and persists the seen-event state in the
host `./state` directory (mounted at `/state` in the container). The compose
environment override makes the state path `/state/seen.json`, so container
restarts do not re-seed or re-notify old activity.
Compose runs with your host UID/GID by default so a private (`0600`) mounted
`config.yaml` remains readable without running as root. Set `UID` and `GID`
explicitly if your host account uses different IDs.

The watch loop reloads the mounted YAML at the start of every poll. To replace
an expired cookie, edit `config.yaml`; the next poll uses the new
`steamLoginSecure` without restarting the container. If the file is
temporarily malformed while an editor is saving it, the service logs the
reload error and keeps using the last valid configuration. You can also pass
the cookie through `STEAM_LOGIN_SECURE` in the environment; Compose passes that
variable through when set, and it takes precedence over the YAML value.

```sh
docker compose logs -f steam-feed-notifier
docker compose down
docker compose up -d
```

Do not commit `config.yaml` or the `state/` directory. The image runs as a
non-root user and does not include tests, fixtures, caches, or local config.
