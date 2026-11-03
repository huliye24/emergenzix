import assert from "node:assert/strict";
import test from "node:test";

process.env.EMERGENZIX_OPERATOR_SESSION_SECRET = "o".repeat(32);
delete process.env.EMERGENZIX_OPERATOR_SESSION_SECRET_FILE;
process.env.EMERGENZIX_VISITOR_SESSION_SECRET = "v".repeat(32);
delete process.env.EMERGENZIX_VISITOR_SESSION_SECRET_FILE;

const operator = await import("../src/lib/operatorSession.ts");
const { canMutateApi } = await import("../src/lib/authorization.ts");

test("operator session remains valid for 30 days and expires closed", () => {
  const issuedAt = 1_000_000;
  const token = operator.createOperatorSession(issuedAt);
  assert.equal(operator.OPERATOR_SESSION_SECONDS, 2_592_000);
  assert.equal(operator.verifyOperatorSession(token, issuedAt + 2_591_999), true);
  assert.equal(operator.verifyOperatorSession(token, issuedAt + 2_592_000), false);
});

test("operator can create backtests but cannot mutate strategies or paper orders", () => {
  assert.equal(canMutateApi("/api/v1/backtests", "POST", true), true);
  assert.equal(canMutateApi("/api/v1/strategies", "POST", true), false);
  assert.equal(canMutateApi("/api/v1/paper/orders", "POST", true), false);
});

test("viewer remains unable to create a backtest", () => {
  assert.equal(canMutateApi("/api/v1/backtests", "POST", false), false);
});
