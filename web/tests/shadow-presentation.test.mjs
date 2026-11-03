import assert from "node:assert/strict";
import test from "node:test";

const { buildEquityPolyline, shadowReturnPct } = await import(
  "../src/lib/shadowPresentation.ts"
);

test("shadow equity polyline is chronological and handles a flat first point", () => {
  const points = buildEquityPolyline([
    { completed_at: "2026-11-03T08:00:00Z", equity: 10_100 },
    { completed_at: "2026-11-03T04:00:00Z", equity: 10_000 },
  ], 100, 50);
  assert.equal(points, "12.00,38.00 88.00,12.00");
  assert.equal(
    buildEquityPolyline([{ completed_at: "2026-11-03T08:00:00Z", equity: 10_000 }], 100, 50),
    "12,25 88,25",
  );
});

test("shadow return uses the persisted initial capital", () => {
  assert.ok(Math.abs(shadowReturnPct(10_000, 10_250) - 2.5) < 1e-9);
  assert.equal(shadowReturnPct(0, 10_250), 0);
});
