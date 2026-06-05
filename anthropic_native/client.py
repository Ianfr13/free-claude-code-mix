"""Native Anthropic provider: serve a tier from real Claude via subscription OAuth."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from loguru import logger

from core.anthropic.native_messages_request import dump_raw_messages_request
from providers.anthropic_messages import AnthropicMessagesTransport
from providers.base import ProviderConfig

from .client_headers import get_forwarded_client_headers
from .oauth import get_access_token

# Anthropic Messages API root; the transport appends "/messages".
ANTHROPIC_NATIVE_DEFAULT_BASE = "https://api.anthropic.com/v1"

# Beta required for OAuth-subscription bearer tokens to be accepted.
OAUTH_BETA = "oauth-2025-04-20"

# Shown in place of an Anthropic ``stop_reason: "refusal"`` (a subscription/AUP
# false-positive on the Opus path). Claude Code renders a ``refusal`` stop_reason
# as the cryptic "violate our Usage Policy" error and the session gets stuck in a
# re-trigger loop. We replace it in-stream with this clear note and finish the
# turn as ``end_turn`` so the user keeps control. The model is NOT switched.
REFUSAL_CLEAN_MESSAGE = (
    "⚠️ **Opus recusou este turno** — falso-positivo do filtro de uso (AUP) "
    "da Anthropic na via de assinatura. O conteúdo não foi processado pelo modelo "
    "(o proxy mix interceptou o erro de \"Usage Policy\" e o substituiu por esta "
    "mensagem; **o modelo não foi trocado**).\n\n"
    "Como seguir:\n"
    "• **Esc Esc** ou **/rewind** — volte para antes do turno que disparou e reformule.\n"
    "• **/clear** — comece uma sessão nova (o histórico atual continua re-disparando a recusa).\n"
    "• **/model sonnet** — rode este turno em outro provedor (DeepSeek), que não passa pelo filtro.\n"
)


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

    # -- refusal interception ------------------------------------------------
    @staticmethod
    def _sse_event_data(event: str) -> dict | None:
        """Parse the JSON payload of a grouped SSE event.

        Concatenates ALL ``data:`` lines (per the SSE spec, a consumer joins
        them with newlines) so a payload split across lines still parses — this
        matches the codebase's own ``parse_sse_lines`` and avoids a latent path
        where a multi-line refusal event would be missed and passed through.
        """
        data_lines: list[str] = []
        for line in event.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return None
        payload = "\n".join(data_lines).strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _sse_event(name: str, data: dict) -> str:
        """Render an SSE event in the same shape as upstream Anthropic events."""
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def _iter_stream_chunks(
        self, response: Any, *, state: Any, thinking_enabled: bool
    ):
        """Event-mode passthrough that turns an Anthropic ``stop_reason:"refusal"``
        into a clean, explanatory ``end_turn`` message — without switching models.

        The provider is always ``stream_chunk_mode == "event"``, so each upstream
        SSE event is forwarded whole. When the terminal ``message_delta`` carries
        ``stop_reason == "refusal"`` we close any open block, inject a fresh text
        block with :data:`REFUSAL_CLEAN_MESSAGE`, and rewrite the stop_reason to
        ``end_turn`` so Claude Code shows the note instead of the Usage-Policy
        error and does not get stuck in the refusal cascade.
        """
        next_index = 0
        open_indices: set[int] = set()
        async for event in self._iter_sse_events(response):
            data = self._sse_event_data(event)
            etype = data.get("type") if isinstance(data, dict) else None

            if etype == "content_block_start":
                idx = data.get("index")
                if isinstance(idx, int):
                    open_indices.add(idx)
                    next_index = max(next_index, idx + 1)
            elif etype == "content_block_stop":
                idx = data.get("index")
                if isinstance(idx, int):
                    open_indices.discard(idx)
            elif etype == "message_delta":
                delta = data.get("delta") if isinstance(data, dict) else None
                if isinstance(delta, dict) and delta.get("stop_reason") == "refusal":
                    logger.warning(
                        "ANTHROPIC_NATIVE: upstream stop_reason=refusal; replaced "
                        "with clean message (model unchanged)"
                    )
                    # Close any still-open blocks (Anthropic streams sequentially,
                    # but close all defensively to keep the block stack balanced).
                    for open_idx in sorted(open_indices):
                        yield self._sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": open_idx},
                        )
                    open_indices.clear()
                    idx = next_index
                    next_index += 1
                    yield self._sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                    yield self._sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {
                                "type": "text_delta",
                                "text": REFUSAL_CLEAN_MESSAGE,
                            },
                        },
                    )
                    yield self._sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    # Rewrite to a clean, self-consistent end_turn delta: drop the
                    # refusal-only stop_details so the emitted delta isn't contradictory.
                    delta["stop_reason"] = "end_turn"
                    delta.pop("stop_details", None)
                    delta["stop_sequence"] = None
                    yield self._sse_event("message_delta", data)
                    continue

            yield event

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
