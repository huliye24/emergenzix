#!/usr/bin/env bash
# Generate one bounded DeepSeek report and deliver pending report notifications.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
secret_dir="${EMERGENZIX_AI_SECRET_DIR:-/home/ubuntu/.config/emergenzix-ai}"
deepseek_key="${secret_dir}/deepseek_api_key"
telegram_key="${secret_dir}/follow_telegram_bot_token"

if [[ ! -r "$deepseek_key" || ! -r "$telegram_key" ]]; then
  printf 'AI report secret files are unavailable\n' >&2
  exit 1
fi

export EMERGENZIX_DEEPSEEK_API_KEY=""
export EMERGENZIX_DEEPSEEK_API_KEY_FILE="$deepseek_key"
export EMERGENZIX_FOLLOW_TELEGRAM_BOT_TOKEN=""
export EMERGENZIX_FOLLOW_TELEGRAM_BOT_TOKEN_FILE="$telegram_key"
export EMERGENZIX_DEEPSEEK_MODEL="${EMERGENZIX_DEEPSEEK_MODEL:-deepseek-flash}"
export EMERGENZIX_STRATEGY_EVIDENCE_PATH="${EMERGENZIX_STRATEGY_EVIDENCE_PATH:-/home/ubuntu/.local/share/emergenzix-ai/evidence/latest.json}"
export EMERGENZIX_AI_REPORT_DIR="${EMERGENZIX_AI_REPORT_DIR:-/home/ubuntu/.local/share/emergenzix-ai/reports}"
export EMERGENZIX_AI_REPORT_OUTBOX_PATH="${EMERGENZIX_AI_REPORT_OUTBOX_PATH:-/home/ubuntu/.local/share/emergenzix-ai/report-outbox.sqlite3}"

exec "${repo_root}/.venv/bin/python" -m emergenzix_api.ai_research_report
