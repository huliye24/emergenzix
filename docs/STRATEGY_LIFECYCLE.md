# Strategy Lifecycle and Promotion Gates

A strategy is a versioned, reviewable artifact—not a prompt, an isolated backtest, or an AI recommendation. Promotion between stages requires evidence and must never automatically grant permission to use real funds.

## Lifecycle

1. **Hypothesis** — a falsifiable statement, rationale, expected mechanism, and known risks.
2. **Research** — experiments conducted in NEMT with versioned data, parameters, code, and recorded outcomes.
3. **Candidate** — a strategy definition imported into Emergenzix with provenance and explicit assumptions.
4. **Backtest** — evaluation that includes data coverage, transaction costs, slippage assumptions, drawdown, and failure cases.
5. **Out-of-sample review** — evaluation on data not used to develop or tune the strategy; document leakage and overfitting risks.
6. **Paper trading** — simulated operation using the intended workflow and monitored market data.
7. **TESTNET** — exchange-protocol validation using dedicated test credentials and documented acceptance tests.
8. **LIVE eligibility review** — separate review of operational, security, legal, and financial risks. Passing prior stages is necessary but not sufficient.

## Required evidence

For each candidate, record as applicable:

- Unique strategy ID and immutable version
- Hypothesis and intended market/instrument
- Data source, version, range, timezone, and quality checks
- Code/configuration revision and parameter set
- Train/validation/test partition and leakage controls
- Fees, slippage, liquidity, and execution assumptions
- Return, volatility, drawdown, exposure, turnover, and benchmark comparisons
- Known limitations, failure modes, and rejected alternatives
- Reviewer, decision, timestamp, and next permitted stage

## Promotion rules

- Missing provenance means the candidate cannot advance.
- A strong in-sample result alone is not sufficient.
- Failed or inconclusive tests must remain visible; do not delete inconvenient runs.
- Strategy versions must not mutate silently after evaluation.
- Any material code, parameter, or data change creates a new version and invalidates affected evidence.
- AI-generated explanations are not a substitute for measured results.
- No stage transition can enable LIVE order placement automatically.

## Execution safety

Risk limits and permission checks run server-side at the time of action, even when a strategy previously passed evaluation. Order intent must be validated against the current mode, data freshness, available balance, exposure limits, and other configured controls. Ambiguous execution state blocks new risky actions until reconciliation.

**Initial rule:** DEMO and PAPER first; TESTNET only after explicit acceptance; LIVE remains disabled unless separately approved.
