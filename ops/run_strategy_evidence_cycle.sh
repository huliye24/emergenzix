#!/usr/bin/env bash
# CPU orchestrator: calculate, transfer exact input to GPU, retrieve prediction, merge.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work_root="${EMERGENZIX_EVIDENCE_WORK_DIR:-/home/ubuntu/.local/share/emergenzix-ai/evidence-cycle}"
gpu_host="${EMERGENZIX_EVIDENCE_GPU_HOST:-emergenzix-evidence-gpu}"
ssh_key="${EMERGENZIX_EVIDENCE_SSH_KEY:-/home/ubuntu/.ssh/emergenzix-evidence-ed25519}"
gpu_repo="${EMERGENZIX_EVIDENCE_GPU_REPO:-/home/ubuntu/emergenzix-evidence-current}"
gpu_work="${EMERGENZIX_EVIDENCE_GPU_WORK_DIR:-/home/ubuntu/.local/share/emergenzix-evidence}"
python="${repo_root}/.venv/bin/python"
export PYTHONPATH="${repo_root}/backend${PYTHONPATH:+:${PYTHONPATH}}"

for timeframe in 4h 1d 1w 1M; do
  work_dir="${work_root}/${timeframe}"
  remote_work="${gpu_work}/${timeframe}"
  "$python" "${repo_root}/ops/strategy_evidence_pipeline.py" cpu \
    --repo-root "$repo_root" --work-dir "$work_dir" --timeframe "$timeframe"

  ssh -i "$ssh_key" -o BatchMode=yes "$gpu_host" "install -d -m 0700 '$remote_work'"
  scp -i "$ssh_key" -q "$work_dir/market-input.json" "$work_dir/strategy-analysis.json" \
    "${gpu_host}:${remote_work}/"
  ssh -i "$ssh_key" -o BatchMode=yes "$gpu_host" \
    "PYTHONPATH='$gpu_repo/backend' '$gpu_repo/.venv/bin/python' '$gpu_repo/ops/strategy_evidence_pipeline.py' gpu --repo-root '$gpu_repo' --work-dir '$remote_work' --timeframe '$timeframe' --chronos-cache '/home/ubuntu/emergenzix-chronos-v2.3.2/hf-cache'"
  scp -i "$ssh_key" -q "${gpu_host}:${remote_work}/model-prediction.json" "$work_dir/model-prediction.json"

  "$python" "${repo_root}/ops/strategy_evidence_pipeline.py" merge \
    --repo-root "$repo_root" --work-dir "$work_dir" --timeframe "$timeframe"
done
