"""Request builder for OpenAI Codex via the local openai-oauth proxy.

The proxy at ``http://localhost:10531/v1`` speaks standard OpenAI
``/chat/completions`` and handles auth, model shaping, and any provider quirks
itself. This builder therefore performs the minimal Anthropic->OpenAI body
conversion and passes the result straight through with no provider-specific
sanitization.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from core.anthropic import ReasoningReplayMode, build_base_request_body
from core.anthropic.conversion import OpenAIConversionError
from providers.exceptions import InvalidRequestError


# Anthropic effort → OpenAI reasoning_effort (GPT-5.5/ChatGPT).
# "max" is rejected with "400 unsupported effort"; clamp to xhigh.
_EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "xhigh",
}


def _resolve_reasoning_effort(request_data: Any, thinking_enabled: bool) -> str | None:
    """Derive ``reasoning_effort`` from the original Anthropic request.

    Claude Code sends ``output_config.effort`` (e.g. from /effort) and/or
    ``thinking.{type,budget_tokens}``. Neither survives the anthropic→openai
    conversion, so we read them before conversion and inject the corresponding
    OpenAI parameter.
    """
    # 1) output_config.effort (Claude Code /effort level)
    output_config = getattr(request_data, "output_config", None)
    if output_config is not None:
        effort = getattr(output_config, "effort", None)
        if isinstance(effort, str) and effort:
            mapped = _EFFORT_MAP.get(effort)
            if mapped:
                return mapped

    # 2) thinking config — enabled/adaptive → high (the safe default for GPT-5.5)
    if thinking_enabled:
        return "high"

    return None


def build_request_body(request_data: Any, *, thinking_enabled: bool) -> dict:
    """Build an OpenAI-format request body from an Anthropic request.

    No provider-specific sanitization: the local proxy handles everything.
    """
    logger.debug(
        "OPENAI_CODEX_REQUEST: conversion start model={} msgs={}",
        getattr(request_data, "model", "?"),
        len(getattr(request_data, "messages", [])),
    )
    try:
        body = build_base_request_body(
            request_data,
            reasoning_replay=ReasoningReplayMode.REASONING_CONTENT
            if thinking_enabled
            else ReasoningReplayMode.DISABLED,
        )
    except OpenAIConversionError as exc:
        raise InvalidRequestError(str(exc)) from exc

    reasoning_effort = _resolve_reasoning_effort(request_data, thinking_enabled)
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
        logger.debug(
            "OPENAI_CODEX_REQUEST: reasoning_effort={}",
            reasoning_effort,
        )

    request_extra = getattr(request_data, "extra_body", None)
    if isinstance(request_extra, dict) and request_extra:
        body["extra_body"] = dict(request_extra)

    logger.debug(
        "OPENAI_CODEX_REQUEST: conversion done model={} msgs={} tools={}",
        body.get("model"),
        len(body.get("messages", [])),
        len(body.get("tools", [])),
    )
    return body
