"""Native Anthropic provider: serve a tier from real Claude via subscription OAuth."""

from __future__ import annotations

import os
import re
from typing import Any

from core.anthropic.native_messages_request import dump_raw_messages_request
from providers.anthropic_messages import AnthropicMessagesTransport
from providers.base import ProviderConfig

from .client_headers import get_forwarded_client_headers
from .oauth import get_access_token

# Anthropic Messages API root; the transport appends "/messages".
ANTHROPIC_NATIVE_DEFAULT_BASE = "https://api.anthropic.com/v1"

# Beta required for OAuth-subscription bearer tokens to be accepted.
OAUTH_BETA = "oauth-2025-04-20"


def _detected_cli_version() -> str:
    """Best-effort Claude Code version for the fallback User-Agent."""
    agent = os.environ.get("AI_AGENT", "")
    match = re.search(r"(\d+)[._-](\d+)[._-](\d+)", agent)
    if match:
        return ".".join(match.groups())
    return "2.1.161"


_FALLBACK_USER_AGENT = f"claude-cli/{_detected_cli_version()} (external, cli)"


class AnthropicNativeProvider(AnthropicMessagesTransport):
    """Talk to ``https://api.anthropic.com/v1`` as the official Claude Code client.

    Auth comes from the local Claude subscription OAuth token (not a config key),
    and request headers mirror the genuine client so the call is indistinguishable
    from Claude Code talking to Anthropic directly.
    """

    stream_chunk_mode = "event"

    def __init__(self, config: ProviderConfig):
        super().__init__(
            config,
            provider_name="ANTHROPIC",
            default_base_url=ANTHROPIC_NATIVE_DEFAULT_BASE,
        )

    def _build_request_body(
        self, request: Any, thinking_enabled: bool | None = None
    ) -> dict:
        """Faithful native passthrough — the upstream IS Anthropic.

        Unlike the shared base builder (written for deepseek/openrouter), we do
        NOT rewrite ``thinking`` into ``{type: enabled}`` (that drops Opus 4.8's
        ``adaptive`` mode and triggers "budget_tokens: Field required"). We only
        normalise ``thinking`` to the exact schema the real API accepts and pass
        every other field (tools, system, context_management, output_config,
        metadata, signed thinking blocks) through untouched.
        """
        body = dump_raw_messages_request(request)
        body.pop("extra_body", None)
        if "thinking" in body:
            normalized = self._normalize_thinking(body.get("thinking"))
            if normalized is None:
                body.pop("thinking", None)
            else:
                body["thinking"] = normalized
        if body.get("max_tokens") is None:
            body.pop("max_tokens", None)
        return body

    @staticmethod
    def _normalize_thinking(thinking: Any) -> dict | None:
        """Coerce a (pydantic-dumped) thinking config to the real API schema.

        adaptive -> {"type":"adaptive"} (no extra keys allowed);
        enabled  -> {"type":"enabled","budget_tokens": N} (budget required);
        disabled -> {"type":"disabled"}.
        """
        if not isinstance(thinking, dict):
            return None
        ttype = thinking.get("type")
        if ttype == "adaptive":
            return {"type": "adaptive"}
        if ttype == "disabled":
            return {"type": "disabled"}
        if ttype == "enabled":
            budget = thinking.get("budget_tokens")
            if not isinstance(budget, int) or budget <= 0:
                budget = 16000
            return {"type": "enabled", "budget_tokens": budget}
        # No/unknown type: honour a bare ``enabled`` flag as adaptive, else drop.
        if thinking.get("enabled"):
            return {"type": "adaptive"}
        return None

    def _request_headers(self) -> dict[str, str]:
        headers = get_forwarded_client_headers()
        if not headers:
            headers = {
                "user-agent": _FALLBACK_USER_AGENT,
                "x-app": "cli",
            }

        # Subscription OAuth bearer replaces any client auth.
        headers["authorization"] = f"Bearer {get_access_token()}"
        headers.pop("x-api-key", None)

        headers.setdefault("anthropic-version", "2023-06-01")
        headers["content-type"] = "application/json"
        headers["accept"] = "text/event-stream"

        # Ensure the OAuth beta flag is present (required for subscription tokens).
        existing = [b.strip() for b in headers.get("anthropic-beta", "").split(",") if b.strip()]
        if OAUTH_BETA not in existing:
            existing.insert(0, OAUTH_BETA)
        headers["anthropic-beta"] = ",".join(existing)
        return headers

    def _model_list_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {get_access_token()}"}
