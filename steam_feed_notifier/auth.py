from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests


class SteamAuthError(RuntimeError):
    pass


class SteamAuthExpiredError(SteamAuthError):
    pass


AUTHENTICATION_URL = "https://api.steampowered.com/IAuthenticationService/"
FINALIZE_LOGIN_URL = "https://login.steampowered.com/jwt/finalizelogin"
WEB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
AUTH_HEADERS = {
    "Origin": "https://steamcommunity.com",
    "Referer": "https://steamcommunity.com/",
    "User-Agent": WEB_USER_AGENT,
}
MOBILE_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "User-Agent": "okhttp/4.9.2",
    "Cookie": "mobileClient=android; mobileClientVersion=777777 3.10.3",
}


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


def _raise_for_status(response: Any) -> None:
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()


def _response_headers(response: Any) -> dict[str, Any]:
    return {key.lower(): value for key, value in response.headers.items()}


def _eresult_error(eresult: Any, context: str) -> SteamAuthError:
    try:
        value = int(eresult)
    except (TypeError, ValueError):
        return SteamAuthError(f"Steam returned an invalid {context} result")
    message = f"Steam {context} failed (eresult {value})"
    if value in (5, 15):
        return SteamAuthExpiredError(f"{message}; refresh token rejected, re-run login")
    return SteamAuthError(message)


