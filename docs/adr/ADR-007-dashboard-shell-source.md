# ADR-007: Dashboard shell source and adaptation boundary

## Status

Accepted on 2026-11-03.

## Context

The product needs a stable application shell before its live monitor, backtest, and shadow-paper screens receive further visual work. Repeated page-level redesigns produced inconsistent navigation, spacing, and responsive behavior.

The current web application uses Next.js 16, React 19, and TypeScript. Tailwind is available in the legacy runtime, but the product repository should not adopt a large UI dependency or replace the application stack merely to obtain navigation chrome.

## Decision

Use the structural pattern of the MIT-licensed [`shadcn/ui` dashboard-01 block](https://github.com/shadcn-ui/ui/tree/main/apps/v4/registry/new-york-v4/blocks/dashboard-01) as the reference for the Emergenzix shell:

- persistent, collapsible desktop sidebar;
- clear active navigation state;
- compact sticky page header;
- single content viewport;
- responsive horizontal navigation on narrow screens.

The implementation is local React and CSS. It reuses Emergenzix icons, tokens, data, and authorization behavior. It does not install the complete shadcn registry block or its data-table, chart, drag-and-drop, Radix, or TanStack dependencies.

The primary destinations are limited to:

1. Live monitor
2. Backtest
3. Shadow PAPER

Strategy editing and activity remain content within those workflows rather than top-level shell destinations.

## Alternatives considered

- **TailAdmin:** MIT and compatible with Next.js/Tailwind, but the free template brings a much larger page and chart surface than the product needs.
- **Tabler:** mature and MIT, but its Bootstrap-based shell would introduce a second styling system. Its optional bundled chart ecosystem also requires separate license review.
- **Tremor:** Apache-2.0 and strong for dashboard components, but it is a component collection rather than the required application shell.

## Consequences

- The three product modes gain one obvious, testable navigation surface.
- Desktop users may collapse the sidebar; mobile users retain direct access to all three modes.
- No new runtime dependency is introduced.
- The MIT source reference and notice must remain documented.
- Migrating legacy page bodies into this shell remains separate work and must preserve trading safety boundaries.

## Verification

- TypeScript typecheck.
- Next.js production build.
- Keyboard-visible focus and `aria-current` on navigation items.
- Desktop expanded/collapsed navigation and narrow-screen navigation remain reachable.
