import { NextRequest, NextResponse } from "next/server";
import {
  createVisitorSession,
  verifyVisitorSession,
  visitorAccessConfigured,
  visitorCodeMatches,
  VISITOR_COOKIE_NAME,
  VISITOR_SESSION_SECONDS,
} from "../../../lib/visitorSession";

const attempts = new Map<string, { count: number; resetAt: number }>();
const MAX_ATTEMPTS = 5;
const ATTEMPT_WINDOW_MS = 15 * 60 * 1000;
const MAX_TRACKED_CLIENTS = 10_000;

function clientKey(request: NextRequest): string {
  return (
    request.headers.get("x-real-ip") ??
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}

export async function POST(request: NextRequest) {
  if (!visitorAccessConfigured()) {
    return NextResponse.json(
      { detail: "Visitor access is not configured" },
      { status: 503 },
    );
  }
  const now = Date.now();
  const key = clientKey(request);
  const previous = attempts.get(key);
  const current = !previous || previous.resetAt <= now
    ? { count: 0, resetAt: now + ATTEMPT_WINDOW_MS }
    : previous;
  if (current.count >= MAX_ATTEMPTS) {
    return NextResponse.json(
      { detail: "Too many attempts. Try again later." },
      { status: 429 },
    );
  }
  let code = "";
  try {
    const payload = (await request.json()) as { code?: unknown };
    code = typeof payload.code === "string" ? payload.code.trim() : "";
  } catch {
    return NextResponse.json({ detail: "Invalid request" }, { status: 400 });
  }
  if (!visitorCodeMatches(code)) {
    for (const [trackedKey, tracked] of attempts) {
      if (tracked.resetAt <= now) attempts.delete(trackedKey);
    }
    if (!attempts.has(key) && attempts.size >= MAX_TRACKED_CLIENTS) {
      return NextResponse.json(
        { detail: "Too many attempts. Try again later." },
        { status: 429 },
      );
    }
    attempts.set(key, { ...current, count: current.count + 1 });
    return NextResponse.json({ detail: "访问者码无效" }, { status: 401 });
  }
  attempts.delete(key);
  const response = NextResponse.json({ authenticated: true, role: "viewer" });
  response.cookies.set(VISITOR_COOKIE_NAME, createVisitorSession(), {
    httpOnly: true,
    maxAge: VISITOR_SESSION_SECONDS,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
export function GET(request: NextRequest) {
  const authenticated = verifyVisitorSession(
    request.cookies.get(VISITOR_COOKIE_NAME)?.value,
  );
  return NextResponse.json({ authenticated, role: authenticated ? "viewer" : null });
}

export function DELETE() {
  const response = NextResponse.json({ authenticated: false });
  response.cookies.set(VISITOR_COOKIE_NAME, "", {
    httpOnly: true,
    maxAge: 0,
    path: "/",
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}

