# Legacy NEMT personal-space removal — 2026-11-03

## Status

Implemented and deployed to the legacy public NEMT web runtime on host alias `dev`.

## Objective

Remove the decorative personal-space/journal interface from the product navigation so the workspace remains focused on quantitative trading tools.

## Acceptance result

- The desktop user-icon entry for `个人空间` is removed.
- `settings` is no longer a valid studio section.
- The personal-space render branch is removed from `StudioApp`.
- The gear-icon system settings page remains available through the `api` section.
- Legacy `?section=settings` requests fall back to the default portfolio page because the value is no longer accepted.
- No journal data, backend state, or component assets were deleted.

## Changed runtime files

- `/opt/nemt-app/web/src/components/trade/studio/navigation.ts`
- `/opt/nemt-app/web/src/components/trade/studio/types.ts`
- `/opt/nemt-app/web/src/components/trade/studio/StudioApp.tsx`

The legacy runtime is not a Git checkout. The Emergenzix repository remains the product source of truth; this document records the temporary legacy deployment evidence.

## Verification performed

- `npx tsc --noEmit` — passed.
- Focused ESLint on the three changed files — 0 errors; one pre-existing unused `_` warning in `StudioApp.tsx` remains.
- `npm run build` — passed under Next.js 16.3.2; all 14 routes completed.
- Source inspection confirmed no `settings` section in `types.ts`, no personal-space navigation item, and no personal-space render branch.
- The retained gear settings route still renders `SettingsPage` with `mode="api"`.
- `nemt-web.service` and `nemt-api.service` — active.
- Local `/trade` — HTTP 200.
- Public `/trade?section=settings` — HTTP 200 and will resolve to the portfolio default after authentication.

Authenticated browser automation was not performed because the visitor code is intentionally excluded from automation logs.

## Rollback evidence

- Source backup: `/opt/nemt-app/backups/personal-space-before-20261103-123000.tar.gz`
- Source backup SHA-256: `c057a9bfd4004dcc9136624b9714ce312e910c60c169d6f5396f39ffae019199`
- Previous build: `/opt/nemt-app/backups/.next-before-personal-space-20261103-123000`

## Product-design direction

Future UI micro-tasks should prefer icons and measured data over explanatory copy, keep labels short, and avoid decorative non-trading destinations. Accessibility labels must remain even when visible text is reduced.
