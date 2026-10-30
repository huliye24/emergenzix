# Emergenzix Roadmap

This roadmap separates product readiness from research progress. Stages are gates, not calendar promises. Do not mark a stage verified without evidence in the repository or CI.

## Phase 0 — Product boundary and repository bootstrap

**Goal:** establish a clean product source of truth.

- [x] Create the independent public repository.
- [x] Confirm GPL-3.0 at the repository root.
- [x] Define product scope, security principles, and AI engineering rules.
- [x] Inventory the NEMT application code proposed for migration.
- [x] Record ADR-001 for the product baseline and NEMT migration boundary.
- [x] Create the first product implementation milestone from the verified inventory.

**Exit gate:** the team can explain which responsibilities belong in Emergenzix and NEMT, what the first backend supports, and what remains unimplemented.

## Phase 1 — Minimal application foundation

**Goal:** establish a runnable, testable product core without live trading.

- [x] Add a deterministic point-in-time historical replay primitive that hides all post-anchor candles during scenario analysis and reveals them only for outcome scoring.

- [x] Add a FastAPI backend, SQLite development persistence, and environment configuration.
- [x] Add health and overview endpoints.
- [x] Add strategy creation/listing and a first deterministic backtest implementation.
- [x] Add paper-account, position, and simulated order workflows with server-side checks.
- [x] Add automated API tests and a GitHub Actions workflow.
- [ ] Confirm the CI run is green and repeat the test suite on the AWS development environment.
- [x] Add the first responsive Next.js/React product interface against the API contract.
- [x] Add a sectioned product workspace for overview, strategies, backtests, paper trading, and activity without changing backend contracts.
- [x] Add separate API and web Dockerfiles with Docker Compose orchestration and a persistent SQLite volume.
- [x] Add GitHub Actions container-build CI and tag-triggered GHCR image publishing/GitHub Release automation.
- [ ] Add an opt-in, chat-allowlisted Telegram command bot for status, latest reports, and historical PAPER backtests (implementation in PR; verification pending).
- [ ] Have Codex build and run the Compose stack, exercise a paper workflow, and verify volume persistence on the AWS development environment.
- [ ] Publish v0.1.0 only after the runtime acceptance checklist passes.
- [ ] Add a reviewed authentication and deployment design before any network exposure.

**Exit gate:** a new contributor can run the backend locally in PAPER mode, tests pass, and no exchange credentials are required.

## Phase 2 — Strategy and evaluation workflow

**Goal:** make strategy candidates inspectable and reproducible.

- [x] Add public OHLCV retrieval through CCXT with bounded exchange/timeframe/limit inputs.
- [x] Add a fail-closed BTC/BNB 4h research sync path that merges checksum-verified Binance archives with a reconciled public tail, excludes forming candles, requires at least two years and strict continuity, and stores immutable content-addressed snapshots. Cloud scheduling and retention remain pending.
- [x] Record ADR-004 for a versioned S3 bronze/silver/gold market-data lake, with canonical 15m BTC/BNB input, deterministic 1h/4h/1d/1w/1mo UTC aggregation, manifests, lineage, revision handling, and fail-closed publication gates.
- [ ] Populate the S3 lake with checksum-verified BTCUSDT, ETHUSDT, and BNBUSDT 15m history from 2021-01-01 through the latest completed candle, publish Parquet partitions and derived timeframes, and verify a pinned manifest from a cloud reader.
  - [x] BTCUSDT 2026-first acceptance slice published and independently read back from S3; see `DATA_LAKE_BTC_2026_ACCEPTANCE.md`.
  - [x] Deploy and observe an idempotent 13-minute BTC closed-candle updater on the Ningxia CPU host; consecutive publish/no-change behavior verified.
  - [x] Extend BTCUSDT back to 2021, preserve seven checksum-valid source gaps explicitly, and verify the merged snapshot plus the 13-minute updater; see `DATA_LAKE_BTC_2021_2026_ACCEPTANCE.md`.
  - [ ] Publish ETHUSDT and BNBUSDT before closing the parent item.
- [ ] Implement ADR-005 venue-preserving multi-exchange data: schema-v2 price and volume semantics, independent Binance/OKX/Bybit manifests, listing-aware quality gates, and pinned cross-venue Gold metrics.
  - [x] Publish and test the schema-v2 candle contract with event/availability time, base volume, quote volume, trade count, optional taker-side volumes, closed/imputed flags, nullable-field propagation, and a non-destructive v1 boundary. Non-Binance adapters remain pending.
  - [x] Publish and independently verify Binance BTCUSDT schema-v2 price and full volume history from 2021 through current, with a separate 13-minute updater and unchanged v1 pointer; see `DATA_LAKE_BTC_V2_ACCEPTANCE.md`.
  - [x] Extend Binance BTCUSDT schema-v2 to its first official candle on 2017-08-17, quarantine 81 official off-grid records without rounding, preserve 31 source gaps, and verify the complete S3 snapshot; see `DATA_LAKE_BTC_2017_CURRENT_ACCEPTANCE.md`.
  - [ ] Verify observed historical depth and backfill eligible BTC, ETH, and BNB spot instruments independently for OKX and Bybit.
  - [ ] Publish coverage, median price, dispersion, aggregate volume, and venue-concentration products only after quote units and constituent manifests reconcile.
