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

Authenticate once with Steam's QR flow. The default MobileApp platform uses
renewable refresh tokens:

```sh
steam-feed-notifier --config config.yaml login
```

Scan and approve the printed QR code in the Steam mobile app. If the app
refuses to load the QR, use the WebBrowser fallback:

```sh
steam-feed-notifier --config config.yaml login --platform web
```

WebBrowser refresh tokens cannot be renewed, so that login must be repeated
when the refresh token expires (around the expiry shown by `auth-status`). The
resulting `auth.json` is stored beside `state_file` (the default is
`~/.local/state/steam-feed-notifier/auth.json`). The `steamLoginSecure` cookie
is renewed automatically; MobileApp refresh tokens are rotated and persisted.
Check the platform and authentication expiries with:

```sh
steam-feed-notifier --config config.yaml auth-status
```

The QR login never prints token values. Keep the auth file private and do not
commit or log it.

Manual cookies remain available as a fallback. To get one in Chrome, open
Steam Community while logged in, press DevTools (`F12`), choose
**Application → Cookies → https://steamcommunity.com**, copy the
`steamLoginSecure` value, and put it in `config.yaml` or `STEAM_LOGIN_SECURE`.
It is a live session token, not a permanent API key; it will expire or be
revoked and must then be refreshed. Never commit or log it.

The included `ntfy` example is a convenient phone target: install the ntfy
app, subscribe to a private topic, and set
`ntfy://ntfy.sh/your-private-topic` in `apprise_urls`. Other Apprise URLs
support Telegram, Pushover, Discord, and many more.
ntfy notifications link directly to the event when tapped.

The first run seeds existing activity silently. Use `--notify-first-run` if
backlog notifications are desired. Normal polling uses only the current day;
`seed_days` controls the small number of older days fetched only during the
first-run seed.

## Commands

```sh
steam-feed-notifier --config config.yaml once
steam-feed-notifier --config config.yaml --dry-run once
steam-feed-notifier --config config.yaml watch
steam-feed-notifier --config config.yaml --fixture-dir tests/fixtures debug
steam-feed-notifier --config config.yaml login
steam-feed-notifier --config config.yaml auth-status
```

`watch` uses a minutes-scale interval, jitter, and exponential backoff for
transient failures. Empty HTTP-200 bodies are treated as logged out and report
“cookie expired, grab a fresh steamLoginSecure”. By default, distinct poll
errors and recovery are sent as notifications; set `notify_errors: false` to
disable them.

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
# edit config.yaml
docker compose up -d --build
```

The repository's `docker-compose.yml` mounts the project directory read-only at
`/config`, so the host `config.yaml` is available as
`/config/config.yaml`. Mounting the containing directory is intentional:
editors that save by renaming a new file over `config.yaml` are then visible
inside the container. The service runs `watch` and persists the seen-event
state in the host `./state` directory (mounted at `/state` in the container).
The compose environment override makes the state path `/state/seen.json`, so
the auth file defaults to `/state/auth.json`; container restarts do not re-seed
or re-notify old activity. Run the one-time login in the container with:

```sh
docker compose run --rm steam-feed-notifier --config /config/config.yaml login
```

Approve the printed QR code in the Steam mobile app. Compose runs as UID/GID
1000 by default, so the host `./state` directory must be writable by that
user for `/state/auth.json` to be created and updated. Set `UID` and `GID`
explicitly if your host account uses different IDs.
Compose runs with your host UID/GID by default so a private (`0600`) mounted
`config.yaml` remains readable without running as root. Set `UID` and `GID`
explicitly if your host account uses different IDs.

The watch loop reloads the mounted YAML at the start of every poll. To replace
an expired cookie, replace the cookie in `config.yaml`; the next poll uses the
new `steamLoginSecure` without restarting the container. If the file is
temporarily malformed while an editor is saving it, the service logs the
reload error and keeps using the last valid configuration. You can also pass
the cookie through `STEAM_LOGIN_SECURE` in the environment; Compose passes that
variable through when set, and it takes precedence over the YAML value. A
single-file bind such as `./config.yaml:/config/config.yaml:ro` is not
equivalent: Docker pins that mount to the original inode, so editor
rename-over saves may never reach the container.

If Steam rejects the stored refresh token, run `login` again. If login or
watch reports a permission error for `/state/auth.json`, make sure the mounted
`state` directory is writable by the compose UID/GID. Never expose refresh-token
or `steamLoginSecure` cookie values.

```sh
docker compose logs -f steam-feed-notifier
docker compose down
docker compose up -d
```

If an earlier `up` ran before `config.yaml` existed, Docker may have created a
directory with that name. This can produce:

```text
error mounting ".../config.yaml" to rootfs at "/config/config.yaml": ... not a directory
```

Create `config.yaml` as a regular file before the first `up`. To recover from
this error, remove the failed container and recreate it:

```sh
docker compose down
docker compose up -d --build
```

The fresh `./state` directory must be writable by the compose UID/GID; a new
`seen.json` is created there with that ownership. If a previous run as root
left a root-owned state file, repair it before starting:

```sh
sudo chown -R "$(id -u):$(id -g)" state
```

Do not commit `config.yaml` or the `state/` directory. The image runs as a
non-root user and does not include tests, fixtures, caches, or local config.
