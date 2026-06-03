"""Native Anthropic passthrough provider (Claude subscription OAuth).

Routes a tier (typically opus) to the genuine Anthropic Messages API using the
local Claude Code OAuth subscription credentials, so the request is served by
real Claude while other tiers go to cheaper providers.
"""

from .client import AnthropicNativeProvider

__all__ = ["AnthropicNativeProvider"]
