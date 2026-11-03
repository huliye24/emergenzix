import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { verifyVisitorSession, VISITOR_COOKIE_NAME } from "./lib/visitorSession";

const PUBLIC_PATHS = new Set(["/access", "/api/access", "/health"]);

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();
  const authenticated = verifyVisitorSession(
    request.cookies.get(VISITOR_COOKIE_NAME)?.value,
  );
  if (!authenticated) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ detail: "Visitor authentication required" }, { status: 401 });
    }
    const target = new URL("/access", request.url);
    target.searchParams.set("returnTo", pathname + request.nextUrl.search);
    return NextResponse.redirect(target);
  }
  if (
    pathname.startsWith("/api/v1/") &&
    !["GET", "HEAD", "OPTIONS"].includes(request.method)
  ) {
    return NextResponse.json(
      { detail: "Visitor sessions are read-only" },
      { status: 403 },
    );
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
