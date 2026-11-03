import assert from "node:assert/strict";
import test from "node:test";

process.env.EMERGENZIX_VISITOR_SESSION_SECRET = "s".repeat(32);
delete process.env.EMERGENZIX_VISITOR_SESSION_SECRET_FILE;

const {
  createVisitorSession,
  verifyVisitorSession,
  VISITOR_SESSION_SECONDS,
} = await import("../src/lib/visitorSession.ts");

test("visitor session remains valid for 30 days and expires closed", () => {
  const issuedAt = 1_000_000;
  const token = createVisitorSession(issuedAt);

  assert.equal(VISITOR_SESSION_SECONDS, 2_592_000);
  assert.equal(verifyVisitorSession(token, issuedAt + VISITOR_SESSION_SECONDS - 1), true);
  assert.equal(verifyVisitorSession(token, issuedAt + VISITOR_SESSION_SECONDS), false);
});

test("visitor session rejects a modified signature", () => {
  const token = createVisitorSession(1_000_000);
  const [payload, signature] = token.split(".");
  const replacement = signature.endsWith("a") ? "b" : "a";
  const modified = payload + "." + signature.slice(0, -1) + replacement;

  assert.equal(verifyVisitorSession(modified, 1_000_001), false);
});
