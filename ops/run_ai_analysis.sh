#!/usr/bin/env bash
# Start the internal, read-only AI analysis API. Secrets stay in mode-600 files
# outside the repository and are never passed as command-line arguments.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
secret_dir="${EMERGENZIX_AI_SECRET_DIR:-/home/ubuntu/.config/emergenzix-ai}"
api_key_path="${secret_dir}/deepseek_api_key"
access_token_path="${secret_dir}/access_token"

if [[ ! -r "$api_key_path" || ! -r "$access_token_path" ]]; then
  printf 'AI analysis secret files are unavailable\n' >&2
  exit 1
fi

IFS= read -r EMERGENZIX_DEEPSEEK_API_KEY < "$api_key_path"
IFS= read -r EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN < "$access_token_path"
EMERGENZIX_DEEPSEEK_API_KEY="${EMERGENZIX_DEEPSEEK_API_KEY%$'\r'}"
EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN="${EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN%$'\r'}"
if [[ -z "$EMERGENZIX_DEEPSEEK_API_KEY" || -z "$EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN" ]]; then
  printf 'AI analysis secret files are empty\n' >&2
  exit 1
fi
export EMERGENZIX_DEEPSEEK_API_KEY EMERGENZIX_AI_ANALYSIS_ACCESS_TOKEN

export EMERGENZIX_DEEPSEEK_MODEL="${EMERGENZIX_DEEPSEEK_MODEL:-deepseek-flash}"
export EMERGENZIX_DB_PATH="${EMERGENZIX_DB_PATH:-/home/ubuntu/.local/share/emergenzix-ai/emergenzix.db}"

exec "${repo_root}/.venv/bin/uvicorn" emergenzix_api.main:app \
  --app-dir "${repo_root}/backend" \
  --host 127.0.0.1 \
  --port "${EMERGENZIX_AI_PORT:-18765}"
