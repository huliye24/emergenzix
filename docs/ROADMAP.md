# Emergenzix Roadmap

This roadmap separates product readiness from research progress. Stages are gates, not calendar promises. Do not mark a stage verified without evidence in the repository or CI.

## Phase 0 — Product boundary and repository bootstrap

**Goal:** establish a clean product source of truth.

- [x] Create the independent public repository.
- [x] Confirm GPL-3.0 at the repository root.
- [x] Define product scope, security principles, and AI engineering rules.
- [x] Inventory the NEMT application code proposed for migration.
- [x] Record ADR-001 for the first backend baseline.
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
- [ ] Have Codex build and run the Compose stack, exercise a paper workflow, and verify volume persistence on the AWS development environment.
- [ ] Publish v0.1.0 only after the runtime acceptance checklist passes.
- [ ] Add a reviewed authentication and deployment design before any network exposure.

**Exit gate:** a new contributor can run the backend locally in PAPER mode, tests pass, and no exchange credentials are required.

## Phase 2 — Strategy and evaluation workflow

**Goal:** make strategy candidates inspectable and reproducible.

- [x] Add public OHLCV retrieval through CCXT with bounded exchange/timeframe/limit inputs.
- [x] Persist source, exchange, symbol, and timeframe metadata with backtest results.
- [x] Add content-addressed immutable OHLCV dataset registration and retrieval with canonical SHA-256 identifiers.
- [x] Add a date-driven BTC weekly backtest: verified 4-hour archive coverage, pre-test-only candidate selection, immutable run records, and JSON/Markdown/CSV outputs (CI/runtime verification is tracked separately).
- [x] Add deterministic three-fold walk-forward evidence for the fixed SMA grid, with fold-level leakage controls, evidence status, and aggregate stability metrics.
- [x] Add one versioned Donchian breakout candidate with prior-candle channels and the same cost/execution assumptions as the SMA baseline.
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
- [ ] Add explicit idempotency keys and richer risk-limit configuration.
- [ ] Add marked-to-market portfolio valuation using a documented price source.
- [ ] Add audit events for rejected actions and account changes.

**Exit gate:** paper trading survives expected failure cases and cannot reach a live exchange adapter.

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
