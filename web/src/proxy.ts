import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { verifyVisitorSession, VISITOR_COOKIE_NAME } from "./lib/visitorSession";
import { verifyOperatorSession, OPERATOR_COOKIE_NAME } from "./lib/operatorSession";
import { canMutateApi } from "./lib/authorization";

const PUBLIC_PATHS = new Set(["/access", "/api/access", "/operator", "/api/operator", "/health"]);

export function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (PUBLIC_PATHS.has(pathname)) return NextResponse.next();
  const authenticated = verifyVisitorSession(
    request.cookies.get(VISITOR_COOKIE_NAME)?.value,
  );
  const operator = verifyOperatorSession(
    request.cookies.get(OPERATOR_COOKIE_NAME)?.value,
  );
  if (!authenticated && !operator) {
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
    if (canMutateApi(pathname, request.method, operator)) {
      return NextResponse.next();
    }
    return NextResponse.json(
      { detail: "This session cannot perform the requested mutation" },
      { status: 403 },
    );
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
