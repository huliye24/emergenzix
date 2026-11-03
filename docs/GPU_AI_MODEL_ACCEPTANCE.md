# GPU quantitative AI deployment acceptance

- **Observed:** 2026-11-03
- **Host:** Ningxia GPU node
- **GPU:** NVIDIA A10G, 23,028 MiB VRAM, compute capability 8.6
- **Boundary:** offline research and PAPER evidence only; no model has an order,
  exchange-credential, strategy-promotion, TESTNET, or LIVE capability
- **Network:** Clash HTTP proxy was scoped to installation and model-download
  commands; no model service or public port was created

## 1. Chronos zero-shot forecasting runtime

- Environment: `/home/ubuntu/emergenzix-chronos-v2.3.2/.venv`
- Package: `chronos-forecasting==2.3.2`
- Upstream tag: `amazon-science/chronos-forecasting@0aba28e9360b6355d5a919596c90b16c25939f02`
- PyTorch: `2.15.0+cu132`; CUDA available on the A10G
- `amazon/chronos-bolt-small` checkpoint revision:
  `772f3d25d38aec6d914c8949dab4462e2d46f5d8`
- `amazon/chronos-2` checkpoint revision:
  `29ec3766d36d6f73f0696f85560a422f50e8498c`
- Disk used by environment, checkpoints, and accepted input copy: 6.4 GiB

Both checkpoints loaded from the pinned local cache and produced finite
eight-step forecasts on CUDA. Observed allocated VRAM was approximately 91 MiB
for Chronos-Bolt Small and 228 MiB for Chronos-2 during the smoke fixture. This
only proves load and inference viability.

### Accepted market-data copy

The GPU host received 39 Parquet files selected on the Ningxia CPU from the
existing Binance spot 15-minute data lake: the latest immutable object in each
monthly partition from 2025-11 through 2026-11 for BTCUSDT, ETHUSDT, and
BNBUSDT. The basename-and-content collection SHA-256 matched on both hosts:

`5d555bcfe70891e60d4e4d642914754581dc7f4df57681759b66f560036ced96`

| Symbol | 15m rows | Range start UTC | Last accepted 15m open UTC | Duplicates | Gaps | Imputed | Complete 1h rows | Complete 4h rows |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | 35,240 | 2025-11-01 00:00 | 2026-11-03 01:45 | 0 | 0 | 0 | 8,810 | 2,202 |
| ETHUSDT | 35,180 | 2025-11-01 00:00 | 2026-11-02 10:45 | 0 | 0 | 0 | 8,795 | 2,198 |
| BNBUSDT | 35,140 | 2025-11-01 00:00 | 2026-11-02 00:45 | 0 | 0 | 0 | 8,785 | 2,196 |

Every accepted source row was marked closed. One-hour and four-hour counts
were derived deterministically from complete groups of four and sixteen 15m
rows; partial groups were excluded.

### Zero-shot walk-forward evidence

- Benchmark implementation: commit
  `39abd5fbb7ffc5406b5e95efa73e422b600256bb`
- GPU source copy SHA-256:
  `24d69d8a67eb364c22187c19072d5960bda58e5de6f6e7a8c8b9fd9bff641e29`
- Context: 512 observations; forecast horizon: one bar
- Origins: eight per symbol and timeframe, spaced seven days apart
- Total observations per method: 72
- Assumed round-trip cost: 30 bps for each active long sample
- Report:
  `/home/ubuntu/emergenzix-chronos-code-39abd5f/results/chronos-walk-forward-v1.json`
- Report SHA-256:
  `3dfe27202c9effe682e4866678f06c725ccf27a349f70b7f6d27b89d1bd7bac2`

| Method | Direction accuracy | Forecast-return MAE | Active long samples | Gross sampled return sum | Cost-adjusted sampled return sum |
| --- | ---: | ---: | ---: | ---: | ---: |
| Chronos-Bolt Small | 61.11% | 0.3757% | 41 | 1.4142% | -10.8858% |
| Chronos-2 | 52.78% | 0.3206% | 27 | 1.4199% | -6.6801% |
| Buy and hold | 48.61% | n/a | 72 | 2.2595% | -19.3405% |
| SMA 20/50 | 45.83% | n/a | 34 | 0.2249% | -9.9751% |

