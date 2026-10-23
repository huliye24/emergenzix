# Upstream Components

Emergenzix should reuse mature quantitative-trading components instead of reimplementing commodity infrastructure. Upstream code remains identifiable, pinned, and subject to its own license and release cadence.

## 1. Freqtrade — strategy and trading engine

- Upstream: https://github.com/freqtrade/freqtrade
- License: GPL-3.0
- Integration: Git submodule at \`vendor/freqtrade\`
- Pinned upstream commit: \`c051e9065c5b677f03817dd1f83cb980af1ed684\`
- Primary capabilities: strategy framework, historical-data downloads, backtesting, backtest analysis, dry-run trading, exchange integrations, and optional FreqAI workflows.
- Runtime boundary: keep Freqtrade in its own environment or container. The current upstream branch requires Python 3.12+, while the Emergenzix API currently supports Python 3.11+.

The submodule is a pinned upstream source reference; it is **not yet the active engine behind** \`POST /api/v1/backtests\`. The existing Emergenzix SMA implementation remains the compatibility baseline until a dedicated adapter is implemented and tested by Codex/AWS. Do not silently switch engines or claim equivalent metrics before comparing their fill, fee, candle, and trade semantics.

### Fetch the pinned source

From a clone of Emergenzix:

\`\`\`bash
git submodule update --init --recursive
\`\`\`

To inspect the exact upstream version:

\`\`\`bash
git -C vendor/freqtrade rev-parse HEAD
git -C vendor/freqtrade show -s --format='%H %cs %s' HEAD
\`\`\`

Do not update the submodule to the latest upstream commit without reviewing release notes, the license, Python requirements, and the adapter contract.

## 2. CCXT — public exchange market data

- Upstream: https://github.com/ccxt/ccxt
- License: MIT
- Integration: Python package dependency in \`backend/requirements.txt\`
- Primary capabilities: normalized exchange interfaces and OHLCV retrieval.

The first integration exposes \`GET /api/v1/market-data/ohlcv\`. It only uses public market-data methods; it does not submit orders and does not require exchange credentials. The endpoint uses an explicit exchange allowlist, bounded timeframes and candle limits, UTC timestamps, and basic OHLCV validation.

Example:

\`\`\`bash
curl 'http://127.0.0.1:8000/api/v1/market-data/ohlcv?exchange=binance&symbol=BTCUSDT&timeframe=1h&limit=120'
\`\`\`

The exchange's newest candle may still be forming. Public APIs can be rate-limited or unavailable, and symbol availability differs by exchange.

## 3. License policy

Emergenzix is licensed under GPL-3.0. Freqtrade's GPL-3.0 license is aligned with the repository license. CCXT is MIT-licensed and can be used as a dependency while retaining its upstream notices as required.

Before adding another source repository or copying files into Emergenzix:

1. Confirm the upstream license from the repository itself.
2. Record the exact upstream URL, commit or release, license, and local modifications.
3. Preserve applicable copyright and license notices.
4. Do not vendor projects with missing, unclear, or incompatible licensing until reviewed.
5. Prefer package dependencies or Git submodules over copying large code trees.
6. Have Codex review dependency conflicts and run tests after integration; do not claim a component is working until observed evidence exists.

## 4. Planned integration order

1. Use CCXT for public historical candles and record data provenance with each evaluation.
2. Run Freqtrade as an isolated optional engine rather than installing its dependency stack into the Emergenzix API environment.
3. Define an adapter that maps strategy versions and OHLCV datasets into Freqtrade's data/strategy formats and normalizes exported results.
4. Compare the adapter's output against known fixtures, including fees, order timing, open-position handling, and drawdown calculation.
5. Keep the existing engine as a fallback until the adapter passes Codex/AWS verification.
6. Consider exchange TESTNET only after paper workflows, authentication, and risk controls have been reviewed. LIVE trading remains disabled.

## 5. Do not import everything blindly

Freqtrade includes a complete bot, configuration system, database, API server, strategy lifecycle, and UI. Emergenzix should reuse the engine and data capabilities while retaining a distinct product boundary. Avoid duplicating Freqtrade's internals in the Emergenzix API or maintaining a long-lived fork unless a specific, documented change requires it.
