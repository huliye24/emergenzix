# Legacy NEMT data-lake backtest control deployment — 2026-11-03

Status: **implemented and deployed** to `https://postyouth.club/trade?section=api`.

## Objective

Turn the second legacy screen into an icon-led strategy workbench that can run a selected strategy and parameter snapshot against an explicit BTC date range from the immutable S3 market-data lake.

## Implemented behavior

- Removed the large page title, explanatory copy, search field, and unavailable AI/ML cards.
- Added icon selectors for NEMT, moving-average crossover, RSI reversion, and Bollinger breakout.
- Added explicit start/end dates, BTCUSDT, 1h/4h/1d timeframe selection, initial capital, and one Run action.
- Added an editable parameter panel with per-strategy validation and reset.
- Added a price chart, ending equity, return, drawdown, Sharpe, win rate, and trade-count results.
- Results retain the exact strategy parameter snapshot and show the data-lake snapshot ID, requested period, returned/expected bars, and omitted incomplete Gold buckets.
- The existing client backtest engine applies fixed 5 bps fees and 5 bps slippage.
- No exchange credential, account mutation, PAPER order, TESTNET order, or LIVE order path was added.

## Data-lake integration

The web host has no S3 credentials. A reproducible export on the Ningxia CPU host reads the current BTC v2 pointer, verifies every selected Parquet object's SHA-256, and produces read-only compressed Gold caches. The web API filters these caches by requested date range and fails closed when the requested symbol/timeframe is not available.

- Source snapshot: `1382f2ab1a09c809ea191683feb4c750bdc8850c562cac7d2c49e82b33839a81`
- Cache directory: `/srv/emergenzix-market-data-cache`
- BTC 1h: 80,587 rows; SHA-256 `4fb13e37eb18487d8e8cb01780c9a652061149aad03e9eb5713503cdbbde628d`
- BTC 4h: 20,121 rows; SHA-256 `0727cc53a1c0e53c2d9c81dbd23f3eebdde5706c5f931c6b0959b2c37b6b6fa9`
- BTC 1d: 3,331 rows; SHA-256 `b2de64d6e4750f4a1af178b4faf337f81dd386becc4f4c8bcb10d9f73d269472`
- Reproducible exporter: `ops/export_market_data_web_cache.py`

The cache files contain public market data and provenance only. They contain no S3 credentials or exchange secrets.

## Acceptance verification

- `npx tsc --noEmit`: passed.
- Focused ESLint on the control page and CLI route: passed with 0 errors and 0 warnings.
- `npm run build`: passed with Next.js 16.3.2.
- `nemt-web.service`: active.
- `nemt-api.service`: active.
- Public `/trade?section=api`: HTTP 200.
- Exact API test: BTCUSDT, 1d, 2023-01-01 through 2024-12-31.
  - Source: `s3_data_lake_gold`.
  - Returned: 730 canonical Gold candles.
  - Expected calendar buckets: 731.
  - Explicitly omitted incomplete Gold buckets: 1.
- Unauthenticated browser access reaches the visitor-code gate as expected; post-login visual interaction was not automated because the visitor code was not read or exposed for testing.

## Runtime files changed

- `/opt/nemt-app/web/src/app/api/cli/route.ts`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.module.css`

The legacy deployment directory is not a Git checkout; this document and the exporter are the repository-side evidence.

## Backup

- Source backup: `/opt/nemt-app/backups/control-panel-v2-before-20261103-151500.tar.gz`
- Source backup SHA-256: `4422c24396db12a7f2f270970f258c5ccf1562d673d4ad208998a9e8dcc80cff`
- Previous build: `/opt/nemt-app/backups/.next-before-control-panel-v2-20261103-151500`

## Known limitations

- This deployed slice currently exposes BTCUSDT only, at 1h, 4h, and 1d.
- Parameter edits are held in the browser for the current session; they are not yet persisted as immutable server-side strategy versions.
- The cache is pinned to the recorded snapshot and is not yet refreshed automatically after each data-lake pointer advance.
- The result is a PAPER research experiment and cannot grant LIVE execution permission.
