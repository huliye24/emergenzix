# Emergenzix Roadmap

This roadmap separates product readiness from research progress. Stages are gates, not calendar promises. Do not mark a stage complete without evidence in the repository.

## Phase 0 — Product boundary and repository bootstrap

**Goal:** establish a clean product source of truth.

- [x] Create the independent public repository.
- [x] Confirm GPL-3.0 at the repository root.
- [x] Define product scope, security principles, and AI engineering rules.
- [ ] Inventory the NEMT application code proposed for migration.
- [ ] Record an ADR for the initial stack and migration boundary.
- [ ] Create the first product implementation milestone from verified inventory.

**Exit gate:** the team can explain which files move, which remain in NEMT, which dependencies are compatible, and how the new product will be tested.

## Phase 1 — Minimal application foundation

**Goal:** establish a runnable, testable product shell without live trading.

- [ ] Migrate or recreate only the approved minimal client and API foundation.
- [ ] Add reproducible setup instructions and environment examples.
- [ ] Add health checks and a basic automated test pipeline.
- [ ] Add explicit DEMO mode and clear environment labeling.
- [ ] Add a minimal responsive navigation and empty states.
- [ ] Add CI checks for accidental secrets and unsafe configuration.

**Exit gate:** a new contributor can run the product locally in DEMO mode using documented steps; tests pass and no exchange credentials are required.

## Phase 2 — Strategy and evaluation workflow

**Goal:** make strategy candidates inspectable and reproducible.

- [ ] Define strategy and backtest domain models.
- [ ] Store versioned parameters and data references.
- [ ] Display assumptions, transaction costs, drawdown, and data coverage.
- [ ] Separate training/in-sample and validation/out-of-sample results.
- [ ] Record the provenance of imported NEMT research evidence.
- [ ] Add exportable evaluation reports.

**Exit gate:** a backtest result can be reproduced from a strategy version, data version, parameter set, and recorded environment.

## Phase 3 — Paper trading and risk controls

**Goal:** test operational behavior without real funds.

- [ ] Implement paper-trading sessions and explicit start/pause/stop controls.
- [ ] Implement server-side order preview and risk validation.
- [ ] Track orders, fills, positions, and simulated balances.
- [ ] Test duplicate requests, partial fills, stale data, timeouts, and recovery.
- [ ] Add an auditable record of rejected actions and reasons.

**Exit gate:** paper trading survives expected failure cases and cannot reach a live exchange adapter.

## Phase 4 — Exchange TESTNET

**Goal:** verify exchange connectivity and order-state correctness in a non-production environment.

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
