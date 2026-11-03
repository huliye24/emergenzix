import { NextRequest, NextResponse } from "next/server";
import {
  createOperatorSession,
  operatorAccessConfigured,
  operatorCodeMatches,
  OPERATOR_COOKIE_NAME,
  OPERATOR_SESSION_SECONDS,
  verifyOperatorSession,
} from "../../../lib/operatorSession";

const attempts = new Map<string, { count: number; resetAt: number }>();
const MAX_ATTEMPTS = 5;
const WINDOW_MS = 15 * 60 * 1000;

function clientKey(request: NextRequest): string {
  return request.headers.get("x-real-ip")
    ?? request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    ?? "unknown";
}

export async function POST(request: NextRequest) {
  if (!operatorAccessConfigured()) {
    return NextResponse.json({ detail: "Operator access is not configured" }, { status: 503 });
  }
  const now = Date.now();
  const key = clientKey(request);
  const previous = attempts.get(key);
  const current = !previous || previous.resetAt <= now
    ? { count: 0, resetAt: now + WINDOW_MS }
    : previous;
  if (current.count >= MAX_ATTEMPTS) {
    return NextResponse.json({ detail: "Too many attempts. Try again later." }, { status: 429 });
  }
  const payload = await request.json().catch(() => ({})) as { code?: unknown };
  const code = typeof payload.code === "string" ? payload.code.trim() : "";
  if (!operatorCodeMatches(code)) {
    attempts.set(key, { ...current, count: current.count + 1 });
    return NextResponse.json({ detail: "操作员码无效" }, { status: 401 });
  }
  attempts.delete(key);
  const response = NextResponse.json({ authenticated: true, role: "research_operator" });
  response.cookies.set(OPERATOR_COOKIE_NAME, createOperatorSession(), {
    httpOnly: true,
    maxAge: OPERATOR_SESSION_SECONDS,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}

export function GET(request: NextRequest) {
  const authenticated = verifyOperatorSession(request.cookies.get(OPERATOR_COOKIE_NAME)?.value);
  return NextResponse.json({ authenticated, role: authenticated ? "research_operator" : null });
}

export function DELETE() {
  const response = NextResponse.json({ authenticated: false });
  response.cookies.set(OPERATOR_COOKIE_NAME, "", {
    httpOnly: true,
    maxAge: 0,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
