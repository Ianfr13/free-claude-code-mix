# free-claude-code · native-Anthropic-passthrough patch

Adds a **native `anthropic` provider** to [`free-claude-code`](https://pypi.org/project/free-claude-code/) so a tier (typically **opus**) is served by the **genuine Anthropic API** using the local **Claude Max subscription OAuth token**, while other tiers go to cheaper providers (DeepSeek).

Result, on the **mix** instance:

| Incoming tier | Routes to |
|---|---|
| `opus` (`claude-opus-4-8`) | **Anthropic real** — your Claude Max subscription (OAuth bearer from `~/.claude/.credentials.json`), with the genuine Claude Code client headers forwarded so it looks like the official client |
| `sonnet` | `deepseek-v4-pro` (official DeepSeek API) |
| `haiku` | `deepseek-v4-flash` (official DeepSeek API) |

## Why a patch?

`free-claude-code` is a tier-router proxy but ships **no `anthropic` provider** (its purpose is to *replace* Anthropic). It's installed as a `uv tool`, so edits to its `site-packages` are wiped by `uv tool upgrade`. This repo restores the change idempotently.

## Contents

```
anthropic_native/        New provider (subclass of AnthropicMessagesTransport)
  ├── client.py            Headers (OAuth bearer + forwarded client headers) + thinking-schema fix
  ├── client_headers.py    Per-request capture of the real Claude Code headers (contextvar)
  └── oauth.py             Reads + refreshes the subscription OAuth token
apply.py                 Idempotently copies the provider + re-applies 3 core edits
systemd/fcc@.service     systemd template unit (one instance per config dir)
env-templates/           Sanitized .env examples (NO secrets)
install.sh               apply.py + install/enable systemd services
```

## Core edits applied by `apply.py`

1. `config/provider_catalog.py` — adds the `anthropic` descriptor (base `https://api.anthropic.com/v1`).
2. `providers/registry.py` — registers the `_create_anthropic` factory.
3. `api/routes.py` — captures incoming client headers so the provider can replay them upstream.

## Key gotcha (the thinking-schema fix)

Opus 4.8 sends `thinking: {type: "adaptive"}`. The shared `free-claude-code` body builder rewrote it to `{type: "enabled"}` **without** `budget_tokens`, which the genuine API rejects (`thinking.enabled.budget_tokens: Field required`). The provider's `_build_request_body` override normalizes thinking to the real schema (`adaptive` → `{type:"adaptive"}` with no extra keys; `enabled` → requires `budget_tokens`) and passes everything else (tools, system, `context_management`, `output_config`, metadata, signed thinking blocks) through untouched.

## Install

```bash
sudo ./install.sh          # runs apply.py + installs/enables systemd services
```

Or manually:

```bash
python3 apply.py                                   # patch site-packages
cp systemd/fcc@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fcc@free-claude-code-mix    # etc. per config
```

Each instance is driven by `FCC_ENV_FILE=/root/.config/<name>/.env` (see `env-templates/`).
Ports: `free-claude-code`→8082, `…-gpt`→8083, `…-flash`→8084, `…-mix`→8086.

Point Claude Code at an instance with `ANTHROPIC_BASE_URL=http://localhost:8086`.

## Caveats

- **Secrets** (`DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, the OAuth credentials) are **not** in this repo — only sanitized templates.
- **OAuth refresh**: the token (~hours) auto-refreshes via the stored refresh token; the refresh endpoint/client-id are the public Claude Code values.
- **ToS**: routing a Claude Max subscription through a proxy with spoofed official-client headers is a gray area of Anthropic's terms; the risk is on the subscription account.
