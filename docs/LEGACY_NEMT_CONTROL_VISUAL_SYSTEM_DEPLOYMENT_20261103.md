# Legacy NEMT control visual-system deployment — 2026-11-03

Status: **implemented and deployed** to `https://postyouth.club/trade?section=api`.

## Objective

Bring the backtest control screen back into the existing Emergenzix visual system while preserving the data-lake experiment workflow and adding explicit position-direction controls.

## Visual alignment

- Removed the custom hand-drawn strategy and control SVG set.
- Reused the existing `StudioIcon` system for every strategy and action.
- Mapped B&H, MA, RSI, BB, and NEMT to the shared trend, market, activity, grid, and CPU icons.
- Reused the Overview screen's editorial palette: black primary rules, light-grey secondary rules, white surfaces, cyan emphasis, and mono data labels.
- Removed the rounded SaaS-card treatment and large shadows.
- Kept the control surface centered at a bounded width and reduced the workbench, chart, metric, and empty-state heights.
- Preserved responsive strategy navigation and parameter editing on narrow screens.

## Position controls retained

- Initial capital presets: 1,000 / 5,000 / 10,000 / 50,000 / 100,000 USDT.
- Default direction: long-only.
- MA, RSI, BB, and NEMT: long-only, short-only, or strategy-directed signals.
- B&H: long-only; unsupported controls remain disabled.
- Direction filtering occurs in the backtest engine rather than only in UI state.

## Verification

- `npx tsc --noEmit`: passed.
- Focused ESLint for the active control page: passed with 0 errors and 0 warnings.
- `npm run build`: passed with Next.js 16.3.2.
- Direction smoke test from the preceding runtime revision: default long-only produced only LONG trades; short-only produced only SHORT trades; strategy mode produced both directions on the same MA fixture.
- `nemt-web.service`: active.
- `nemt-api.service`: active.
- Public control URL: HTTP 200.
- Source inspection confirmed that no `Glyph` custom-icon component remains in the active page.

Automated post-login browser inspection was not repeated because the browser-control layer could not reliably establish the existing Chrome tab URL in the preceding attempt. The production build, service, and public endpoint checks passed.

## Runtime files changed

- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.tsx`
- `/opt/nemt-app/web/src/components/trade/studio/pages/ControlPage.module.css`
- `/opt/nemt-app/web/src/components/trade/studio/backtest/engine.ts` (direction filtering from the preceding unrecorded revision)

## Backup

- Visual source backup: `/opt/nemt-app/backups/control-panel-v5-before-20261103-180500.tar.gz`
- Visual source backup SHA-256: `6534b91211a09569bc46809dc9e4e95d97e7b1f2b5775e6b99f44acf3be07ef9`
- Previous visual build: `/opt/nemt-app/backups/.next-before-control-panel-v5-20261103-180500`
- Direction-control source backup: `/opt/nemt-app/backups/control-panel-v4-before-20261103-170500.tar.gz`
- Direction-control source backup SHA-256: `b6d235dfffb5cdf13f6e270671e40916b8e51ddc01976313a5642960df3d2661`

## Safety boundary

The control remains a PAPER research interface. Long/short selection only affects historical backtest signal filtering and does not place or authorize exchange orders.