def _post(
    session: requests.Session,
    operation: str,
    data: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
    files: dict[str, tuple[None, str]] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[dict[str, Any], Any]:
    response = session.post(
        f"{AUTHENTICATION_URL}{operation}/v1/",
        data=data or None,
        files=files,
        params=params,
        headers=headers or AUTH_HEADERS,
        timeout=30,
    )
    _raise_for_status(response)
    headers = _response_headers(response)
    raw_eresult = headers.get("x-eresult")
    if raw_eresult:
        try:
            eresult = int(raw_eresult)
        except ValueError as exc:
            raise SteamAuthError("Steam returned an invalid authentication result") from exc
        if eresult != 1:
            raise _eresult_error(eresult, "authentication")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SteamAuthError("Steam returned invalid authentication JSON") from exc
    result = payload.get("response", {}) if isinstance(payload, dict) else {}
    if not isinstance(result, dict):
        raise SteamAuthError("Steam returned an invalid authentication response")
    return result, response


def _varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_varint(field_number: int, value: int) -> bytes:
    return _varint(field_number << 3) + _varint(value)


def _protobuf_string(field_number: int, value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _varint((field_number << 3) | 2) + _varint(len(encoded)) + encoded


def _mobile_qr_request(device_name: str) -> str:
    details = (
        _protobuf_string(1, device_name)
        + _protobuf_varint(2, 3)
        + _protobuf_varint(3, -500)
        + _protobuf_varint(4, 528)
    )
    request = _varint((3 << 3) | 2) + _varint(len(details)) + details
    return base64.b64encode(request).decode("ascii")


def begin_qr_session(
    session: requests.Session,
    device_name: str,
    platform: str = "mobile",
) -> dict[str, Any]:
    if platform == "mobile":
        result, _ = _post(
            session,
            "BeginAuthSessionViaQR",
            {},
            headers=MOBILE_API_HEADERS,
            files=_multipart({"input_protobuf_encoded": _mobile_qr_request(device_name)}),
            params={"format": "json"},
        )
    elif platform == "web":
        result, _ = _post(
            session,
            "BeginAuthSessionViaQR",
            {
                "device_friendly_name": WEB_USER_AGENT,
                "platform_type": "2",
                "website_id": "Community",
            },
        )
    else:
        raise ValueError("platform must be 'mobile' or 'web'")
    required = ("client_id", "challenge_url", "request_id")
    if any(not result.get(key) for key in required):
        raise SteamAuthError("Steam did not return a usable QR login challenge")
    return result


def poll_auth_session(
    session: requests.Session,
    client_id: str,
    request_id: str,
    platform: str = "mobile",
) -> dict[str, Any]:
    if platform not in {"mobile", "web"}:
        raise ValueError("platform must be 'mobile' or 'web'")
    result, _ = _post(
        session,
        "PollAuthSessionStatus",
        {"client_id": str(client_id), "request_id": request_id},
        headers=MOBILE_API_HEADERS if platform == "mobile" else AUTH_HEADERS,
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
        headers=MOBILE_API_HEADERS,
    )
    access_token = result.get("access_token")
    if not access_token:
        raise SteamAuthError("Steam did not return an access token")
    new_refresh_token = result.get("refresh_token")
    return str(access_token), (
        str(new_refresh_token) if isinstance(new_refresh_token, str) else None
    )


def mobile_cookie(steamid: str, access_token: str) -> str:
    return quote(f"{steamid}||{access_token}", safe="")


def _cookie_headers(response: Any) -> list[str]:
    value = _response_headers(response).get("set-cookie", [])
    if isinstance(value, str):
        return [value]
    return list(value)


def _cookie_value(response: Any, name: str) -> str | None:
    pattern = re.compile(rf"(?:^|,\s*){re.escape(name)}=([^;,\s]+)")
    for header in _cookie_headers(response):
        match = pattern.search(header)
        if match:
            return match.group(1)
    return None


def _rotated_refresh_token(response: Any) -> str | None:
    value = _cookie_value(response, "steamRefresh_steam")
    if not value:
        return None
    decoded = unquote(value)
    try:
        _, token = decoded.split("||", 1)
    except ValueError as exc:
        raise SteamAuthError("Malformed refresh cookie") from exc
    return token


def _multipart(values: dict[str, str]) -> dict[str, tuple[None, str]]:
    return {key: (None, value) for key, value in values.items()}


def mint_web_cookie(
    session: requests.Session,
    refresh_token: str,
    steamid: str,
) -> tuple[str, str | None]:
    sessionid = secrets.token_hex(12)
    response = session.post(
        FINALIZE_LOGIN_URL,
        files=_multipart(
            {
                "nonce": refresh_token,
                "sessionid": sessionid,
                "redir": "https://steamcommunity.com/login/home/?goto=",
            }
        ),
        headers=AUTH_HEADERS,
        timeout=30,
    )
    _raise_for_status(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise SteamAuthError("Steam returned invalid login JSON") from exc
    if isinstance(payload, dict) and payload.get("error") is not None:
        error = int(payload["error"])
        if error == 8:
            raise SteamAuthExpiredError(
                "Steam refresh token rejected (eresult 8); re-run login"
            )
        raise _eresult_error(error, "login")
    transfer_info = payload.get("transfer_info") if isinstance(payload, dict) else None
    if not transfer_info:
        raise SteamAuthError("Malformed login response")
    rotated_refresh_token = _rotated_refresh_token(response)
    transfers = list(transfer_info)
    steam_transfers = [
        entry
        for entry in transfers
        if urlparse(str(entry.get("url", ""))).hostname
        and urlparse(str(entry["url"])).hostname.endswith("steamcommunity.com")
    ]
    candidates = steam_transfers + [entry for entry in transfers if entry not in steam_transfers]
    last_error: SteamAuthError | None = None
    for entry in candidates:
        url = str(entry.get("url", ""))
        params = entry.get("params", {})
        if not url or not isinstance(params, dict):
            last_error = SteamAuthError("Malformed login transfer")
            continue
        form = {
            "steamID": str(steamid),
            **{str(key): str(value) for key, value in params.items()},
        }
        for attempt in range(5):
            try:
                transfer_response = session.post(
                    url,
                    files=_multipart(form),
                    headers=AUTH_HEADERS,
                    timeout=30,
                )
                _raise_for_status(transfer_response)
                try:
                    transfer_payload = transfer_response.json()
                except ValueError as exc:
                    raise SteamAuthError("Steam returned invalid transfer JSON") from exc
                result = (
                    transfer_payload.get("result")
                    if isinstance(transfer_payload, dict)
                    else None
                )
                if result != 1:
                    raise _eresult_error(result, "login transfer")
                cookie = _cookie_value(transfer_response, "steamLoginSecure")
                if not cookie:
                    raise SteamAuthError("Steam transfer returned no steamLoginSecure cookie")
                return cookie, rotated_refresh_token
            except (SteamAuthError, requests.RequestException) as exc:
                if isinstance(exc, SteamAuthError):
                    last_error = exc
                else:
                    last_error = SteamAuthError("Steam login transfer failed")
                if attempt < 4:
                    time.sleep(0.5)
    raise last_error or SteamAuthError("Steam login transfer failed")


def login_via_qr(
    session: requests.Session,
    device_name: str,
    on_challenge: Callable[[str], None],
    timeout: int = 180,
    platform: str = "mobile",
) -> tuple[str, str, str | None]:
    if platform not in {"mobile", "web"}:
        raise ValueError("platform must be 'mobile' or 'web'")
    challenge = begin_qr_session(session, device_name, platform)
    on_challenge(str(challenge["challenge_url"]))
    client_id = str(challenge["client_id"])
    request_id = str(challenge["request_id"])
    interval = max(1, int(float(challenge.get("interval", 5))))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = poll_auth_session(session, client_id, request_id, platform)
        if status.get("new_client_id"):
            client_id = str(status["new_client_id"])
        if status.get("new_challenge_url"):
            on_challenge(str(status["new_challenge_url"]))
        refresh_token = status.get("refresh_token")
        if refresh_token:
            access_token = status.get("access_token")
            if platform == "mobile" and not access_token:
                raise SteamAuthError("Steam did not return an access token")
            claims = jwt_claims(str(refresh_token))
            steamid = claims.get("sub")
            if not steamid:
                raise SteamAuthError("Steam refresh token has no steamid claim")
            return (
                str(steamid),
                str(refresh_token),
                str(access_token) if access_token else None,
            )
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise SteamAuthError("timed out waiting for Steam QR login approval")


def cookie_expiry(cookie: str) -> int:
    try:
        encoded_token = unquote(cookie).split("||", 1)[1]
        return int(jwt_claims(encoded_token).get("exp", 0))
    except (IndexError, SteamAuthError, TypeError, ValueError):
        return 0


class TokenStore:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise SteamAuthError(f"could not read auth store {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise SteamAuthError(f"auth store {self.path} is not a JSON object")
        return {
            key: value[key]
            for key in (
                "steamid",
                "refresh_token",
                "cookie",
                "cookie_expiry",
                "platform",
            )
            if value.get(key) is not None
        }

    def save(self, value: dict[str, Any]) -> None:
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
        on_refresh: Callable[[datetime | None], None] | None = None,
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
        return str(steamid), str(refresh_token)

    def _mint(self, force: bool = False) -> None:
        steamid, refresh_token = self._require_refresh()
        platform = str(self.tokens.get("platform", "web"))
        stored_cookie = str(self.tokens.get("cookie", ""))
        expiry = cookie_expiry(stored_cookie)
        if not force and expiry > int(time.time()) + 300:
            return
        if platform == "mobile":
            refresh_expiry = self._expiry(refresh_token)
            renew = refresh_expiry <= int(time.time()) + 60 * 24 * 60 * 60
            access_token, new_refresh_token = mint_access_token(
                self.session,
                refresh_token,
                steamid,
                renew=renew,
            )
            if new_refresh_token:
                self.tokens["refresh_token"] = new_refresh_token
                self.store.save(self.tokens)
            cookie = mobile_cookie(steamid, access_token)
        elif platform == "web":
            cookie, new_refresh_token = mint_web_cookie(
                self.session,
                refresh_token,
                steamid,
            )
            if new_refresh_token:
                self.tokens["refresh_token"] = new_refresh_token
        else:
            raise SteamAuthError("auth store has an unsupported platform; run login")
        self.tokens.setdefault("platform", platform)
        self.tokens["cookie"] = cookie
        self.tokens["cookie_expiry"] = cookie_expiry(cookie)
        self.store.save(self.tokens)
        if self.on_refresh:
            expiry = self.tokens["cookie_expiry"]
            self.on_refresh(
                datetime.fromtimestamp(expiry, tz=timezone.utc) if expiry else None
            )

    def cookie(self) -> str:
        self._mint()
        cookie = self.tokens.get("cookie")
        if not cookie:
            raise SteamAuthError("auth store has no usable steamLoginSecure cookie; run login")
        return str(cookie)

    def force_refresh(self) -> str:
        self._mint(force=True)
        return self.cookie()

    def status(self) -> dict[str, Any]:
        steamid, refresh_token = self._require_refresh()
        return {
            "steamid": steamid,
            "platform": str(self.tokens.get("platform", "web")),
            "cookie_expiry": cookie_expiry(str(self.tokens.get("cookie", ""))),
            "refresh_expiry": self._expiry(refresh_token),
        }
