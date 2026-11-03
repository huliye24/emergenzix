import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";

export const VISITOR_COOKIE_NAME = "emergenzix_visitor";
export const VISITOR_SESSION_SECONDS = 30 * 24 * 60 * 60;

type VisitorPayload = {
  exp: number;
  nonce: string;
  role: "viewer";
};

function sessionSecret(): string | null {
  const value = secretValue(
    "EMERGENZIX_VISITOR_SESSION_SECRET",
    "EMERGENZIX_VISITOR_SESSION_SECRET_FILE",
  );
  return value.length >= 32 ? value : null;
}

export function visitorAccessConfigured(): boolean {
  return (
    secretValue("EMERGENZIX_VISITOR_CODE", "EMERGENZIX_VISITOR_CODE_FILE").length >= 12 &&
    sessionSecret() !== null
  );
}

export function visitorCodeMatches(provided: string): boolean {
  const expected = secretValue(
    "EMERGENZIX_VISITOR_CODE",
    "EMERGENZIX_VISITOR_CODE_FILE",
  );
  if (expected.length < 12) return false;
  return constantTimeTextEqual(provided, expected);
}

export function createVisitorSession(nowSeconds = Math.floor(Date.now() / 1000)): string {
  const secret = sessionSecret();
  if (!secret) throw new Error("Visitor session secret is not configured");
  const payload: VisitorPayload = {
    exp: nowSeconds + VISITOR_SESSION_SECONDS,
    nonce: randomBytes(16).toString("base64url"),
    role: "viewer",
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = createHmac("sha256", secret).update(encoded).digest("base64url");
  return encoded + "." + signature;
}

export function verifyVisitorSession(
  token: string | undefined,
  nowSeconds = Math.floor(Date.now() / 1000),
): boolean {
  const secret = sessionSecret();
  if (!secret || !token) return false;
  const [encoded, suppliedSignature, extra] = token.split(".");
  if (!encoded || !suppliedSignature || extra !== undefined) return false;
  const expectedSignature = createHmac("sha256", secret)
    .update(encoded)
    .digest("base64url");
  if (!constantTimeTextEqual(suppliedSignature, expectedSignature)) return false;
  try {
    const payload = JSON.parse(
      Buffer.from(encoded, "base64url").toString("utf8"),
    ) as Partial<VisitorPayload>;
    return payload.role === "viewer" && Number.isInteger(payload.exp) && payload.exp! > nowSeconds;
  } catch {
    return false;
  }
}
function constantTimeTextEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  if (leftBuffer.length !== rightBuffer.length) {
    timingSafeEqual(leftBuffer, leftBuffer);
    return false;
  }
  return timingSafeEqual(leftBuffer, rightBuffer);
}

function secretValue(valueName: string, fileName: string): string {
  const direct = process.env[valueName]?.trim() ?? "";
  const filePath = process.env[fileName]?.trim() ?? "";
  if (direct && filePath) return "";
  if (!filePath) return direct;
  try {
    return readFileSync(/* turbopackIgnore: true */ filePath, "utf8").trim();
  } catch {
    return "";
  }
}

