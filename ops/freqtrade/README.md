# Freqtrade Upstream Engine

Freqtrade is pinned as a Git submodule in \`vendor/freqtrade\`. It is included for evaluation as Emergenzix's mature strategy/backtesting/dry-run engine, not as a claim that the API adapter is complete.

## Important runtime separation

The current Freqtrade \`develop\` branch requires Python 3.12 or newer and has a larger dependency set than the Emergenzix FastAPI backend. Do not install its dependencies into the same virtual environment as Emergenzix. Use the upstream Docker image or a separate virtual environment.

## Get the upstream source

\`\`\`bash
git submodule update --init --recursive
\`\`\`

## Current state

- The pinned source is available at \`vendor/freqtrade\`.
- CCXT public OHLCV is integrated into the Emergenzix API separately.
- The existing Emergenzix backtest endpoint still uses the local SMA baseline.
- A normalized Freqtrade backtest adapter is a follow-up task and must preserve the distinction between upstream engine output and Emergenzix's current result schema.
- No real-money trading or exchange credentials are enabled by this integration.

See [Upstream Components](../../docs/UPSTREAM_COMPONENTS.md) for licensing, provenance, and the adapter plan.
