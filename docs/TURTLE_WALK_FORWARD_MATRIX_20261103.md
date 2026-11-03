# Turtle Walk-Forward Matrix — 2026-11-03

Status: **implemented and independently reproduced on both AWS nodes**.

This is a BACKTEST_ONLY research artifact. It cannot create PAPER, TESTNET, or
LIVE orders and does not establish profitability.

## Fixed protocol

- Code commit: `b2b91c8`
- Symbols: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`
- Source interval: closed Binance spot 15-minute candles
- Evaluation intervals: `1h`, `4h`, `1d`
- Systems: Turtle `20/10` and `55/20`, ATR(20), 1% risk unit, maximum four units
- Range: `2025-11-01T00:00:00Z` inclusive to `2026-11-02T00:00:00Z` exclusive
- Walk-forward: rolling 120-day context followed by a 60-day out-of-sample test,
  advanced 60 days; four complete folds per cell
- Initial cash: 10,000 USDT per isolated fold
- Costs: 10 bps fee and 5 bps slippage per side
- Execution: completed-candle signal, next-candle open fill
- Positioning: long-only, no leverage; open positions close at each fold end

The 120-day segment is used only for indicator warm-up. The engine starts each
test flat and rejects any attempt to trade before the OOS boundary. Partial final
folds are excluded rather than shortened.

## Dataset checks

The input is the existing immutable local mirror of the market-data lake's v2
monthly Parquet stage. All selected rows were checked before resampling:

- exact 15-minute UTC grid with no gap or duplicate;
- `is_closed=true` for every candle;
- zero `is_imputed=true` candles;
- positive, internally consistent OHLC and nonnegative volume;
- only complete UTC-aligned 1h/4h/1d bars are admitted.

The 39 monthly input files have the same ordered aggregate SHA-256 on both
nodes: `d89957dfc07919f9f9e1afe694f4d195366a80fc636d687fb53845ced0a681aa`.

This staged mirror is not yet the canonical S3 pointer set. At verification
time, S3 exposed only the BTC pointer, and that broader BTC snapshot reported
historical gaps. The matrix therefore records every local input file hash in
its JSON report and does not claim canonical S3 publication.

## OOS matrix summary

Returns below compound the four isolated OOS fold returns for comparison only.
`DD` is the worst maximum drawdown among the four folds, not a live portfolio
drawdown.

| Symbol | TF | System | OOS return | DD | Trades | Win rate | Buy/hold |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| BTC | 1h | 20/10 | -3.07% | 13.94% | 79 | 35.44% | 19.68% |
| BTC | 1h | 55/20 | -20.19% | 17.51% | 43 | 20.93% | 19.68% |
| BTC | 4h | 20/10 | 0.55% | 10.54% | 21 | 38.10% | 19.68% |
| BTC | 4h | 55/20 | 1.09% | 14.97% | 10 | 40.00% | 19.68% |
| BTC | 1d | 20/10 | 8.11% | 6.10% | 5 | 20.00% | 19.68% |
| BTC | 1d | 55/20 | 1.81% | 6.26% | 4 | 25.00% | 19.68% |
| ETH | 1h | 20/10 | -2.82% | 13.20% | 73 | 27.40% | 18.33% |
| ETH | 1h | 55/20 | -20.37% | 20.99% | 38 | 31.58% | 18.33% |
| ETH | 4h | 20/10 | 8.77% | 11.26% | 21 | 28.57% | 18.33% |
| ETH | 4h | 55/20 | -7.66% | 13.68% | 10 | 20.00% | 18.33% |
| ETH | 1d | 20/10 | -2.03% | 4.29% | 5 | 20.00% | 18.33% |
| ETH | 1d | 55/20 | 0.21% | 4.46% | 3 | 33.33% | 18.33% |
| BNB | 1h | 20/10 | -7.84% | 16.62% | 83 | 30.12% | 20.37% |
| BNB | 1h | 55/20 | 0.56% | 14.10% | 38 | 39.47% | 20.37% |
| BNB | 4h | 20/10 | -0.12% | 14.95% | 22 | 36.36% | 20.37% |
| BNB | 4h | 55/20 | -17.24% | 16.36% | 13 | 15.38% | 20.37% |
| BNB | 1d | 20/10 | 1.04% | 10.93% | 5 | 20.00% | 20.37% |
| BNB | 1d | 55/20 | 5.18% | 6.15% | 3 | 33.33% | 20.37% |

Most cells underperformed buy-and-hold over these folds. Several daily cells
also have too few trades for strong inference. No cell is promoted to PAPER or
LIVE by this report.

## Reproduction evidence

Both nodes used isolated `/home/ubuntu/emergenzix-runs/b2b91c8` directories and
PyArrow 26.0.0:

| Node | Wall time | Report SHA-256 | JSON file SHA-256 | CSV file SHA-256 |
| --- | ---: | --- | --- | --- |
| `moodify-cpu` | 2.30 s | `1eaf9e81b4cfd0f452d4b9e7ac6c003eac87bedecfec644caf97d4f849833966` | `6db6b9cdc7f96072cfbd9ceef90f1c7cca703206b4333958349a9f8f7852cbce` | `337492bafc5d399255fb1c8be4c7794efb3baba6b26072b543bf5e4efc8d1477` |
| `moodify-gpu` | 2.88 s | same | same | same |

Outputs remain at `evidence/matrix.json` and `evidence/matrix.csv` under each
isolated run directory. The classic engine is CPU-bound; the GPU node is a
deterministic parity runner here. GPU scheduling should be reserved for
Chronos/Qlib inference and model training.

## Fixed command

```bash
.venv/bin/python ops/turtle_walkforward_matrix.py \
  --input-dir DATA_LAKE_MIRROR \
  --start 2025-11-01T00:00:00Z \
  --end 2026-11-02T00:00:00Z \
  --train-days 120 --test-days 60 --step-days 60 \
  --initial-cash 10000 \
  --output-json evidence/matrix.json \
  --output-csv evidence/matrix.csv
```

Next data-layer task: publish verified BTC/ETH/BNB manifests and pointers to the
canonical lake, then have the runner resolve snapshot IDs instead of filesystem
paths. Next compute task: dispatch classic matrices to CPU and Chronos/Qlib
batches to GPU under the same immutable experiment manifest.
