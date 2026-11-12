# Emergenzix Core Engine Charter

## One Engine. Infinite Evolution.

**Product thesis:** Emergenzix concentrates its research effort on one compounding capability: a market-intelligence engine that learns reusable representations of market behavior from immutable historical data, produces calibrated and testable market assessments, and supplies those assessments to independently governed strategy and risk layers.

This is a focus decision, not a claim that a profitable model already exists.

## The one core capability

**BTC Market Intelligence** is the first proving ground. The engine should learn to:

1. Represent multi-timeframe market conditions from causal, point-in-time inputs.
2. Identify market states and transitions, including uncertainty and insufficient evidence.
3. Estimate conditional outcome distributions over explicitly defined horizons, not merely guess the next candle direction.
4. Compare its assessments against simple baselines and unseen chronological periods.
5. Preserve datasets, manifests, model versions, predictions, metrics, and experiment lineage so future models can reuse prior evidence.

## System boundaries

- **RDAP / data lake:** durable, immutable, content-addressed market data and experiment provenance.
- **NEMT:** hypotheses, market theory, labels, and research interpretation.
- **Core model:** market representation, state assessment, outcome distribution, and uncertainty.
- **Strategy layer:** converts reviewed model outputs into candidate actions; it is replaceable and separately evaluated.
- **Risk gate:** deterministic, independent, fail-closed constraints. A model cannot override it.
- **Telegram / UI:** operational interfaces only; not the research center of gravity.

## Research sequence

### E0 — Data and evaluation readiness
- Verify BTC historical dataset coverage, gaps, timestamps, source provenance, and hashes.
- Freeze causal feature definitions and prediction horizons.
- Establish chronological TRAIN / DEVELOPMENT / SEALED_TEST partitions with purge/embargo rules.
- Record strong simple baselines before training neural models.

### E1 — Market representation baseline
- Train a small, reproducible baseline (e.g. TCN or other suitable sequence model) on registered BTC data.
- Predict well-defined market-state and future-distribution targets.
- Compare against persistence, zero-return, linear/simple statistical, and shuffled-feature controls.
- Save immutable model, config, seed, dataset fingerprint, metrics, and predictions.

### E2 — Generalization and robustness
- Run walk-forward folds and multiple seeds.
- Evaluate calibration, state-wise performance, regime transitions, uncertainty, turnover implications, and cost-aware downstream utility.
- Test across volatility and liquidity conditions.
- Keep sealed test data unopened until the model and protocol are frozen.

### E3 — Strategy learning
- Only after E1/E2 evidence is credible, train or evaluate policies (including PPO) against the frozen observation interface.
- Use realistic fees, slippage, funding, position constraints, and liquidation assumptions.
- Compare against simple rule-based policies under identical data and risk budgets.

### E4 — Controlled PAPER evaluation
- Require independent risk approval, operational recovery tests, monitoring, and explicit human authorization.
- No automatic promotion and no LIVE order permission from model metrics or CI.

## Acceptance principles

A larger model is not automatically a better model. A passing training loop is not evidence of market understanding. A low prediction loss is not evidence of profitability.

A candidate must demonstrate:
- reproducibility from immutable inputs;
- improvement over relevant baselines on chronological unseen data;
- no future-data leakage or partition contamination;
- calibration and explicit uncertainty / abstention behavior;
- robustness across regimes and seeds;
- cost-aware downstream value without exceeding the common risk budget;
- complete provenance and independently verifiable artifacts.

Negative results are first-class research assets. A failed experiment must be recorded, explained, and reusable.

## Scope discipline

- BTC first. Do not expand to ETH/SOL until the BTC evaluation protocol is stable.
- One primary model candidate at a time. Alternative architectures are challengers, not parallel product pillars.
- No new dashboard or broad feature work unless it materially improves data quality, training, evaluation, or safe operations.
- Keep implementation tasks small (15–30 minutes), each with one objective and focused tests.
- Existing modules are retained only when they serve this engine or enforce independent safety boundaries.

## Current PR #81 disposition

PR #81 is research groundwork, not proof of a trained or ready model. Its negative dataset/audit results must remain negative evidence. Resolve the reported optional-Torch CI failures, preserve fail-closed behavior, and keep PPO/encoder training, sealed evaluation, promotion, TESTNET, and LIVE paths disabled until their separate acceptance gates pass.

## North-star metric

There is no single shortcut metric such as accuracy or training loss. The north star is **repeatable, calibrated, cost-aware improvement over strong baselines on unseen chronological market data, with controlled risk and complete provenance**.
