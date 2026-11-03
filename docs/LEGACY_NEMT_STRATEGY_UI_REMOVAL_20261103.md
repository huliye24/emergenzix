# Legacy NEMT strategy UI removal — 2026-11-03

## Status

Implemented and deployed to the legacy public NEMT web runtime on host alias `dev`.

## Objective

Remove the browser-based strategy authoring and backtesting workspace so the public interface behaves as a concise monitoring display. Strategy generation and controlled import remain outside this UI micro-task.

## Acceptance result

- The strategy icon is removed from desktop and mobile navigation.
- `strategies` is no longer a valid studio section.
- `StrategiesPage` is no longer imported or rendered by `StudioApp`.
- The legacy `strategy` alias is removed.
- The portfolio home no longer links to strategy management and its shortcut grid now contains only Market and PAPER.
- The legacy trading workbench no longer contains a hidden `查看策略` button.
- Legacy `?section=strategies` requests fall back to the default portfolio page after authentication.
- Existing strategy data, backtest records, backend endpoints, and source components were not deleted.

## Changed runtime files

- `/opt/nemt-app/web/src/components/trade/studio/navigation.ts`
- `/opt/nemt-app/web/src/components/trade/studio/types.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioApp.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/OverviewPage.module.css`
- `/opt/nemt-app/web/src/components/trade/studio/pages/TradingWorkbench.tsx`

The legacy runtime is not a Git checkout. The Emergenzix repository remains the product source of truth; this document records the temporary legacy deployment evidence.

## Verification performed

- `npx tsc --noEmit` — passed.
- Focused ESLint on the changed TypeScript/TSX files — 0 errors; one pre-existing unused `_` warning in `StudioApp.tsx` remains.
- `npm run build` — passed under Next.js 16.3.2; all 14 routes completed.
- Literal source searches found no remaining `strategies` reference in the six changed route/navigation files.
- Retained sections are Portfolio, Markets, PAPER Execution, and Settings.
- `nemt-web.service` and `nemt-api.service` — active.
- Local `/trade` — HTTP 200.
- Public `/trade?section=strategies` — HTTP 200 and resolves to the portfolio default after authentication.

Authenticated browser automation was not performed because the visitor code is intentionally excluded from automation logs.

## Rollback evidence

- Source backup: `/opt/nemt-app/backups/strategy-ui-before-20261103-124000.tar.gz`
- Source backup SHA-256: `d0645376e32bcc5ac013283cdcaf595d01a780e543b7477342f99cbadcac8e5e`
- Previous build: `/opt/nemt-app/backups/.next-before-strategy-ui-20261103-124000`

## Safety boundary

- This change removes only browser UI entry points.
- It does not add Codex-to-production strategy publication, automatic promotion, or order permission.
- Any future Codex-generated strategy import must preserve versioning, provenance, review, PAPER-only promotion gates, and server-side risk enforcement.
