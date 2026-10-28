# GPU Walk-forward batch runner — v1 (research only)

**Status: code submitted; tests and AWS/A10 runtime acceptance are pending.**
This is a sequential one-shot orchestrator on top of PRs #59–#62.
It is deliberately **NOT** a web route, Telegram command, scheduler,
trade executor, checkpoint promoter or continuous autonomous agent.

## Frozen experiment boundary

1. Reopen registered content-addressed OHLCV via the existing **read-only**
   SQLite dataset loader; reject source corruption or incomplete candles.
2. Freeze the complete expanding-history plan **before** any output or model
   training. Reject invalid fold counts, short histories or overlapping tests.
3. Create a **new** batch directory and write \`batch_plan.json\` first.
   This stores the full plan/dataset fingerprints, one model configuration,
   one seed and ascending fold execution order.
4. For each fold **sequentially**, call the existing \`train_one_fold\`
   executor with the same predetermined model/hyperparameters/seed and a
   unique \`fold-NNN\` directory. Every fold starts a fresh model. A later
   fold may incorporate already elapsed earlier periods as training history
   but never its own holdout period.
5. Immediately call \`verify_fold_run\`, which independently reconstructs
   the plan and train-only normalizer and checks reported holdout labels,
   timestamps, metrics and file hashes against registered source candles.
6. If **every** fold passes, publish \`batch_complete.json\` last. It
   records the SHA-256 of \`batch_plan.json\` and of each fold's sealed
   \`sha256.json\`, together with exact fold identities and test counts.

A failure or interruption leaves a **partial** directory with no
\`batch_complete.json\`. Do not treat it as completed, skip a fold, or
restart a partially written experiment with the same directory. Use a
**new experiment ID / output directory** for a retry. Failed results
should still be disclosed when comparing experimental evidence.

## CLI — manual, opt-in execution only

From \`backend/\` with PyTorch installed, using a registered, verified,
contiguous 1h/4h/1d dataset (at least 1,024 candles for default windows):

\`\`\`bash
python -m emergenzix_gpu_lab.fold_batch \
  --db-path /path/to/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --output-dir /tmp/gpu-walkforward-mlp-seed42 \
  --folds 3 --train-bars 512 \
  --validation-bars 128 --test-bars 128 \
  --lookback 32 --horizon 1 \
  --model mlp --device cpu --seed 42 \
  --epochs 2 --batch-size 256
\`\`\`

**GPU:** the CLI defaults to explicit \`--device cuda\` and will fail
closed without CUDA. CPU requires \`--device cpu\`. An operator who has
already booted/configured the dedicated A10/A10G host can run the same
module inside the existing isolated Compose GPU profile:

\`\`\`bash
docker compose --profile gpu run --rm --no-deps \
  --entrypoint python gpu-research \
  -m emergenzix_gpu_lab.fold_batch \
  --db-path /data/emergenzix.db \
  --dataset-id DATASET_SHA256 \
  --output-dir /outputs/gpu-walkforward-mlp-seed42 \
  --model mlp --device cuda --seed 42 --epochs 20
\`\`\`

**Do not start or charge any GPU host automatically.** This development
PR performs no actual experiments and does not boot an AWS machine.

## Output structure

\`\`\`text
new-batch/
  batch_plan.json           # immutable-by-contract plan; written first
  fold-001/
    model_weights.pt
    manifest.json
    metrics.json
    test_predictions.json
    sha256.json
  fold-002/
    ...
  fold-003/
    ...
  batch_complete.json       # written LAST only if every fold verifies
\`\`\`

A completion marker is a **record of checks performed**, not proof of
model profitability or independent authentication of the training host.
The fold verifier validates reported outputs against source data but
**does not replay saved PyTorch weights** to reproduce predictions.
The batch marker itself is not yet an independently verified/signed report.

No cross-fold statistical aggregation, PnL, slippage, fees, model ranking,
multi-trial correction, automatic promotion or order placement is offered.

## Codex acceptance gate

\`\`\`bash
cd backend
python -m pytest -q tests/test_gpu_fold_batch.py
python -m pytest -q \
  tests/test_gpu_fold_verify.py \
  tests/test_gpu_fold_train.py \
  tests/test_gpu_fold_samples.py \
  tests/test_gpu_walk_forward.py
\`\`\`

The added tests exercise chronological train→verify order, completion
only after every fold passes, rejection of invalid/unsafe configurations,
interruption on a failed trainer/verification, no automatic restart,
source SQLite integrity and a separate **optional** real two-fold CPU
run when PyTorch is installed. These tests have **not** yet been run.

## Next microtask (separate PR)

Build a **read-only batch report verifier and cross-fold descriptive
summary**: re-open each saved fold with the same source DB, validate the
batch completion marker and compute aggregate, sample-weighted holdout
MSE/MAE/direction accuracy against its zero-return baseline. Do not
select a winning model or claim statistical significance from the test
results; holdout-driven model/hyperparameter selection remains prohibited.
