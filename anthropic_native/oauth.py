"""Load (and transparently refresh) the local Claude Code OAuth subscription token.

The genuine Anthropic API accepts the Claude Max/Pro subscription via an OAuth
bearer token. Claude Code stores it in ``~/.claude/.credentials.json`` under
``claudeAiOauth`` with ``accessToken`` / ``refreshToken`` / ``expiresAt`` (ms).

We read it fresh per request (cheap, local file) and refresh in-process when it
is within the skew window of expiry. Refresh failures are non-fatal: we fall
back to the on-disk access token and let Anthropic reject it if truly expired.
"""

from __future__ import annotations

import json
import os
import threading
import time

import httpx
from loguru import logger

CREDENTIALS_PATH = os.environ.get(
    "ANTHROPIC_OAUTH_CREDENTIALS_PATH", "/root/.claude/.credentials.json"
)
# Public Claude Code OAuth client id + token endpoint (refresh grant).
OAUTH_CLIENT_ID = os.environ.get(
    "ANTHROPIC_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
)
OAUTH_TOKEN_URL = os.environ.get(
    "ANTHROPIC_OAUTH_TOKEN_URL", "https://console.anthropic.com/v1/oauth/token"
)
# Refresh when fewer than this many seconds remain before expiry.
REFRESH_SKEW_SECONDS = 300

_lock = threading.Lock()


class OAuthCredentialError(RuntimeError):
    """Raised when the local Claude credentials are missing or malformed."""


def _read() -> dict:
    with open(CREDENTIALS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _write(data: dict) -> None:
    tmp = f"{CREDENTIALS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, CREDENTIALS_PATH)


def _expires_at_seconds(oauth: dict) -> float:
    raw = oauth.get("expiresAt", 0) or 0
    # stored in milliseconds
    return float(raw) / 1000.0


def _refresh(oauth: dict) -> str:
    refresh_token = oauth.get("refreshToken")
    if not refresh_token:
        raise OAuthCredentialError("no refreshToken in credentials")
    resp = httpx.post(
        OAUTH_TOKEN_URL,
        json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        },
        headers={"content-type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    access = tok["access_token"]
    data = _read()
    oa = data.get("claudeAiOauth", {})
    oa["accessToken"] = access
    oa["refreshToken"] = tok.get("refresh_token", refresh_token)
    expires_in = int(tok.get("expires_in", 0))
    if expires_in:
        oa["expiresAt"] = int((time.time() + expires_in) * 1000)
    data["claudeAiOauth"] = oa
    _write(data)
    logger.info("ANTHROPIC_NATIVE: refreshed subscription OAuth token")
    return access


def get_access_token() -> str:
    """Return a usable subscription access token, refreshing if near expiry."""
    try:
        data = _read()
    except FileNotFoundError as exc:
        raise OAuthCredentialError(
            f"Claude credentials not found at {CREDENTIALS_PATH}"
        ) from exc
    oauth = data.get("claudeAiOauth")
    if not oauth or not oauth.get("accessToken"):
        raise OAuthCredentialError("claudeAiOauth.accessToken missing")

    if time.time() < _expires_at_seconds(oauth) - REFRESH_SKEW_SECONDS:
        return oauth["accessToken"]

    with _lock:
        # re-read under lock; another thread may have refreshed already
        data = _read()
        oauth = data.get("claudeAiOauth", {})
        if time.time() < _expires_at_seconds(oauth) - REFRESH_SKEW_SECONDS:
            return oauth["accessToken"]
        try:
            return _refresh(oauth)
        except Exception as exc:  # noqa: BLE001 - refresh is best-effort
            logger.warning(
                "ANTHROPIC_NATIVE: token refresh failed ({}); using on-disk token",
                exc,
            )
            return oauth.get("accessToken", "")
