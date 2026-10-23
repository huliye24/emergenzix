# Emergenzix — Engineering Rules for AI and Human Contributors

These rules apply to Codex, Claude Code, other coding agents, and human contributors.

## 1. Start with the product boundary

Before changing code, read:

- [README.md](README.md)
- [Product scope](docs/PRODUCT_SCOPE.md)
- [Roadmap](docs/ROADMAP.md)
- [Provisional architecture](docs/ARCHITECTURE.md)
- [Strategy lifecycle](docs/STRATEGY_LIFECYCLE.md)
- [Security policy](SECURITY.md)

Emergenzix is the product repository. NEMT remains the research and experiment repository. Do not move research notebooks, raw datasets, GPU artifacts, MATLAB experiments, or unrelated prototypes here without an explicit decision.

## 2. Mandatory micro-task protocol

- Plan each task to take approximately **15–30 minutes**.
- One task must have one primary objective, one acceptance result, and a focused verification step.
- If estimated work exceeds 30 minutes, split it before implementation.
- Do not bundle unrelated refactors, dependency upgrades, UI redesigns, and feature work into one task.
- Prefer one focused commit per completed micro-task.
- Before editing, state the files expected to change and the acceptance criteria.
- If the repository state differs from assumptions, stop and inspect rather than improvising a large rewrite.

## 3. Required implementation cycle

1. Inspect the relevant files and current repository state.
2. Restate the single objective and acceptance criteria.
3. Make the smallest viable change.
4. Run the narrowest relevant test or check; run broader checks when feasible.
5. Review the diff for unrelated changes, secrets, unsafe defaults, and misleading documentation.
6. Report changed files, verification performed, known limitations, and the commit or PR reference.

Never claim a test passed unless it was actually run and its result was observed. Never claim a feature exists when only documentation or a mock has been added.

## 4. Architecture and dependency discipline

- Do not choose or change the application stack without recording an Architecture Decision Record (ADR).
- Before migrating code from NEMT, inventory source files, dependencies, license notices, tests, and security concerns.
- Reuse only code that can be legally and technically migrated; preserve copyright and third-party license notices.
- Do not add dependencies for functionality already available in the existing stack without explaining the trade-off.
- Keep exchange-specific behavior behind explicit adapters and keep risk checks in trusted server-side code.
- Do not let UI state be the sole enforcement mechanism for authorization or risk limits.

## 5. Trading safety rules

- Default to **DEMO/PAPER** behavior.
- TESTNET access must be explicitly configured and clearly separated from LIVE credentials.
- **LIVE order placement must remain disabled** until a separate, documented approval and acceptance process authorizes it.
- No withdrawal capability is permitted for exchange API credentials.
- Never bypass validation, risk limits, permission checks, idempotency, reconciliation, or audit logging for convenience.
- AI output, a backtest, or a strategy score must never directly grant permission to place a real-money order.
- Unknown execution mode, stale market data, inconsistent balances, uncertain order state, or failed risk checks must block execution (fail closed).

## 6. Secrets and data

- Never commit API keys, tokens, passwords, private keys, `.env` files, account credentials, or production logs containing sensitive data.
- Use environment variables or an approved secret manager for local/server configuration; commit only sanitized examples.
- Redact secrets and sensitive account data from logs, test fixtures, screenshots, and issue reports.
- Use synthetic or public data in tests unless private data is explicitly approved and safely handled.
- Do not log full authorization headers, signatures, or secret-bearing request payloads.

## 7. Tests and acceptance

- Add or update tests for changed behavior.
- Test failure paths and safety boundaries, not only successful paths.
- For trading-related changes, include relevant cases for duplicate requests, stale data, rejected orders, partial fills, timeout/unknown state, and recovery where applicable.
- A green CI run does not by itself prove exchange integration or production readiness.
- Never test real-money order placement as part of routine development.

## 8. Documentation and Git discipline

- Keep documentation aligned with actual implementation status.
- Distinguish **planned**, **in progress**, **implemented**, and **verified**.
- Do not overwrite another contributor's work without inspection.
- Avoid force pushes, destructive cleanup, bulk renames, or broad automated edits unless explicitly approved.
- Keep pull requests reviewable and include a summary, tests, risks, and follow-up work.

## 9. Definition of done

A micro-task is complete only when:

- The stated acceptance criteria are met.
- The relevant checks have been run or the reason they could not be run is recorded.
- The diff contains no unrelated changes or secrets.
- Documentation and status statements are accurate.
- Remaining risks or limitations are reported.
