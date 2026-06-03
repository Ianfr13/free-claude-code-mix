"""OpenAI Codex provider (OpenAI-compatible chat via the local openai-oauth proxy)."""

from __future__ import annotations

from typing import Any

from providers.base import ProviderConfig
from providers.defaults import OPENAI_CODEX_DEFAULT_BASE
from providers.openai_compat import OpenAIChatTransport

from .request import build_request_body


class OpenAICodexProvider(OpenAIChatTransport):
    """OpenAI Codex via the local proxy at ``http://localhost:10531/v1``.

    Talks standard OpenAI ``/chat/completions`` to the local openai-oauth proxy,
    which injects auth from ``auth.json`` and handles any provider quirks. No
    real credential is used (a static dummy ``codex`` key keeps the OpenAI
    client happy) and no request sanitization is applied here -- the body is
    passed straight through to the proxy.
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(
            config,
            provider_name="OPENAI_CODEX",
            base_url=config.base_url or OPENAI_CODEX_DEFAULT_BASE,
            api_key=config.api_key,
        )

    def _build_request_body(
        self, request: Any, thinking_enabled: bool | None = None
    ) -> dict:
        return build_request_body(
            request,
            thinking_enabled=self._is_thinking_enabled(request, thinking_enabled),
        )