- [x] Persist source, exchange, symbol, and timeframe metadata with backtest results.
- [x] Add content-addressed immutable OHLCV dataset registration and retrieval with canonical SHA-256 identifiers.
- [x] Add a date-driven BTC weekly backtest: verified 4-hour archive coverage, pre-test-only candidate selection, immutable run records, and JSON/Markdown/CSV outputs (CI/runtime verification is tracked separately).
- [x] Add deterministic three-fold walk-forward evidence for the fixed SMA grid, with fold-level leakage controls, evidence status, and aggregate stability metrics.
- [x] Add a strategy-agnostic outer walk-forward protocol: 18-month training history, 3-month causal parameter selection, 3-month unseen test, and quarterly rolling. The engine accepts arbitrary experiment arms and shared cost/risk settings and reports profitable-window counts, drawdown stability, parameter changes, and results after removing the largest winner. The M0 three-arm identities live in a separate experiment configuration.
- [x] Add a generic parameter-stability selector that evaluates local grid neighborhoods, prefers profitable plateaus over isolated return peaks, records parameter-cliff diagnostics, and can be injected per experiment arm without coupling the walk-forward engine to a strategy.
- [ ] Connect Donchian-only, reviewed NEMT v4.4, and experimental NEMT v5.2.7 to that protocol with one in-memory OHLCV input. The protocol exists, but a real three-arm result is not yet verified; the current v4.4 CLI still downloads its own data and must be refactored without duplicating its execution logic.
- [x] Add one versioned Donchian breakout candidate with prior-candle channels and the same cost/execution assumptions as the SMA baseline.
- [x] Add a fixed fee/slippage sensitivity matrix for the frozen weekly candidate and export it to CSV and Markdown.
- [x] Add a read-only CLI for fingerprint-verified comparison of saved date-range backtest records.
- [ ] Expand the fixed SMA parameter grid into a versioned multi-strategy candidate registry (trend, breakout, and mean-reversion) while preserving pre-test-only selection and comparable execution assumptions.
- [ ] Add a separate BTC futures simulation model for long/short positions, including funding and liquidation assumptions, before claiming downside-profit or hedge capability; keep LIVE orders disabled.
- [x] Allow backtests to consume a registered dataset ID and persist the exact dataset fingerprint and range in result provenance (implementation added; verification pending).
- [ ] Add broader market-data completeness checks.
- [ ] Add a bounded process-local TTL cache for public OHLCV requests (implementation in PR; verification pending).
- [ ] Add a dataset quality report for interval gaps, irregular timestamps, coverage, and freshness diagnostics (implementation in PR; verification pending).
- [ ] Store versioned strategy parameters and input-data references (parameter snapshots implemented in PR; verification pending).
- [ ] Compare strategy versions on one immutable dataset and persist comparison provenance (implementation in PR; verification pending).
- [ ] Display assumptions, transaction costs, drawdown, and data coverage.
- [ ] Separate training/in-sample and validation/out-of-sample results (implementation in PR; verification pending).
- [ ] Record the provenance of imported NEMT research evidence.
- [x] Add JSON, Markdown, and CSV report exports for the date-driven BTC historical experiment (broader report formats remain future work; latest revision awaits CI confirmation).
- [ ] Add broader exportable evaluation reports across all backtest and validation workflows.
- [ ] Implement a Freqtrade adapter in a separate runtime and compare results against the current compatibility engine.

**Exit gate:** a backtest result can be reproduced from a strategy version, data version, parameter set, and recorded environment.

## Phase 3 — Paper trading and risk controls

**Goal:** harden simulated operations before exchange integration.

- [ ] Add configurable paper portfolios and auditable account resets.
- [ ] Test duplicate requests, concurrent orders, stale data, timeouts, and recovery.
- [x] Add optional explicit idempotency keys to PAPER orders with conflict detection.
- [ ] Add richer risk-limit configuration.
- [ ] Add marked-to-market portfolio valuation using a documented price source.
- [ ] Add audit events for rejected actions and account changes.
- [ ] Integrate deterministic position sizing with PAPER intent review (the pure calculator is implemented; API/order integration remains pending).
- [ ] Add aggregate PAPER portfolio exposure, concentration, and drawdown checks.
- [ ] Add an operator-controlled PAPER kill switch that fails closed.
- [ ] Add balanced accounting entries and PnL attribution for fills, fees, slippage, and valuation changes.
- [ ] Add explicit strategy observation, retirement, and rollback records without granting order permission.

**Exit gate:** paper trading survives expected failure cases and cannot reach a live exchange adapter.

The domain order and GitHub-to-cloud delivery boundary are defined in
[ADR-003](adr/ADR-003-quant-platform-domain-and-cloud-runtime.md). Every item
above must be split into 15–30 minute implementation tasks.

## Phase 4 — Exchange TESTNET

**Goal:** verify exchange connectivity and order-state correctness in a non-production environment. CCXT public market data is not an order adapter. Freqtrade is pinned as an upstream submodule, but its execution/backtesting adapter is not yet implemented.

- [ ] Define an exchange adapter contract.
- [ ] Keep TESTNET credentials server-side and separate from other modes.
- [ ] Verify order submission, cancellation, partial fills, reconciliation, and restart recovery.
- [ ] Add rate-limit, retry, idempotency, and clock-drift handling.
- [ ] Complete a written acceptance report with observed evidence.

**Exit gate:** end-to-end TESTNET behavior is independently verified. This does not authorize LIVE trading.

## Phase 5 — Production-readiness assessment

**Goal:** evaluate whether a restricted live mode should exist at all.

This phase requires a separate security, operational, legal, and risk review. It must define permissions, capital limits, kill switch, monitoring, incident response, recovery, and human authorization. The outcome may be to keep LIVE disabled.

**Exit gate:** written approval by the responsible maintainer. No earlier phase can automatically enable LIVE.

## Task sizing rule

Every implementation issue should be a 15–30 minute micro-task with one objective, explicit acceptance criteria, and a focused verification step. Split larger work into smaller issues before implementation.
