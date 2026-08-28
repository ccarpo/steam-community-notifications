from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


class SteamAuthError(RuntimeError):
    pass


class SteamAuthExpiredError(SteamAuthError):
    pass


AUTHENTICATION_URL = (
    "https://api.steampowered.com/IAuthenticationService/"
)


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (
        IndexError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise SteamAuthError("invalid Steam token") from exc
    if not isinstance(claims, dict):
        raise SteamAuthError("invalid Steam token claims")
    return claims


def _post(
    session: requests.Session,
    operation: str,
    data: dict[str, str],
) -> tuple[dict[str, Any], Any]:
    response = session.post(
        f"{AUTHENTICATION_URL}{operation}/v1/",
        data=data,
        timeout=30,
    )
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    headers = {key.lower(): value for key, value in response.headers.items()}
    raw_eresult = headers.get("x-eresult")
    if raw_eresult:
        try:
            eresult = int(raw_eresult)
        except ValueError as exc:
            raise SteamAuthError("Steam returned an invalid authentication result") from exc
        if eresult not in (1,):
            message = f"Steam authentication failed (eresult {eresult})"
            if eresult in (5, 15):
                raise SteamAuthExpiredError(
                    f"{message}; refresh token rejected, re-run login"
                )
            raise SteamAuthError(message)
    try:
        payload = response.json()
    except ValueError as exc:
        raise SteamAuthError("Steam returned invalid authentication JSON") from exc
    result = payload.get("response", {}) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        raise SteamAuthError("Steam returned an invalid authentication response")
    return result, response


def begin_qr_session(session: requests.Session, device_name: str) -> dict[str, Any]:
    result, _ = _post(
        session,
        "BeginAuthSessionViaQR",
        {
            "device_friendly_name": device_name,
            "platform_type": "3",
            "website_id": "Community",
        },
    )
    required = ("client_id", "challenge_url", "request_id")
    if any(not result.get(key) for key in required):
        raise SteamAuthError("Steam did not return a usable QR login challenge")
    return result


def poll_auth_session(
    session: requests.Session,
    client_id: str,
    request_id: str,
) -> dict[str, Any]:
    result, _ = _post(
        session,
        "PollAuthSessionStatus",
        {"client_id": str(client_id), "request_id": request_id},
    )
    return result


def mint_access_token(
    session: requests.Session,
    refresh_token: str,
    steamid: str,
    renew: bool = False,
) -> tuple[str, str | None]:
    result, _ = _post(
        session,
        "GenerateAccessTokenForApp",
        {
            "refresh_token": refresh_token,
            "steamid": str(steamid),
            "renewal_type": "1" if renew else "0",
        },
    )
    access_token = result.get("access_token")
    if not access_token:
        raise SteamAuthError("Steam did not return an access token")
    new_refresh_token = result.get("refresh_token")
    return access_token, new_refresh_token if isinstance(new_refresh_token, str) else None


def login_via_qr(
    session: requests.Session,
    device_name: str,
    on_challenge: Callable[[str], None],
    timeout: int = 180,
) -> tuple[str, str, str]:
    challenge = begin_qr_session(session, device_name)
    on_challenge(str(challenge["challenge_url"]))
    client_id = str(challenge["client_id"])
    request_id = str(challenge["request_id"])
    interval = max(1, int(float(challenge.get("interval", 5))))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = poll_auth_session(session, client_id, request_id)
        if status.get("new_client_id"):
            client_id = str(status["new_client_id"])
        if status.get("new_challenge_url"):
            on_challenge(str(status["new_challenge_url"]))
        refresh_token = status.get("refresh_token")
        access_token = status.get("access_token")
        if refresh_token and access_token:
            claims = jwt_claims(str(refresh_token))
            steamid = claims.get("sub")
            if not steamid:
                raise SteamAuthError("Steam refresh token has no steamid claim")
            return str(steamid), str(refresh_token), str(access_token)
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise SteamAuthError("timed out waiting for Steam QR login approval")


class TokenStore:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise SteamAuthError(f"could not read auth store {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise SteamAuthError(f"auth store {self.path} is not a JSON object")
        return {
            key: str(value[key])
            for key in ("steamid", "refresh_token", "access_token")
            if value.get(key)
        }

    def save(self, value: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
            text=True,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(value, output)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


class AuthManager:
    def __init__(
        self,
        store: TokenStore,
        session: requests.Session,
        on_refresh: Callable[[datetime], None] | None = None,
    ):
        self.store = store
        self.session = session
        self.on_refresh = on_refresh
        self.tokens = store.load() or {}

    def __call__(self) -> str:
        return self.cookie()

    def _expiry(self, token: str) -> int:
        try:
            return int(jwt_claims(token).get("exp", 0))
        except (SteamAuthError, TypeError, ValueError):
            return 0

    def _require_refresh(self) -> tuple[str, str]:
        steamid = self.tokens.get("steamid")
        refresh_token = self.tokens.get("refresh_token")
        if not steamid or not refresh_token:
            raise SteamAuthError("no usable auth store; run the login command")
        return steamid, refresh_token

    def _mint(self, force: bool = False) -> None:
        steamid, refresh_token = self._require_refresh()
        now = int(time.time())
        access_token = self.tokens.get("access_token", "")
        access_expiry = self._expiry(access_token) if access_token else 0
        refresh_expiry = self._expiry(refresh_token)
        if not force and access_expiry > now + 300:
            return
        renew = refresh_expiry <= now + 30 * 24 * 60 * 60
        access_token, new_refresh_token = mint_access_token(
            self.session,
            refresh_token,
            steamid,
            renew=renew,
        )
        self.tokens["access_token"] = access_token
        if new_refresh_token:
            self.tokens["refresh_token"] = new_refresh_token
        self.store.save(self.tokens)
        if self.on_refresh:
            expiry = datetime.fromtimestamp(
                self._expiry(access_token), tz=timezone.utc
            )
            self.on_refresh(expiry)

    def cookie(self) -> str:
        self._mint()
        steamid, access_token = self.tokens.get("steamid"), self.tokens.get("access_token")
        if not steamid or not access_token:
            raise SteamAuthError("auth store has no usable access token; run the login command")
        return f"{steamid}%7C%7C{access_token}"

    def force_refresh(self) -> str:
        self._mint(force=True)
        return self.cookie()

    def status(self) -> dict[str, Any]:
        steamid, refresh_token = self._require_refresh()
        access_token = self.tokens.get("access_token", "")
        now = int(time.time())
        return {
            "steamid": steamid,
            "access_expiry": self._expiry(access_token),
            "refresh_expiry": self._expiry(refresh_token),
            "renewal_window": self._expiry(refresh_token) <= now + 30 * 24 * 60 * 60,
        }
