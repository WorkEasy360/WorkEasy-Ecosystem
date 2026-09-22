import { NextRequest, NextResponse } from "next/server";

/**
 * Two jobs, both request-scoped and both cheap - this is the "optimistic"
 * layer (docs: Proxy "should not be used as a full session management or
 * authorization solution"). The real checks happen server-side, per request,
 * against the Django backend (`lib/api`) - this only avoids flashing
 * protected UI at a browser that plainly has no session cookie at all, and
 * emits a strict, nonce-based CSP (Phase 4 Task 34 - the CSP trigger).
 *
 * Must match Django's own session cookie name exactly
 * (backend/config/settings/base.py: SESSION_COOKIE_NAME). This code never
 * reads the cookie's *value* - it is HttpOnly and opaque to this process
 * too, by design - only whether one was sent at all.
 */
const SESSION_COOKIE = "w360_session";

const PUBLIC_PATHS = ["/login"];

export function proxy(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";

  const hasSession = request.cookies.has(SESSION_COOKIE);
  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  if (!hasSession && !isPublic && pathname !== "/") {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Carry a resume token straight through (it is opaque to us) so a
    // product deep-link that landed here unauthenticated still resumes
    // correctly once the real login page finishes (Task 31/32).
    const resume = request.nextUrl.searchParams.get("resume");
    url.search = resume ? `?resume=${encodeURIComponent(resume)}` : "";
    return NextResponse.redirect(url);
  }

  const csp = `
    default-src 'self';
    script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""};
    style-src 'self' ${isDev ? "'unsafe-inline'" : `'nonce-${nonce}'`};
    img-src 'self' data:;
    font-src 'self';
    connect-src 'self';
    object-src 'none';
    base-uri 'self';
    form-action 'self';
    frame-ancestors 'none';
    upgrade-insecure-requests;
  `
    .replace(/\s{2,}/g, " ")
    .trim();

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  // Belt-and-braces alongside frame-ancestors, matching the backend's own
  // SecurityHeadersMiddleware convention of stacking both.
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
