# NEMT Product-Code Inventory for Emergenzix

**Status:** initial repository inspection completed; no source code has been copied by this change.

## Verified source structure

Source repository: [huliye24/NEMT-Simulator2](https://github.com/huliye24/NEMT-Simulator2)

| Source path | Observed responsibility | Initial decision | Notes |
| --- | --- | --- | --- |
| \`web/package.json\` | Next.js 16 / React 19 web application and build scripts | Adapt concepts; do not bulk-copy | Existing frontend is substantial and branded for NEMT. Emergenzix needs its own interface and navigation. |
| \`web/src/app/page.tsx\` | Current dashboard entry; loads market state, symbols, report, and portfolio | Reuse API-contract ideas only | Imports the existing \`EmergentixHome\` component and NEMT data helpers. Product naming and data contracts need a deliberate rewrite. |
| \`web/src/components/EmergentixHome.tsx\` | Existing dashboard UI | Review selectively later | Name and UI assumptions predate the independent Emergenzix repository; no file copied. |
| \`web/src/lib/api.ts\` | Frontend API/data access | Review selectively later | Must be audited for mock/demo assumptions and server endpoints before any reuse. |
| \`backend/trade_main.py\` | Existing API process entry point | Review selectively later | Uses Uvicorn and the existing \`trade_app\` package. |
| \`backend/trade_app/main.py\` | Existing FastAPI application | Do not copy wholesale | Large application module with broader responsibilities than the initial Emergenzix core. |
| \`backend/trade_app/accounts.py\`, \`auth.py\`, \`security.py\` | Account, authentication, and security logic | Security review required before reuse | Authentication and account handling must not be migrated without reviewing assumptions and tests. |
| \`backend/trade_app/binance_spot.py\`, \`orders.py\`, \`risk.py\` | Exchange adapter, order workflows, and risk controls | Do not enable or copy blindly | These are high-impact execution paths. Emergenzix starts with isolated paper execution and no live exchange adapter. |
| \`backend/tests/\` | Existing backend tests | Use as reference only | Tests are evidence about existing behavior, not proof of Emergenzix compatibility. |
| \`backend/trade_requirements.txt\` | Existing API runtime dependencies | Use as a reference | Emergenzix declares its own minimal dependencies and tests. |

## Initial migration decision

Emergenzix will start with a **small, independent FastAPI backend** rather than copying the complete NEMT application. The first vertical slice provides:

- Health and product overview endpoints
- Strategy creation and listing
- A deterministic long-only SMA-crossover backtest
- Persistent backtest results and equity/trade records
- Paper account, positions, and simulated order history
- Server-side paper-order limits and no-short-selling checks
- Tests using an isolated temporary SQLite database

The first slice intentionally has no exchange connection, no live-order endpoint, no authentication claim, and no production deployment claim. It is a development baseline, not a production-ready financial service.

## License and dependency review

Before moving any source code from NEMT, inspect the source repository's license and the relevant file headers, retain copyright notices, and verify third-party dependency compatibility with Emergenzix's GPL-3.0 license. This implementation is newly authored for Emergenzix and does not bulk-copy the listed source files.

## Remaining work

1. Run the backend tests in CI and on the AWS development environment.
2. Review the first implementation and address test findings.
3. Decide the responsive web/PWA stack after the backend contract stabilizes.
4. Review authentication, persistence, deployment, monitoring, and security requirements before exposing the service to the public internet.
