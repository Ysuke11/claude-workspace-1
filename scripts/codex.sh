#!/usr/bin/env bash
# Claude Code から OpenAI Codex CLI を呼び出すためのラッパー。
#
#   scripts/codex.sh setup                     Codex CLI のインストールとログイン確認
#   scripts/codex.sh review [--base BRANCH | --uncommitted | --commit SHA] [観点...]
#                                              コード差分を Codex にレビューさせる
#   scripts/codex.sh check FILE [観点...]       資料・ドキュメントのファクトチェック/レビュー
#   scripts/codex.sh ask "質問"                 読み取り専用でセカンドオピニオンを聞く
#   scripts/codex.sh rescue "指示"              Codex に作業ディレクトリ内の修正を任せる
#
# 結果は標準出力と .codex-reports/ 配下のファイルに保存される。
# 認証は環境変数 OPENAI_API_KEY（または CODEX_API_KEY）を使う。
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPORT_DIR="$REPO_ROOT/.codex-reports"
CODEX_MODEL="${CODEX_MODEL:-}"

die() { echo "codex.sh: $*" >&2; exit 1; }

ensure_installed() {
  if ! command -v codex >/dev/null 2>&1; then
    echo "Codex CLI をインストールしています..." >&2
    npm install -g @openai/codex >/dev/null 2>&1 || die "npm install -g @openai/codex に失敗しました"
  fi
}

ensure_login() {
  if codex login status >/dev/null 2>&1; then
    return
  fi
  local key="${OPENAI_API_KEY:-${CODEX_API_KEY:-}}"
  [ -n "$key" ] || die "OPENAI_API_KEY が設定されていません。クラウド環境の設定で環境変数として追加してください。"
  printf '%s' "$key" | codex login --with-api-key >/dev/null || die "codex login に失敗しました"
}

model_args() {
  if [ -n "$CODEX_MODEL" ]; then
    printf '%s\n' -m "$CODEX_MODEL"
  fi
}

report_path() {
  mkdir -p "$REPORT_DIR"
  echo "$REPORT_DIR/$(date +%Y%m%d-%H%M%S)-$1.md"
}

run_exec() {
  # $1: sandbox, $2: report name, rest: prompt
  local sandbox="$1" name="$2"; shift 2
  local out; out="$(report_path "$name")"
  local -a margs; mapfile -t margs < <(model_args)
  codex exec "${margs[@]}" --sandbox "$sandbox" --ephemeral --color never \
    -C "$REPO_ROOT" -o "$out" "$*" >&2
  cat "$out"
  echo >&2
  echo "レポート: ${out#"$REPO_ROOT"/}" >&2
}

cmd_review() {
  # codex exec review は対象指定とカスタム指示を併用できないため、
  # 差分の取り方をプロンプトで伝えて codex exec に読ませる。
  local target="" focus=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --base) target="\`git diff ${2:?--base にはブランチが必要です}...HEAD\` の差分"; shift 2 ;;
      --commit) target="\`git show ${2:?--commit には SHA が必要です}\` の差分"; shift 2 ;;
      --uncommitted) target="未コミットの変更（\`git diff HEAD\` と \`git status --porcelain\` に出る未追跡ファイル）"; shift ;;
      *) focus="$focus $1"; shift ;;
    esac
  done
  if [ -z "$target" ]; then
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
      target="未コミットの変更（\`git diff HEAD\` と \`git status --porcelain\` に出る未追跡ファイル）"
    else
      target="\`git diff $(default_branch)...HEAD\` の差分"
    fi
  fi
  local prompt="あなたはコードレビュアーです。$target をレビューしてください。ファイルは編集しないでください。
バグ・セキュリティ・アクセシビリティ・保守性の観点で、指摘は重大度順（高/中/低）に、ファイル名と行番号・理由・修正案を付けて日本語で書いてください。問題がなければそう明記してください。"
  [ -n "$focus" ] && prompt="$prompt
特に次の観点を重視:$focus"
  run_exec read-only review "$prompt"
}

cmd_check() {
  local file="${1:?check にはファイルパスが必要です}"; shift
  [ -f "$file" ] || die "ファイルが見つかりません: $file"
  local focus="${*:-事実誤り・根拠の弱い主張・古い情報・論理の飛躍・抜け漏れ}"
  run_exec read-only check "あなたはレビュアーです。ファイル '$file' を読み、次の観点でレビューしてください: $focus。
ファイルは編集しないでください。出力は日本語で、
1. 重大な問題（事実誤り等）
2. 改善提案
3. 問題なしと確認できた点
の3節に分け、各指摘には該当箇所の引用と修正案を付けてください。"
}

default_branch() {
  local b
  b="$(git -C "$REPO_ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -z "$b" ]; then
    git -C "$REPO_ROOT" remote set-head origin --auto >/dev/null 2>&1 || true
    b="$(git -C "$REPO_ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  fi
  echo "${b:-origin/main}"
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    setup)
      ensure_installed
      ensure_login
      codex --version
      codex login status
      ;;
    review) ensure_installed; ensure_login; cmd_review "$@" ;;
    check)  ensure_installed; ensure_login; cmd_check "$@" ;;
    ask)    ensure_installed; ensure_login; run_exec read-only ask "${*:?ask には質問が必要です}" ;;
    rescue) ensure_installed; ensure_login; run_exec workspace-write rescue "${*:?rescue には指示が必要です}" ;;
    *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
  esac
}

main "$@"
