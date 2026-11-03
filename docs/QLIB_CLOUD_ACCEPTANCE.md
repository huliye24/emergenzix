# Qlib Ningxia CPU acceptance

- **Status:** deployed and execution-accounting comparison verified
- **Observed:** 2026-11-03
- **Mode:** offline research only; no credentials, exchange gateway, order API, or LIVE permission

## Deployment

| Item | Observed value |
| --- | --- |
| Host | AWS China Ningxia CPU research node `ip-172-31-43-176` |
| Qlib source | `microsoft/qlib` commit `54355232463878d2eebb91fe0ee5fa7fa1f5976c` |
| Qlib version | `0.9.8` from the pinned editable source checkout |
| Isolated environment | `/home/ubuntu/qlib-5435523/.venv` |
| Installed tree | `/home/ubuntu/qlib-5435523` (approximately 1.3 GB including the virtual environment) |
| Network | GitHub/PyPI were reachable directly; the retry used host-local Mihomo at `127.0.0.1:7890` without changing global proxy configuration |

Qlib remains outside the Emergenzix API environment. Its dependency tree
includes MLflow, LightGBM, CVXPY, Jupyter, Gym, and related scientific packages.
The installation emitted Qlib's upstream warning that Gym is unmaintained and
does not support NumPy 2.0; no reinforcement-learning result is accepted by
this deployment.

## Immutable input

| Item | Value |
| --- | --- |
| Dataset ID / content SHA-256 | `fb07ce13b64382c28e68b069096a85ff895a22f189f076456888ce0cddf26e9e` |
| Source | Binance public archive |
| Instrument | `BTC/USDT` |
| Frequency | 4 hours / Qlib `240min` |
| Range | `2026-01-31T00:00:00Z` through `2026-05-30T20:00:00Z` |
| Bars | 720 |
| Interval gaps | 0 |

The stored row was verified with Emergenzix's content-addressed dataset
verifier before export. The UTC-aware timestamps were converted to
timezone-naive UTC solely because Qlib's `dump_bin` requires
`datetime64[ns]`; OHLCV values and ordering were unchanged. Qlib then read 720
calendar entries and 720 OHLCV rows with zero missing values.

## First execution-accounting comparison

Both engines consumed the same SMA(10,30) crossover decisions:

- initial cash: 10,000 USDT;
- long-only, no leverage;
- completed-bar signal and next 4-hour bar open execution;
- 10 basis points fee per side;
- zero slippage for this engine-accounting comparison;
- 32 orders forming 16 completed round trips;
- all positions closed by 2026-05-27.

| Metric | Emergenzix | Qlib |
| --- | ---: | ---: |
| Final equity | `7690.7330444` | `7690.733140987903` |
| Total return | `-23.092670%` | `-23.09266859012097%` |
| Absolute final-equity difference |  | `0.00009658790349931223` USDT |
| BTC buy-and-hold return over the Emergenzix evaluation range | `-12.066157%` | supplied as an explicit 4-hour return series |

The near-zero equity difference supports consistency of this one execution and
fee-accounting fixture. It does not validate Qlib generally, prove strategy
profitability, or justify promotion. The strategy materially underperformed
the BTC benchmark in this sample.

## Artifacts and fingerprints

Artifacts remain on the research host under
`/home/ubuntu/qlib-emergenzix-data/fb07ce13b64382c28e68b069096a85ff895a22f189f076456888ce0cddf26e9e`.

| Artifact | SHA-256 |
| --- | --- |
| `source/btcusdt.csv` | `2121c1774905178343f22502b5b5990f335be46fb21dc2ba424d7da5e8e5114d` |
| `orders-sma-10-30.csv` | `36e0ad10c78876feb0374bb10d03ed879ec61b7e141166c8453665ce28f757a0` |
| `comparison-sma-10-30.json` | `7306bca31745f567cca00e0d90f653480e8af7490097aa23a6ac3c6f2895d477` |

## Known limitations

1. Qlib's benchmark path tried to load `1min` data for a `240min` run. The
   acceptance passed the BTC 4-hour return series explicitly instead of
   fabricating minute bars.
2. Qlib requires a future-calendar boundary for the last execution step. The
   Qlib run ended at the penultimate bar (`2026-05-30T16:00:00Z`) and retained
   the final bar as that boundary. This cannot change the result because all
   orders were closed three days earlier.
3. The comparison replayed identical Emergenzix-generated orders. It validates
   one execution/accounting fixture, not Qlib model training, factor discovery,
   signal generation, data-provider behavior, or general backtest equivalence.
4. No automatic scheduler, API endpoint, model service, TESTNET gateway, or
   LIVE integration was installed.

