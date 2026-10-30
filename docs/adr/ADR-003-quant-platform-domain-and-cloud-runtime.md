# ADR-003: Quantitative platform domains and cloud runtime boundary

- **Status:** Accepted
- **Date:** 2026-10-30
- **Scope:** Emergenzix product architecture and delivery boundary

## Context

Emergenzix already provides immutable market datasets, versioned strategies,
historical evaluation, a basic PAPER ledger, operational alerts, and container
build automation. It does not yet provide a complete portfolio, execution, or
production operations system. Treating those partial capabilities as one
undifferentiated trading service would make risk ownership unclear and could
allow research evidence or UI state to bypass execution controls.

The GitHub repository is the source of truth for reviewed product code and
deployment definitions. Cloud machines are runtime environments, not an
alternative source repository. Runtime state, credentials, logs, databases,
and generated reports must not be copied back into Git.

## Decision

Emergenzix will organize the product around ten explicit domains. A domain may
initially be implemented in the same process, but its inputs, outputs, safety
checks, and persisted evidence must remain distinguishable.

| Domain | Question | Current state | Required boundary |
| --- | --- | --- | --- |
| Data | What did the system observe? | Partially implemented | Immutable source identity, timestamps, quality, freshness, and content fingerprint |
| Indicators | What market state was derived? | Partially implemented inside analysis/backtests | Versioned, deterministic calculations from completed data only |
| Signals | Did a reviewable opportunity occur? | Partially implemented | Signal identity, strategy-independent evidence, and no order permission |
| Strategy | What trading rule applies? | Partially implemented | Immutable strategy versions, assumptions, lifecycle state, and reproducible evaluation |
| Position sizing | How large is one trade? | Pure PAPER calculator implemented; integration planned | Server-side sizing from price, stop distance, fees, limits, and available PAPER capital |
| Portfolio | What is aggregate risk? | Planned | Cross-position exposure, concentration, correlation cluster, drawdown, and cash constraints |
| Execution | How is an approved intent handled safely? | PAPER fill simulation only | Idempotent intent, adapter isolation, state machine, reconciliation, and fail-closed unknown state |
| Risk control | When must activity stop? | Basic checks and alerts only | Pre-trade checks, runtime guardrails, kill switch, stale-data block, and independently auditable decisions |
| Accounting | Where did profit and loss come from? | Basic PAPER cash/position records | Double-entry identities for cash, position, fees, slippage, funding, and realized/unrealized PnL |
| Research governance | How is a strategy validated, promoted, retired, or rolled back? | Lifecycle documented; automation incomplete | Evidence gates, human approval, immutable decisions, observation periods, retirement, and rollback |

Dependencies flow downward from observed data toward execution. Execution may
consume an approved order intent only after position, portfolio, mode, data
freshness, and risk decisions pass. Research results, AI output, UI state, or a
strategy score never grant execution permission.

## Source and runtime flow

```text
GitHub repository
  reviewed code + tests + migrations + deployment definitions
        |
        v
GitHub Actions
  tests + immutable versioned container images + checksums
        |
        v
Cloud runtime (AWS or another approved provider)
  pinned image digest + environment configuration + secret manager
        |
        +--> runtime database / audit log / metrics / reports
        |
        +--> public market data (current implementation)

Cloud runtime does not push credentials, databases, or generated state to Git.
```

Cloud deployment must be reproducible from a reviewed repository revision and
an immutable image digest. Operators must be able to identify the running
revision without embedding secrets. Direct, unreviewed editing of application
code on a cloud host is not a supported release process.

## Mode and host isolation

- DEMO/PAPER remains the default and only implemented execution boundary.
- TESTNET requires a separate adapter, credentials, configuration, persistence,
  acceptance report, and explicit enablement. It must never fall back to LIVE.
- LIVE remains unavailable. Adding a LIVE endpoint, credential, or order path
  requires a separate ADR and documented approval process.
- Research workers may produce immutable evidence but cannot mutate execution
  permissions or submit exchange orders.
- A control-plane host must not expose the unauthenticated API publicly. Cloud
  deployment requires reviewed authentication, authorization, TLS, network
  policy, secret management, backup, observability, and recovery design.

## Implementation sequence

The next slices should be implemented and verified independently:

1. Add idempotent PAPER order intents and auditable rejection records.
2. Separate deterministic position sizing from order creation.
3. Add PAPER portfolio exposure and aggregate risk decisions.
4. Add a PAPER execution state machine and reconciliation for unknown outcomes.
5. Add balanced accounting entries and PnL attribution.
6. Add an operator-controlled PAPER kill switch that fails closed.
7. Add explicit research promotion, observation, retirement, and rollback records.
8. Define authenticated cloud deployment only after the product boundaries are
   testable locally and in CI.

Each item is an epic and must be split into 15–30 minute micro-tasks before
implementation.

## Consequences

- The initial implementation may remain a modular monolith; this ADR does not
  authorize microservices or a stack change.
- Existing endpoints remain PAPER-only and are not reclassified as production
  execution.
- More persisted identities and audit records are required before TESTNET work.
- Cloud operation becomes a release and runtime concern, while GitHub remains
  the reviewed product source of truth.

## Rejected alternatives

- **Treat the current PAPER endpoint as a complete execution engine:** rejected
  because it lacks idempotent intents, reconciliation, portfolio risk, and
  complete accounting.
- **Let each cloud server carry its own edited copy of the application:**
  rejected because revisions, tests, rollback, and provenance would diverge.
- **Combine strategy approval with order permission:** rejected because research
  evidence cannot safely authorize capital or bypass current risk checks.
