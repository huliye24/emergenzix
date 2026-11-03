import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";

export const OPERATOR_COOKIE_NAME = "emergenzix_operator";
export const OPERATOR_SESSION_SECONDS = 30 * 24 * 60 * 60;

type OperatorPayload = { exp: number; nonce: string; role: "research_operator" };

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

function constantTimeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  if (a.length !== b.length) {
    timingSafeEqual(a, a);
    return false;
  }
  return timingSafeEqual(a, b);
}

function sessionSecret(): string | null {
  const value = secretValue(
    "EMERGENZIX_OPERATOR_SESSION_SECRET",
    "EMERGENZIX_OPERATOR_SESSION_SECRET_FILE",
  );
  return value.length >= 32 ? value : null;
}

export function operatorAccessConfigured(): boolean {
  return secretValue("EMERGENZIX_OPERATOR_CODE", "EMERGENZIX_OPERATOR_CODE_FILE").length >= 16
    && sessionSecret() !== null;
}

export function operatorCodeMatches(provided: string): boolean {
  const expected = secretValue("EMERGENZIX_OPERATOR_CODE", "EMERGENZIX_OPERATOR_CODE_FILE");
  return expected.length >= 16 && constantTimeEqual(provided, expected);
}

export function createOperatorSession(now = Math.floor(Date.now() / 1000)): string {
  const secret = sessionSecret();
  if (!secret) throw new Error("Operator session secret is not configured");
  const payload: OperatorPayload = {
    exp: now + OPERATOR_SESSION_SECONDS,
    nonce: randomBytes(16).toString("base64url"),
    role: "research_operator",
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = createHmac("sha256", secret).update(encoded).digest("base64url");
  return `${encoded}.${signature}`;
}

export function verifyOperatorSession(
  token: string | undefined,
  now = Math.floor(Date.now() / 1000),
): boolean {
  const secret = sessionSecret();
  if (!secret || !token) return false;
  const [encoded, signature, extra] = token.split(".");
  if (!encoded || !signature || extra !== undefined) return false;
  const expected = createHmac("sha256", secret).update(encoded).digest("base64url");
  if (!constantTimeEqual(signature, expected)) return false;
  try {
    const payload = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as Partial<OperatorPayload>;
    return payload.role === "research_operator" && Number.isInteger(payload.exp) && payload.exp! > now;
  } catch {
    return false;
  }
}
