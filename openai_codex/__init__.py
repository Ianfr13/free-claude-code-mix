"""OpenAI Codex provider package.

Routes a tier to OpenAI chat/completions through the local openai-oauth proxy
(``http://localhost:10531/v1``). The proxy reads ``auth.json`` directly, so no
real credential is required and no provider-specific request sanitization is
applied -- the body is passed through to the proxy, which handles everything.
"""

from providers.defaults import OPENAI_CODEX_DEFAULT_BASE

from .client import OpenAICodexProvider

__all__ = ["OPENAI_CODEX_DEFAULT_BASE", "OpenAICodexProvider"]
