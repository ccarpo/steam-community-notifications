FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY steam_feed_notifier ./steam_feed_notifier
RUN pip install .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /config /state \
    && chown app:app /config /state

VOLUME ["/config", "/state"]
USER app

ENTRYPOINT ["steam-feed-notifier"]
CMD ["--config", "/config/config.yaml", "watch"]
