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

Or run it in Docker:

```Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install .
CMD ["steam-feed-notifier", "--config", "/config/config.yaml", "watch"]
```

Mount a directory containing `config.yaml` and a persistent state file at
`/config`; keep the cookie and state outside the image.
