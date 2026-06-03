"""Forward the genuine Claude Code client headers to the upstream Anthropic API.

When the user's Claude Code is pointed at this proxy it still sends its real
client-identity headers (User-Agent, x-app, anthropic-beta, x-stainless-*). We
capture them per request (set in the ``/v1/messages`` route) and replay them on
the upstream call so the request looks exactly like the official client. Auth
and hop-by-hop headers are intentionally excluded — auth is replaced with the
subscription OAuth bearer in the provider.
"""

from __future__ import annotations

import contextvars

# Lower-cased header names that identify the official client. Forwarded verbatim.
FORWARDABLE_HEADERS = frozenset(
    {
        "user-agent",
        "x-app",
        "anthropic-beta",
        "anthropic-version",
        "anthropic-dangerous-direct-browser-access",
        "x-stainless-lang",
        "x-stainless-package-version",
        "x-stainless-os",
        "x-stainless-arch",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
        "x-stainless-retry-count",
        "x-stainless-async",
        "x-stainless-timeout",
        "x-stainless-helper-method",
    }
)

_incoming: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "anthropic_native_incoming_headers", default={}
)


def set_incoming_headers(headers) -> None:
    """Record the forwardable subset of an incoming request's headers."""
    forwarded: dict[str, str] = {}
    for key, value in headers.items():
        lk = key.lower()
        if lk in FORWARDABLE_HEADERS:
            forwarded[lk] = value
    _incoming.set(forwarded)


def get_forwarded_client_headers() -> dict[str, str]:
    """Return the captured client headers for the current request (copy)."""
    return dict(_incoming.get())
