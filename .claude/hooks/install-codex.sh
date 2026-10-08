#!/usr/bin/env bash
# SessionStart フック: クラウドセッションで Codex CLI を用意する。
set -uo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

if ! command -v codex >/dev/null 2>&1; then
  npm install -g @openai/codex >/dev/null 2>&1 || { echo "Codex CLI のインストールに失敗しました" >&2; exit 0; }
fi

key="${OPENAI_API_KEY:-${CODEX_API_KEY:-}}"
if [ -n "$key" ] && ! codex login status >/dev/null 2>&1; then
  printf '%s' "$key" | codex login --with-api-key >/dev/null 2>&1 || true
fi
exit 0
