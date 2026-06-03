#!/usr/bin/env bash
# Install the free-claude-code native-Anthropic patch + systemd services.
# Idempotent: safe to re-run (e.g. after `uv tool upgrade free-claude-code`).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 1/3  Applying source patch (provider + core edits)"
python3 "$HERE/apply.py"

echo "==> 2/3  Installing systemd template unit"
cp "$HERE/systemd/fcc@.service" /etc/systemd/system/fcc@.service
systemctl daemon-reload

echo "==> 3/3  Enabling + starting instances that have a config"
for inst in free-claude-code free-claude-code-gpt free-claude-code-flash free-claude-code-mix; do
  if [ -f "/root/.config/$inst/.env" ]; then
    systemctl enable --now "fcc@$inst"
    printf '    %-26s %s / %s\n' "fcc@$inst" "$(systemctl is-active "fcc@$inst")" "$(systemctl is-enabled "fcc@$inst")"
  else
    echo "    skip fcc@$inst (no /root/.config/$inst/.env)"
  fi
done

echo "Done. Each instance restarts on failure and starts on boot."
