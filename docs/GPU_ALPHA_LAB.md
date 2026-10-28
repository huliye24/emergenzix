# GPU Alpha Lab — Experiment 001 (research-only)

This on-demand PyTorch extension is isolated from the production Emergenzix
API and Telegram/PAPER workers. No Binance order credentials, internet
access, web endpoints, inference service or trading permissions are used.

The books *MXNet 神经网络与量化投资* and *Python量化交易实战入门与技巧*
motivate an experiment-oriented pipeline and an independent evaluation layer.
This first step uses **modern PyTorch**, not the 2018 MXNet/CUDA setup.
Small MLP and **causal TCN** models predict future log returns; neither is
assumed profitable or ready to trade.

## What E001 does (and does not do)

1. Read an existing, registered \`market_datasets\` OHLCV record using SQLite
   \`mode=ro\`; recompute its SHA-256 with the same verifier as the API.
2. Accept \`1h\`, \`4h\` or \`1d\` candles with contiguous UTC timestamps,
   positive OHLC and nonnegative volume. Reject broken history before training.
3. Build **past-only** log-return, log(high/low), log(close/open) and log-volume
   change features. Predict the horizon-\`h\` future log return.
4. Split candles in time order (70% train / 15% validation / 15% holdout).
   Remove train/validation examples whose future targets cross a boundary.
   Fit normalizer exclusively on the observed training feature interval.
5. Choose the best epoch by **validation loss**, then evaluate the untouched
   holdout once. Report MSE, MAE, direction accuracy, and a zero-return
   prediction baseline.
6. Write \`model_weights.pt\`, \`manifest.json\`, \`metrics.json\`,
   \`test_predictions.json\` and \`sha256.json\` into a new run directory.
   The run directory is never overwritten.

**Not implemented:** walk-forward training, profitable trading strategies,
slippage/fees/PnL, position sizing, Monte Carlo, model selection by test
results, continuous agents, PAPER orders, or LIVE trading. All of those
require separate research and acceptance gates. A good test MSE alone is
not evidence of positive risk-adjusted returns.

## A10 / A10G setup — only after the GPU machine is booted

Requires an NVIDIA A10/A10G host with a driver compatible with the pinned
CUDA 12.4 PyTorch image, NVIDIA Container Toolkit, Docker Engine and a recent
Docker Compose supporting GPU reservations. Use the existing API SQLite
named volume \`emergenzix-data\` populated by registered OHLCV datasets.

The new Compose service has:
- \`profiles: [gpu]\`: never launched by ordinary \`docker compose up\`;
- **network_mode none**, no published ports and no API/Binance keys;
- source volume mounted **read-only**, separate output volume;
- unprivileged UID 10001, read-only root filesystem and dropped Linux caps;
- an explicit \`--device cuda\` mode which **fails** if CUDA is not accessible.

From the repository root:

\`\`\`bash
# GPU must first be visible on the host:
nvidia-smi

# Build the optional standalone image (existing API image is unchanged):
docker compose --profile gpu build gpu-research

# Verify CUDA is actually exposed to PyTorch:
docker compose --profile gpu run --rm --no-deps \
  --entrypoint python gpu-research -c \
  'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
\`\`\`

Choose a registered SHA-256 dataset ID from the existing
\`POST /api/v1/datasets\` API. The model only supports contiguous, valid OHLCV;
prefer **years** of history rather than the current 90-day backtest window.

\`\`\`bash
# Each output directory must be unique. Replace DATASET_SHA256 with a real ID.
docker compose --profile gpu run --rm --no-deps gpu-research \
  --db-path /data/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --output-dir /outputs/e001-mlp-s42 \
  --model mlp --device cuda --seed 42 \
  --epochs 20 --lookback 32 --horizon 1

docker compose --profile gpu run --rm --no-deps gpu-research \
  --db-path /data/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --output-dir /outputs/e001-tcn-s42 \
  --model tcn --device cuda --seed 42 \
  --epochs 20 --lookback 32 --horizon 1
\`\`\`

The artifacts are in the named Docker volume \`emergenzix-gpu-output\`. The
code deliberately cannot write to the production SQLite database; the API
container never depends on the GPU image. Don't load untrusted PyTorch pickle
checkpoints; if loading trusted local weights, use \`weights_only=True\`.

## Independent read-only artifact verification (E001)

After a completed E001 run, inspect the outputs **before** comparing models.
The verifier uses the Python standard library: it does **not** require CUDA,
PyTorch or NumPy, and does not load the `model_weights.pt` checkpoint.
It never writes to the run directory or touches the registered dataset DB.

From a local machine that can read the experiment output directory:

```bash
cd backend
python -m emergenzix_gpu_lab.verify --run-dir /path/to/e001-mlp-s42
```

When running inside the isolated Docker volume on the GPU host:

```bash
docker compose --profile gpu run --rm --no-deps \
  --entrypoint python gpu-research \
  -m emergenzix_gpu_lab.verify --run-dir /outputs/e001-mlp-s42
```

Expected result is JSON with `"status": "verified_self_consistent"` and a
zero exit status; malformed/missing artifacts return `"status": "rejected"`
and exit nonzero. This gate checks all four saved SHA-256 digests, the
research-only flags and protocol, held-out prediction time order / horizon,
purged split timestamp ordering, and recomputed holdout / zero-return baseline
metrics. It also rejects symlinked expected artifacts.

**Limitations:** The saved SHA-256 list is not a signed attestation; someone
who can rewrite all the files can forge mutually consistent results. This
tool does not re-open the original dataset, reconstruct the original training
splits, validate the model weights, independently recompute predictions, or
prove real-world trading performance. Those checks remain in the human/Codex
A10 acceptance issue #56. Never use a positive result as permission to
promote a model to PAPER, TESTNET or LIVE execution.

## Local CPU checks — no A10 cost

Within \`backend/\` on a development machine with \`numpy\` and the
regular API development requirements installed:

\`\`\`bash
python -m pytest -q tests/test_gpu_alpha_lab.py
\`\`\`

The normal CI intentionally does **not** install PyTorch (~gigabytes);
PyTorch-dependent tests use \`pytest.importorskip\`. For a separate CPU
smoke test, install a matching CPU wheel from the official PyTorch release
channel, then run the same tests. Before trusting the A10 run, check CUDA,
container logs, data fingerprint, artifact checksums, and two independent
seeds. Be aware that bitwise cross-driver CPU/GPU repeatability is not
guaranteed, even with deterministic algorithms enabled.

## Next experiments (not merged into E001)

- Multiple chronological walk-forward folds and strong statistical baselines.
- Multiple assets, richer causal features, feature stability and label
  overlap controls.
- Trial-count correction / statistical significance, costs and slippage.
- Model experiment registry integrated with existing durable research
  task storage, but only after offline evidence passes acceptance.
