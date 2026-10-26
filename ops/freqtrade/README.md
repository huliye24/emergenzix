# Freqtrade — upstream research engine, not another custom execution stack

**Selected upstream:** [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade), GPL-3.0. This repository already has its source as a **pinned Git submodule** at `vendor/freqtrade`, SHA `c051e9065c5b677f03817dd1f83cb980af1ed684`. The opt-in research profile below reuses its official **Docker runtime** and the `SampleStrategy` file from that pinned source; the runtime image digest and source commit are **different provenance fields**.

Freqtrade already supports backtesting, wallet sizing, fees, dry-run infrastructure and position adjustments through its strategy callbacks. We should validate these before writing competing portfolio/order modules. The adapter is NOT integrated into Emergenzix's date-range API and does not implement S-003 tranche plans yet.

## 1. Fetch the upstream GitHub source and Docker image

From the repository root (Git Bash, WSL2 or Linux/Mac terminal):

```bash
git submodule update --init --recursive vendor/freqtrade
git -C vendor/freqtrade rev-parse HEAD
# Must print c051e9065c5b677f03817dd1f83cb980af1ed684

mkdir -p ops/freqtrade/user_data/data ops/freqtrade/user_data/backtest_results
docker compose -f ops/freqtrade/docker-compose.research.yml \
  --profile freqtrade-research --profile freqtrade-download pull
```

The Compose file is **separate** from the main application. Nothing starts on normal `docker compose up`. It uses Freqtrade's official `freqtradeorg/freqtrade:stable` image by default, not its Python environment inside the API. `stable` moves with upstream releases: for rigorous provenance, record `docker image inspect freqtradeorg/freqtrade:stable --format '{{index .RepoDigests 0}}'` or override `FREQTRADE_RESEARCH_IMAGE` with a reviewed immutable `@sha256:...` digest before a frozen experiment. Image version equivalence to the pinned source is **not** assumed.

## 2. Download *public* BTC/USDT history (explicit network opt-in)

Only the separate `freqtrade-public-data` service needs network access and it has no exchange credentials, API server, Telegram or configured trade command:

```bash
docker compose -f ops/freqtrade/docker-compose.research.yml \
  --profile freqtrade-download run --rm --no-deps freqtrade-public-data \
  download-data --config /freqtrade/user_data/config.research.json \
  --exchange binance --pairs BTC/USDT --timeframes 4h \
  --timerange 20250901-20251101 --data-format-ohlcv json
```

Start downloading **well before** the requested backtest because the upstream SampleStrategy uses a 200-candle startup window. Archived market data is not verified by Emergenzix's separate Binance ZIP + SHA-256 mechanism in this pilot. Record source, timeframe, missing-candle checks and data hashes before comparing outputs. If the exchange API is unavailable, fail rather than invent a candle.

## 2A. Preferred parity mode: use the *same verified dataset* as Emergenzix

When comparing engines, do **not** let one use downloaded Freqtrade candles and the other use a separate data feed: their date boundaries, microsecond/millisecond handling and missing candles may differ. Use a content-addressed BTCUSDT spot 4h dataset that already exists in the Emergenzix SQLite DB (e.g. one created by the date-range pipeline):

```bash
# From backend/ ; specify the DB that actually holds that dataset ID.
python -m emergenzix_api.freqtrade_dataset_bridge \
  --db-path ../data/emergenzix.db \
  --dataset-id <64-hex-dataset-id>
```

The bridge reads the source SQLite database **read-only**, checks the dataset's canonical SHA-256, limits the format to a dataset *declared* `binance_public_archive` / `binance` / `BTCUSDT` / `4h`, and validates candle OHLCV, consecutive UTC 4h opens, alignment and completed candles. It produces:

```text
ops/freqtrade/user_data/data/binance/BTC_USDT-4h.json
ops/freqtrade/user_data/data/binance/BTC_USDT-4h.emergenzix-manifest.json
```

Freqtrade's pinned JSON data-handler expects the positional layout `[epoch_ms, open, high, low, close, volume]`. The manifest records source dataset ID/SHA, exported file SHA, count, UTC boundaries, and an explicit warning that the data's exchange source remains **declared metadata** rather than independently cryptographically authenticated by the registration database. The bridge refuses incomplete candles, gaps, overwriting different existing Freqtrade data, or an altered manifest; repeat export is idempotent only if both still match.

Use the **dataset-locked** Freqtrade runner and specify the same historical window as the Emergenzix report:

```bash
python -m emergenzix_api.freqtrade_research_cli \
  --capital 100 --period 1w --start 2025-10-21 \
  --db-path ../data/emergenzix.db \
  --dataset-id <64-hex-dataset-id>
```

Before invoking Docker, the runner verifies the source DB **again** against the Freqtrade file/manifest and checks at least **200 previous 4h candles** for Freqtrade's pinned SampleStrategy startup window and coverage through the full exclusive end boundary. Both `--db-path` and `--dataset-id` are required together. Missing history blocks the run; it does **not** fetch or invent candles.

**Critical qualification:** same dataset does not mean same strategy or equivalent fees/fills. This is still Freqtrade's upstream **SampleStrategy**, not Emergenzix's SMA/Donchian research candidate or NEMT. A matching data fingerprint permits subsequent engine parity work, but returns must **not** be compared as if the trading rules already match.

## 3. Run an offline historical backtest from capital + period

From `backend/`:

```bash
python -m emergenzix_api.freqtrade_research_cli \
  --capital 100 --period 1w --start 2025-10-21
```

Or the most recent complete UTC week:

```bash
python -m emergenzix_api.freqtrade_research_cli --capital 120 --period 1w
```

