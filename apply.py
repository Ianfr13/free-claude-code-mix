#!/usr/bin/env python3
"""Re-apply the free-claude-code 'native Anthropic passthrough' patch.

free-claude-code is installed as a uv tool; `uv tool upgrade` wipes site-packages.
Run this after any upgrade to restore:

  * a new provider `anthropic` that serves a tier from the GENUINE Anthropic API
    using the local Claude subscription OAuth token (~/.claude/.credentials.json),
    forwarding the real Claude Code client headers so it looks like the official
    client; and
  * the 3 small core edits that register it + capture incoming headers.

Idempotent: running it again when already patched makes no changes.

Usage:  python3 /root/fcc-patch/apply.py
"""

from __future__ import annotations

import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_site_packages() -> str:
    candidates = glob.glob(
        "/root/.local/share/uv/tools/free-claude-code/lib/python3.*/site-packages"
    )
    if not candidates:
        sys.exit("ERROR: free-claude-code site-packages not found (is it installed?)")
    return sorted(candidates)[-1]


# (relative path, sentinel that means 'already applied', find-string, replacement)
EDITS = [
    (
        "config/provider_catalog.py",
        '"anthropic": ProviderDescriptor(',
        'PROVIDER_CATALOG: dict[str, ProviderDescriptor] = {\n    "nvidia_nim": ProviderDescriptor(',
        'ANTHROPIC_NATIVE_DEFAULT_BASE = "https://api.anthropic.com/v1"\n\n'
        'PROVIDER_CATALOG: dict[str, ProviderDescriptor] = {\n'
        '    "anthropic": ProviderDescriptor(\n'
        '        provider_id="anthropic",\n'
        '        transport_type="anthropic_messages",\n'
        "        default_base_url=ANTHROPIC_NATIVE_DEFAULT_BASE,\n"
        '        capabilities=("chat", "streaming", "tools", "thinking", "native_anthropic"),\n'
        "    ),\n"
        '    "nvidia_nim": ProviderDescriptor(',
    ),
    (
        "providers/registry.py",
        "def _create_anthropic(",
        "def _create_nvidia_nim(config: ProviderConfig, settings: Settings) -> BaseProvider:",
        "def _create_anthropic(config: ProviderConfig, _settings: Settings) -> BaseProvider:\n"
        "    from providers.anthropic_native import AnthropicNativeProvider\n\n"
        "    return AnthropicNativeProvider(config)\n\n\n"
        "def _create_nvidia_nim(config: ProviderConfig, settings: Settings) -> BaseProvider:",
    ),
    (
        "providers/registry.py",
        '"anthropic": _create_anthropic,',
        'PROVIDER_FACTORIES: dict[str, ProviderFactory] = {\n    "nvidia_nim": _create_nvidia_nim,',
        'PROVIDER_FACTORIES: dict[str, ProviderFactory] = {\n'
        '    "anthropic": _create_anthropic,\n'
        '    "nvidia_nim": _create_nvidia_nim,',
    ),
    (
        "api/routes.py",
        "from providers.anthropic_native.client_headers import set_incoming_headers",
        "from .services import ClaudeProxyService\n\nrouter = APIRouter()",
        "from .services import ClaudeProxyService\n\n"
        "try:\n"
        "    from providers.anthropic_native.client_headers import set_incoming_headers\n"
        "except Exception:  # pragma: no cover - patch is optional\n"
        "    def set_incoming_headers(_headers) -> None:  # type: ignore[misc]\n"
        "        return None\n\n"
        "router = APIRouter()",
    ),
    (
        "api/routes.py",
        "set_incoming_headers(request.headers)",
        '@router.post("/v1/messages")\n'
        "async def create_message(\n"
        "    request_data: MessagesRequest,\n"
        "    service: ClaudeProxyService = Depends(get_proxy_service),\n"
        "    _auth=Depends(require_api_key),\n"
        "):\n"
        '    """Create a message (always streaming)."""\n'
        "    return service.create_message(request_data)",
        '@router.post("/v1/messages")\n'
        "async def create_message(\n"
        "    request_data: MessagesRequest,\n"
        "    request: Request,\n"
        "    service: ClaudeProxyService = Depends(get_proxy_service),\n"
        "    _auth=Depends(require_api_key),\n"
        "):\n"
        '    """Create a message (always streaming)."""\n'
        "    set_incoming_headers(request.headers)\n"
        "    return service.create_message(request_data)",
    ),
]


def main() -> None:
    sp = find_site_packages()
    print(f"site-packages: {sp}")

    # 1) provider package
    dst = os.path.join(sp, "providers", "anthropic_native")
    os.makedirs(dst, exist_ok=True)
    for fn in ("__init__.py", "client.py", "client_headers.py", "oauth.py"):
        shutil.copy2(os.path.join(HERE, "anthropic_native", fn), os.path.join(dst, fn))
    print(f"provider package copied -> {dst}")

    # 2) core edits (idempotent)
    for rel, sentinel, find, repl in EDITS:
        path = os.path.join(sp, rel)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if sentinel in content:
            print(f"[skip] {rel}: already patched")
            continue
        if find not in content:
            print(f"[WARN] {rel}: anchor not found (upstream changed?) — patch manually")
            continue
        content = content.replace(find, repl, 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"[edit] {rel}: patched")

    # 3) sanity import
    sys.path.insert(0, sp)
    import importlib

    reg = importlib.import_module("providers.registry")
    ok = "anthropic" in reg.PROVIDER_FACTORIES
    print(f"verify: anthropic registered = {ok}")
    print("DONE" if ok else "FAILED")


if __name__ == "__main__":
    main()
