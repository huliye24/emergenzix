# Emergenzix

**An open-source, risk-controlled quantitative trading product.**

NEMT theory, mathematical hypotheses, strategy specifications, and research
conclusions live in the separate
[Non-Equilibrium Market Theory repository](https://github.com/huliye24/Non-Equilibrium-Market-Theory).
This repository contains the software implementation and operational product.

Emergenzix is the product layer emerging from the NEMT research program. It is designed to turn market data and reviewed research into reproducible strategy evaluation and auditable trading workflows.

> **Development status:** `main` contains the initial backend, responsive dashboard, a CCXT-based public OHLCV endpoint, and a pinned Freqtrade upstream submodule. This is the v0.1.0 development target. Docker Compose packaging and GitHub container-release automation are being added; do not treat the version as released until Codex/AWS verification is complete.

## NEMT and Emergenzix

- **NEMT — theory and strategy research:** economic hypotheses, quantitative theory, strategy specifications, experiments, and reproducible conclusions. The public theory source is maintained in the separate NEMT theory repository.
- **Emergenzix — product and operations:** strategy management, backtesting, paper trading, product APIs, risk controls, and the user-facing responsive interface.

A research result must carry provenance and pass review before it informs a product strategy. Research code and experiment artifacts are not copied into the product runtime by default.

## Current implementation

- FastAPI backend with health and product overview endpoints
- Strategy creation and listing for the initial SMA-crossover strategy type
- Deterministic backtesting using completed-bar signals and next-bar-open execution
- Fee and slippage assumptions recorded with each backtest
- Persisted backtest results, equity curve, and closed-trade records
- SQLite-backed paper account, positions, and simulated order history
- Server-side notional limit, available-cash checks, and no-short-selling validation
- Responsive Next.js/React workspace with dedicated Overview, Strategies, Backtests, Paper Trading, and Activity views; strategy, backtest, paper-order, position, and history workflows share the existing API
- Public spot OHLCV retrieval through CCXT, plus verified Binance BTC/USDT 4h historical archive imports with SHA-256 checksum validation
- Content-addressed OHLCV dataset registry with canonical SHA-256 IDs, immutable duplicate handling, persisted candle payloads, and dataset metadata/list/detail endpoints
- BTC point-in-time historical replay records linked to immutable datasets, with deterministic IDs, record fingerprints, database-enforced immutability, and random or explicit timestamp anchors
- Date-driven BTC weekly backtests with checksum-verified Binance 4h archives, pre-test-only SMA and Donchian candidate selection, immutable result records, and JSON/Markdown/CSV report exports
- Freqtrade source pinned as a Git submodule for evaluation as the mature strategy/backtesting/dry-run engine; its adapter is not yet wired into the current backtest endpoint
- Dataset source, exchange, symbol, and timeframe persisted with backtest results
- Deterministic completed-candle scenario analysis for downside continuation, false breakdown, range, and upside reversal
- Automated backend tests, frontend type-check/build, Docker image build CI, and tag-triggered GHCR release workflow

The backtest is a baseline implementation, not evidence of profitability. Paper orders use caller-supplied reference prices and do not represent exchange market fills.

## Quick research run: initial capital + period

Run a BTC/USDT 4h historical benchmark with two required inputs:

```bash
cd backend
python -m emergenzix_api.quick_backtest_cli --capital 100 --period 1w
# Replay a frozen historical week instead of using the most recent complete UTC dates:
python -m emergenzix_api.quick_backtest_cli --capital 120 --period 1w --start 2025-10-21
```

Periods: `1w` (7 days), `1m` (30 days), `1y` (365 days), `3y` (1095 days), or custom `Nd`. For long windows, use the compute environment and verified historical archives. This **reuses the existing long-only spot strategy selector**, not the unimplemented small-account multi-tranche/pyramiding engine. It writes JSON, summary, equity, trade and cost-report artifacts; never submits exchange orders.

See [Quick backtest setup and API](docs/QUICK_BACKTEST.md). The API accepts `{"initial_cash":100,"period":"1w"}` at `POST /api/v1/research/date-range-backtests/quick`.

## Get the repository and upstream engine

Clone the repository with the pinned Freqtrade source:

```bash
git clone --recurse-submodules https://github.com/huliye24/emergenzix.git
cd emergenzix
```

If the repository is already cloned, initialize the upstream source with `git submodule update --init --recursive`. Freqtrade is kept as a separate source tree and should run in its own environment/container; its current upstream branch requires Python 3.12 or newer.

## Run with Docker Compose (recommended)

Requirements: Docker Engine and the Docker Compose v2 plugin.

```bash
cp .env.example .env
docker compose up -d --build
```

Open http://127.0.0.1:3000. The API is private to the Compose network and the web port binds to localhost by default.

Useful commands:

```bash
docker compose ps
docker compose logs -f api web
docker compose logs -f watch
docker compose down
```

### Optional Telegram command bot

After setting the Telegram token and exact allowed chat ID in the local .env file, enable the private command process explicitly:

~~~bash
docker compose --profile telegram up -d --build
docker compose --profile telegram logs -f telegram-bot
~~~

Available commands are /status, /latest, /history, /report RECORD_ID, /compare ID1 ID2, /evidence RECORD_ID, /stats [limit], and /backtest YYYY-MM-DD [days]. The /compare, /evidence, and /stats commands require their read-only research API endpoints to be deployed. See [Telegram bot setup and safety](docs/TELEGRAM_BOT.md). The bot only runs historical PAPER research and status queries; it has no order commands. Long research reports are sent in numbered parts rather than silently truncated.

SQLite data is stored in the named volume emergenzix-data and survives container recreation. **Do not run `docker compose down -v` unless you intend to delete that volume and the paper-trading records.**

The current application has no authentication. Keep the default localhost binding or use a protected private access path. Do not publish port 3000 or expose a public reverse proxy before the authentication and deployment review is complete.

After a verified release has been published, set EMERGENZIX_VERSION in .env to the desired version, then use `docker compose pull` followed by `docker compose up -d` to update the running containers.

## Run the backend

Requirements for the Emergenzix API: Python 3.11 or newer. CCXT is installed from `backend/requirements.txt`.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
```

Start the API in a terminal:

```bash
cd backend
uvicorn emergenzix_api.main:app --host 127.0.0.1 --port 8000
```

## Run the web dashboard

In a second terminal:

```bash
cd web
npm install
EMERGENZIX_API_URL=http://127.0.0.1:8000 npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). On Windows PowerShell, set the variable first with `$env:EMERGENZIX_API_URL = "http://127.0.0.1:8000"`, then run `npm run dev`.

The dashboard proxies API requests through Next.js. The backend must be running first.

## AWS / Codex verification

Run these checks from the repository:

```bash
cd backend
python -m pip install -r requirements.txt
python -m compileall -q emergenzix_api tests
pytest -q
cd ../web
npm install
npm run typecheck
npm run build
```

Use the observed output as evidence for the next iteration. The API currently has no authentication; keep it on localhost or protect it behind an approved private network/reverse proxy. Do not expose the backend or dashboard directly to the public internet without a reviewed deployment and authentication design.

## API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Runtime health and safety mode |
| GET | `/api/v1/overview` | Strategy, backtest, and paper-account summary |
| GET | `/api/v1/market-data/ohlcv` | Fetch public OHLCV candles through CCXT; no exchange credentials required |
| POST | `/api/v1/scenarios/analyze` | Classify completed candles into four reviewable market scenarios; never places orders |
| GET / POST | `/api/v1/datasets` | Register a validated OHLCV dataset by content hash or list saved dataset metadata |
| POST | `/api/v1/datasets/import/binance-btc-4h` | Download and checksum-verify official BTCUSDT 4h archive files for a requested date range, then register the immutable dataset |
| GET | `/api/v1/datasets/{dataset_id}` | Retrieve an immutable dataset and its exact saved candles |
| GET | `/api/v1/datasets/{dataset_id}/quality` | Diagnose interval gaps, coverage, and freshness without modifying the dataset |
| POST | `/api/v1/research/historical-replays` | Run and persist a reproducible BTC point-in-time replay; never creates orders |
| GET | `/api/v1/research/historical-replays` | List saved immutable BTC replay records |
| GET | `/api/v1/research/historical-replays/{record_id}` | Retrieve and verify a saved replay record |
| POST | `/api/v1/research/date-range-backtests` | Select a versioned BTC SMA or Donchian candidate using only pre-test data, simulate a requested historical date range, and persist an immutable report |
| GET | `/api/v1/research/date-range-backtests` | List saved date-range backtest reports |
| GET | `/api/v1/research/date-range-backtests/{record_id}` | Retrieve and verify a saved date-range report |
| GET | `/api/v1/research/date-range-comparisons?record_ids=...` | Compare 2–10 saved historical backtests via read-only, integrity-verified observations; no winner is selected |
| GET | `/api/v1/research/date-range-backtests/{record_id}/evidence` | Read-only v4 research evidence diagnostics; never authorizes strategy promotion or orders |
| GET | `/api/v1/research/date-range-statistics?limit=30` | Summarize latest verified saved experiments; distinguish record counts from independent trading weeks |
| GET / POST | `/api/v1/strategies` | List or create strategies |
| GET | `/api/v1/strategies/{id}` | Inspect a strategy and its current version |
| GET / POST | `/api/v1/strategies/{id}/versions` | List immutable parameter snapshots or create a new version |
| POST | `/api/v1/backtests` | Run and persist a backtest from raw bars or a registered dataset ID |
| GET | `/api/v1/backtests` | List recent backtests |
| GET | `/api/v1/backtests/{id}` | Retrieve a saved backtest |
| POST | `/api/v1/strategy-comparisons` | Compare 2–10 versions on one immutable dataset and persist the report |
| GET | `/api/v1/strategy-comparisons/{id}` | Retrieve a saved strategy comparison |
| POST | `/api/v1/strategy-validations` | Evaluate a pinned strategy version on chronological in-sample and out-of-sample periods |
| GET | `/api/v1/strategy-validations/{id}` | Retrieve a saved validation report |
| GET | `/api/v1/paper/account` | Inspect the simulated account |
| GET | `/api/v1/paper/positions` | List simulated positions |
| GET / POST | `/api/v1/paper/orders` | List or create paper orders |
| GET | `/api/v1/paper/trades` | Inspect simulated fills |

Backtest requests accept exactly one input source: ordered OHLCV bars with timezone-aware timestamps, or a registered `dataset_id`. Dataset-backed runs load the saved candle sequence server-side, reject a dataset whose symbol differs from the selected strategy, and persist the dataset ID, SHA-256 fingerprint, time range, and bar count in result provenance. Fees and slippage are explicit request parameters and are included in the saved result.

The `watch` Compose service evaluates public OHLCV continuously and writes JSON-line events only for its initial snapshot, a scenario transition, a risk failure, or a new paper-review trigger. It excludes the newest exchange candle from signal evaluation because it may still be forming, persists its last state in the data volume, and has no order-placement capability. When Telegram is configured, scenario events include a server-rendered candlestick and volume chart annotated with the same support, resistance, scenario, and completed-candle data used by the evaluator. For timely monitoring, the chart separately shades and labels the newest forming candle and its current reference price; that candle is explicitly marked as not being a signal input. The chart also overlays causal Bollinger Bands (20-period SMA, 2 population standard deviations) and one automatically selected trendline fitted from the latest two confirmed rising pivot lows or falling pivot highs. These overlays and the forming candle are visual monitoring aids only: they do not alter scenario classification, create a signal, or place an order. Run one local cycle with `cd backend && python -m emergenzix_api.watch_agent --once`.

Optional Telegram delivery is enabled only when both `EMERGENZIX_TELEGRAM_BOT_TOKEN` and `EMERGENZIX_TELEGRAM_CHAT_ID` are present in the server environment. Keep both values out of source control. Telegram delivery failures are reported with sanitized metadata and do not stop the watcher or create an order.

The read-only dataset quality endpoint reports timeframe-aligned candle gaps, irregular intervals, estimated coverage when spacing is consistent, and latest-candle freshness. It does not mutate datasets or automatically block historical backtests; freshness is a diagnostic heuristic.

Public OHLCV responses use a bounded, in-process TTL cache (30 seconds by default, up to 256 entries). Cache keys include exchange, normalized symbol, timeframe, and requested limit. Configure it with `EMERGENZIX_MARKET_DATA_CACHE_TTL_SECONDS` and `EMERGENZIX_MARKET_DATA_CACHE_MAX_ENTRIES`; set the TTL to `0` to disable caching. Only successful provider responses are cached. Each API process has its own cache, which is cleared on restart and is not shared across replicas.

Each new strategy starts with immutable version 1. Create later parameter snapshots with `POST /api/v1/strategies/{id}/versions`; prior versions remain unchanged, and repeated submission of the current parameters returns `409`. Backtests use the latest version by default or can pin a specific `strategy_version_id`. Results retain the version ID, canonical parameter snapshot, and SHA-256 parameter fingerprint. Historical backtests from databases created before this feature remain unassigned because their original version cannot be inferred safely.

Strategy comparisons require a registered immutable `dataset_id` and 2–10 unique version IDs from the same strategy. Every version is evaluated against the exact same saved candles, initial cash, fee rate, and slippage rate. The persisted report includes the dataset fingerprint, parameter fingerprints, and comparable return, drawdown, trade-count, win-rate, and benchmark metrics. Comparisons are descriptive research artifacts, not evidence of future profitability.

Strategy validation requires a registered immutable `dataset_id`, an explicit strategy version ID, and a chronological split fraction from 0.5 to 0.9 (default 0.7). The API runs the pinned version on the in-sample prefix and on the later holdout period with identical initial cash, fees, and slippage. Pre-split candles are used only to initialize holdout indicators; no position is carried into the holdout, and holdout metrics begin at the first holdout candle. The report records split boundaries, actual split fraction, dataset and parameter fingerprints, and both periods' return, drawdown, trade-count, win-rate, and benchmark metrics. The holdout is an evaluation period, not a parameter-selection loop; a caller can still misuse it by repeatedly tuning against holdout results, so review discipline remains necessary.

See [BTC Historical Replay Records](docs/HISTORICAL_REPLAY.md) for the replay API, provenance, integrity checks, and interpretation limits.

See [BTC Historical Data Ingestion](docs/BTC_DATA_INGESTION.md) for the verified Binance 4h archive import and dataset workflow.

See [BTC Date-Range Backtest](docs/DATE_RANGE_BACKTEST.md) for the date-driven one-week experiment, CLI invocation, immutable records, and CSV/Markdown report artifacts.
See [NEMT Strategy Library](docs/NEMT_STRATEGY_LIBRARY.md) for the reviewed v4.4 research adapter, provenance fingerprints, cloud default, and its strict no-LIVE boundary.
See [Saved Date-Range Backtest Comparison](docs/DATE_RANGE_COMPARISON.md) for read-only, fingerprint-verified comparison of persisted historical runs.
See [Weekly Research Batch Planning and Reports](docs/RESEARCH_BATCH_PLAN.md) for deterministic manifests, an internal `plan/register/status` CLI, durable leases, a one-job historical worker, and verified reports. No order permissions are granted. Optional private Telegram `/batch plan|register|status|report` commands require `EMERGENZIX_TELEGRAM_BATCH_ENABLED=true` and only register/review tasks; they never run a worker or trade.
For explicitly authorized cloud operations, `bash ops/research_batch_once.sh BATCH_SHA256` runs **one** historical worker job using the existing Docker Compose API image and shared SQLite volume; it does not install an automatic scheduler.
See [Codex Acceptance Checklist](docs/CODEX_DATE_RANGE_BACKTEST_ACCEPTANCE.md) for the exact validation and real-data smoke-test steps to run when Codex is available.


## GPU Alpha Lab — Experiment 001 (optional)

A research-only, GPU-isolated PyTorch MLP/TCN experiment can train on an existing SHA-256-verified OHLCV dataset in the API's SQLite volume. It uses purged chronological splits, train-only normalization, a zero-return baseline, and writes hashed experiment artifacts. **No inference or trading signal is connected to the API, Telegram, PAPER orders, or LIVE orders.** The default Compose services do not start the GPU lab. See [GPU Alpha Lab setup and A10 instructions](docs/GPU_ALPHA_LAB.md) before powering on a GPU host. A green regular CI checks CPU/data behavior, not A10 hardware.

## Reuse the pinned Freqtrade engine (isolated research pilot)

Instead of implementing another broker, position accounting engine or backtesting framework, the first upstream-engine experiment runs the already vendored [Freqtrade](https://github.com/freqtrade/freqtrade) source in its own Docker environment.

```bash
git submodule update --init --recursive vendor/freqtrade
docker compose -f ops/freqtrade/docker-compose.research.yml \
  --profile freqtrade-research --profile freqtrade-download pull
# See ops/freqtrade/README.md for explicit, public-data-only download.
cd backend
python -m emergenzix_api.freqtrade_research_cli --capital 100 --period 1w --start 2025-10-21
```

The backtest runs **without network access, exchange credentials, API ports or live trading**. The separate historical-data download requires an explicit action. The upstream sample strategy is a demonstration and has NOT been compared with NEMT or the S-003 staged-entry proposal. [Full isolated setup and limitations](ops/freqtrade/README.md).

## Safety boundaries

- The API only provides **PAPER** execution; the CCXT integration is read-only public market data, with no exchange order adapter or LIVE order endpoint.
- Unknown or invalid requests are rejected; paper orders enforce notional limits and prohibit short selling.
- API keys and exchange credentials are not required and must never be committed.
- The initial SQLite setup is for local/single-process development. Reassess storage, authentication, monitoring, and operational controls before multi-user or production deployment.
- Backtests and simulated results do not guarantee future performance.

## Repository guide

- [Product scope](docs/PRODUCT_SCOPE.md)
- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Upstream components and license boundaries](docs/UPSTREAM_COMPONENTS.md)
- [Migration inventory](docs/MIGRATION_INVENTORY.md)
- [ADR-001: product baseline and migration boundary](docs/adr/ADR-001-product-baseline-and-migration.md)
- [ADR-002: container and release pipeline](docs/adr/ADR-002-container-and-release-pipeline.md)
- [Versioning, CI/CD, and container releases](docs/RELEASE.md)
- [Strategy lifecycle](docs/STRATEGY_LIFECYCLE.md)
- [Engineering rules](AGENTS.md)
- [Security policy](SECURITY.md)

## License

Licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE).