Periods use the same **fixed-day** normalization as the built-in quick backtester: `1w=7d`, `1m=30d`, `1y=365d`, `3y=1095d`, or `Nd` up to 1095 days. Historical start date must be complete and end before the current UTC day. The runner checks the local sample strategy, validates the keyless dry-run config and the exact Git submodule SHA, then invokes **only `backtesting`** with a fixed BTC/USDT pair. Results go into `ops/freqtrade/user_data/backtest_results/`.

The **backtest container has no network access**, no exposed ports, a read-only root filesystem and no exchange credentials. If the isolated upstream version cannot backtest from local history alone, treat it as an integration failure and diagnose it; do not quietly enable an exchange or LIVE connection.

### Limitations (do not report S-003 performance from this)

- **Upstream `SampleStrategy` is a demo**, not NEMT and not a strategy we endorse.
- `stake_amount=unlimited`, `max_open_trades=1` is an upstream baseline—not our reviewed portfolio-wide risk allocation or multi-tranche policy.
- No link yet to S-003.1 ledger or pyramiding, no strategy-library router, and no fee/fill parity with the existing Emergenzix backtester.
- Result files are upstream-native; the Emergenzix API and Telegram do **not** display them until a separately tested normalization adapter exists.
- Config is spot-only, `dry_run=true`, with an empty exchange key and secret. Neither profile exposes an exchange-order API.
- CI checks source/config, Python launcher, and Compose syntax. Real Docker execution, archive downloads, full report comparison and small-account minimum-order validation still require a host environment and verified market data.

## 3A. Signal-parity experiment: Emergenzix SMA(5,20)

Before trying NEMT, a deliberately simple baseline is available in
`ops/freqtrade/strategies/EmergenzixSmaParity.py`. It duplicates **only the
signal decisions** of `emergenzix_api.backtesting.run_sma_crossover`:

| Contract | Both research implementations |
| --- | --- |
| Market | BTC/USDT 4h spot, long-only |
| Signal | Fast SMA(5) crosses over/under SMA(20), closes only |
| Causality | Signal on completed bar; intended fill at NEXT 4h open |
| Wallet | Initial user capital, single long position, no pyramid |
| Side fee in parity run | Freqtrade explicitly `--fee 0.001` (10bps) |
| Verified history | **Must provide** same source dataset SHA-256 + DB path |

For reproducible analysis (after [2A](#2a-preferred-parity-mode-use-the-same-verified-dataset-as-emergenzix)):

```bash
cd backend
python -m emergenzix_api.freqtrade_research_cli \
  --capital 100 --period 1w --start 2025-10-21 \
  --baseline sma-5-20 \
  --db-path ../data/emergenzix.db \
  --dataset-id <64-hex-dataset-id>
```

The default `--baseline sample` remains upstream SampleStrategy, in its own
read-only source path. The local parity strategy and its distinct wallet config
are mounted read-only, and `--baseline sma-5-20` refuses to run without source
parity. CI tests the **real SMA crossover methods** from the adapter with
synthetic pandas candles and compares the signal indices and expected
next-open execution timestamps to the existing Emergenzix implementation.

### What this does NOT yet make equivalent

1. **Slippage:** old Emergenzix reference defaults to 5bps per side; Freqtrade's
   backtesting fee flag controls *fees*, not identical fill slippage. For the
   first clean reference compare, run Emergenzix SMA with `slippage_bps=0`,
   `fee_bps=10`; later add a separate documented fill/slippage parity policy.
2. **Reserve and order sizing:** Freqtrade has precision, minimum stake, exchange
   filters, order sizing and fee reservation rules, even with parity config's
   `tradable_balance_ratio=1.0`; do not expect all quantities to match.
3. **Exit safeguards:** Emergenzix forces the final open position to close
   on the last candle close (with costs). Freqtrade's end-of-backtest exit
   and stop/ROI processing may differ. A very remote safety stop and ROI
   threshold are retained in the parity adapter; they are not exact replicas.
4. **Engine details:** Same next-open signal *intent* does not guarantee identical
   execution order, timestamp handling, fill price, rejected orders or final
   equity across implementations. Instrument these as observed mismatches.
5. **No normal historical run has been performed here.** The new CI validation
   is source-level signal equivalence, not a live Docker-integrated PnL parity
   or a validated S-003 staged-tranche performance result.

**Acceptance for the next microtask:** execute both engines on exactly one
same-SHA archive, compare (a) all signal timestamp/direction events and their
4h candle indices, (b) actual entry/exit time and prices, (c) fee and fill
differences, (d) wallet/final-equity difference and (e) open-at-end disposition;
label every mismatch by cause. Do not tune signals to make PnL agree.

## 4. Next small work items

1. **Smoke-test** the isolated Freqtrade container: verify image digest, data coverage, source revision, startup candles, 100 USDT starting balance and native output with a frozen 1w interval.
2. Define an adapter for immutable exported trade/equity evidence and compare fees/slippage/timestamps to the existing Emergenzix baseline. Do not mix incompatible metrics.
3. Port **one** already-reviewed NEMT strategy into an upstream `IStrategy` and test the same data both ways, with explicit execution semantics.
4. Only then implement staged adds via upstream `adjust_trade_position` and `custom_stake_amount`, verifying total-plan risk, candle effects, wallet behavior and transaction fees.
5. Keep SQLite schema migration for the local 1y/3y quick-backtest pathway as a separate tracked compatibility task: [#66](https://github.com/huliye24/emergenzix/issues/66).

Do not fork a second general-purpose brokerage, matching, account, backtest, or web UI framework until the selected upstream demonstrably cannot support a documented requirement.
