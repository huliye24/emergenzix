# Turtle 20/10 dual-AWS backtest — 2026-11-03

## Status

Verified research run. This is historical BACKTEST_ONLY evidence and does not authorize PAPER, TESTNET, or LIVE orders.

## Fixed inputs

- Product commit: `1b5c766`
- Protocol: `emergenzix-turtle-20-10-v1`
- Symbol: `BTCUSDT`
- Timeframe: `4h`
- Completed candles: `500`
- Range: `2026-08-11T20:00:00Z` through `2026-11-03T00:00:00Z`
- Initial cash: `10,000 USDT`
- Entry: close above the preceding 20-bar high
- Exit: close below the preceding 10-bar low
- Volatility unit: ATR(20)
- Risk budget: 1% of marked equity per unit
- Pyramiding: maximum four units, additions at half-ATR intervals
- Execution assumption: completed-close signal, next-bar-open fill
- Fee: 10 bps per side
- Slippage: 5 bps per side
- Direction: long-only, no leverage

The Binance public data was fetched once through the existing local Mihomo proxy on the CPU node, restricted to completed candles, then copied unchanged to the GPU node. Both nodes therefore evaluated the exact same immutable input.

## Nodes and reproducibility

| Node | Runtime | Result |
| --- | --- | --- |
| AWS Ningxia CPU (`moodify-cpu`) | Python 3.12.3, x86_64 | completed |
| AWS Ningxia GPU (`moodify-gpu`) | Python 3.12.3, NVIDIA A10G | completed |

- Dataset SHA-256 on both nodes: `6e6249773484f3a08a3e53914c814f32476f05d43234b2b9d68338857a1d72e0`
- Canonical report SHA-256 on both nodes: `f2dc766726eabbdd2ebe65176ad3ccd0b83c3f35f0e493da719cafd483eb36c4`
- Serialized result file SHA-256 on both nodes: `3dc28b87fab7b12f60bf9e5994a970330c9cf4e6d8820cf10672e1a3b847488f`

Matching hashes demonstrate deterministic parity for this input and code revision. They do not demonstrate profitability or external data correctness.

## Observed result

| Metric | Turtle baseline | Buy and hold benchmark |
| --- | ---: | ---: |
| Final equity | `10,342.24 USDT` | — |
| Total return | `+3.42%` | `+22.02%` |
| Maximum drawdown | `15.19%` | not calculated in this run |
| Closed trades | `8` | `1` conceptual hold |
| Win rate | `25.00%` | not comparable |

The strategy underperformed buy-and-hold by about 18.60 percentage points while still experiencing a 15.19% maximum drawdown. This sample does not support promoting the candidate. The two profitable trades were insufficient to offset six losing trades by a wide enough margin to beat the passive benchmark.

## Required next evidence

1. Repeat BTC, ETH, and BNB across 1h, 4h, and 1d immutable datasets.
2. Use walk-forward periods rather than selecting one recent window.
3. Compare System 1 (20/10) with System 2 (55/20) without tuning on the final holdout.
4. Add benchmark drawdown, volatility, Sharpe/Sortino, turnover, exposure time, profit factor, and fee/slippage sensitivity.
5. Stress gaps, missing candles, execution delay, larger slippage, and exchange outages.
6. Only after out-of-sample review, run shadow/PAPER observation; keep LIVE disabled.

## Runtime boundary

The isolated files are under `/home/ubuntu/emergenzix-runs/1b5c766` on both nodes. No systemd service was installed or enabled, no existing worker was modified, and no exchange credentials or private endpoints were used.
