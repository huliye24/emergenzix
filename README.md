# Emergenzix

**An open-source, risk-controlled quantitative trading product.**

Emergenzix is the product layer emerging from the NEMT research program. It is intended to turn market data and research-backed strategy candidates into transparent, testable workflows for strategy management, backtesting, paper trading, and carefully gated exchange execution.

> **Current status: repository bootstrap.** Product documentation is being established before application code is migrated. This README does not claim that planned capabilities are already implemented.

## Why a separate repository?

NEMT and Emergenzix have different responsibilities:

- **NEMT — research and experiments:** economic hypotheses, quantitative theory, datasets, MATLAB/GPU experiments, model evaluation, and reproducible research.
- **Emergenzix — product and operations:** user-facing workflows, strategy lifecycle, backtesting interfaces, paper trading, exchange adapters, risk controls, auditability, and product delivery.

Research may inform product decisions, but research code and experimental results do not automatically become production trading logic. Any integration must have a documented interface and a verification path.

## Product principles

1. **Research before execution.** A hypothesis is not a trading strategy until it has passed defined validation gates.
2. **Risk controls are part of the product.** They are not optional add-ons or UI-only checks.
3. **Fail closed.** Unknown modes, stale data, missing permissions, invalid limits, or uncertain order state must block execution.
4. **Human approval for material risk.** AI-generated ideas and backtest results cannot independently authorize real-money trades.
5. **Reproducibility and auditability.** Record data versions, parameters, strategy versions, decisions, and execution outcomes.
6. **Small, verifiable engineering tasks.** Every change must have a clear scope, acceptance criteria, and proportionate tests.

## Initial product scope

- Strategy and configuration management
- Market-data access with freshness and quality indicators
- Backtesting and evaluation workflows
- Paper-trading lifecycle and portfolio/order history
- Risk limits, order previews, audit records, and controlled exchange adapters
- A mobile-first user experience; use web/PWA as the initial delivery option unless an architecture decision approves another client

The exact technology baseline and migration scope will be decided after auditing the existing NEMT product code. Do not assume that every NEMT module belongs in this repository.

## Safety boundary

The initial development path must prioritize **DEMO and PAPER** workflows. Exchange TESTNET integration requires explicit configuration and end-to-end acceptance tests. **LIVE real-money order execution must remain disabled** until a separate, documented security and risk review approves it.

Never commit API keys, secrets, account identifiers, private datasets, or production credentials. Exchange credentials must remain server-side, use least privilege, and must not have withdrawal permissions.

Emergenzix is software, not financial advice. Backtests and simulated performance do not guarantee future results.

## Repository guide

- [Product scope](docs/PRODUCT_SCOPE.md)
- [Roadmap](docs/ROADMAP.md)
- [Provisional architecture](docs/ARCHITECTURE.md)
- [Strategy lifecycle and promotion gates](docs/STRATEGY_LIFECYCLE.md)
- [Security policy](SECURITY.md)
- [AI and contributor engineering rules](AGENTS.md)

## Development status

This repository currently contains the GPL-3.0 license and initial product-governance documentation. Application code has not yet been migrated. The next step is to inventory the existing NEMT product implementation, record the migration boundary, and make the first architecture decision before moving code.

## License

Emergenzix is licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE). Third-party components retain their own license obligations; check compatibility before copying or adding dependencies.
