#!/usr/bin/env bash
set -euo pipefail

runtime="${BRLA_SB3_RUNTIME:-/home/ubuntu/emergenzix-training-runs/sb3-2.7.1}"
python_bin="${BRLA_PYTHON:-/home/ubuntu/emergenzix-chronos-v2.3.2/.venv/bin/python}"
proxy="${BRLA_HTTP_PROXY:-http://127.0.0.1:7890}"

test -d "$runtime"
test -x "$python_bin"

export PYTHONPATH="$runtime${PYTHONPATH:+:$PYTHONPATH}"
export HTTP_PROXY="$proxy"
export HTTPS_PROXY="$proxy"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"

exec "$python_bin" "$@"