This is a sparse, one-step, zero-shot comparison, not a continuous portfolio
backtest. Return sums are descriptive and are not compounded. The baseline
rows emit direction signals rather than return forecasts, so reporting a
forecast MAE for them would be misleading. All cost-adjusted results were
negative; no method passed a strategy-promotion or execution gate. Qlib is not
shown because its models have not yet been trained on the locked feature and
label specification.

## 2. Qlib CUDA model runtime

- Environment: `/home/ubuntu/emergenzix-qlib-gpu-0.9.8/.venv`
- Source: `microsoft/qlib@54355232463878d2eebb91fe0ee5fa7fa1f5976c`
- Qlib: `0.9.8` from the pinned source, matching the Ningxia CPU evaluation
- PyTorch: `2.15.0+cu132`; pandas: `2.3.3`
- Disk used: 6.6 GiB

Transformer, LSTM, ALSTM, and TRA were instantiated with deterministic seeds.
Their parameters were observed on `cuda:0`; the TRA predictor and router were
both on `cuda:0`. Optional CatBoost and XGBoost imports were absent and are not
part of this acceptance. No training score, saved model, or strategy result is
claimed yet.

### Locked Qlib dataset contract

The training contract uses the same accepted Parquet collection and does not
download or extend market history. Each feature row uses only a completed
candle available before the label candle. The label is the following candle's
close-to-close percentage return.

- Sequence length: 60 completed feature rows
- Features: 1/4/16-period log returns, candle body, high-low range, log volume
  change, 16-period realized volatility, SMA 20/50 spread, and taker-buy share
- Normalization: mean and standard deviation fitted only on rows at or before
  the training boundary
- Train targets: through `2026-06-30 23:59:59` UTC; deterministic cap of 6,000
  latest sequences per symbol/timeframe
- Validation targets: `2026-07-01 00:00:00` through
  `2026-08-31 23:59:59` UTC; cap of 1,000 latest sequences per
  symbol/timeframe
- Test targets: the exact 72 Chronos origins, with no dense test-window rows
  added

The GPU preparation smoke test produced 138,096 causal raw rows, 39,126 train
sequences, 7,116 validation sequences, and 72 test sequences across the nine
BTC/ETH/BNB and 15m/1h/4h instruments. Training and model-result acceptance
remain pending.

## 3. FinBERT sentiment runtime

- Environment: `/home/ubuntu/emergenzix-finbert-prosus/.venv`
- Runtime: PyTorch `2.15.0+cu132`, Transformers `4.57.6`
- Model: `ProsusAI/finbert`
- Checkpoint revision: `0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031`
- Disk used: 5.9 GiB

The classifier loaded on `cuda:0`. A fixed three-text smoke fixture returned
positive, negative, and neutral respectively, with top probabilities 0.954798,
0.957026, and 0.744991. These examples test the deployed classification path,
not real-world accuracy, calibration, crypto relevance, or profitability.

## Known limitations and next acceptance

- Chronos calibration and a Qlib comparison have not yet been completed. The
  completed sparse walk-forward is too small to establish statistical or
  economic significance and is not a replacement for a continuous backtest.
- Qlib models have selected CUDA but have not yet been trained on a
  point-in-time BTC/ETH/BNB feature dataset with locked holdouts.
- No point-in-time news dataset, publication-time validation, source-license
  inventory, deduplication, or FinBERT calibration study exists yet.
- FinGPT 7B/13B was deliberately not downloaded. The published 13B sentiment
  artifact is a LoRA adapter requiring a matching Llama 2 base model and a
  separate review of base-model access and terms.
- None of these models is wired into the Emergenzix API, UI, paper-order path,
  risk gate, or strategy lifecycle. A future adapter must remain advisory and
  fail closed on stale data, missing provenance, non-finite output, model drift,
  or unavailable checkpoints.
