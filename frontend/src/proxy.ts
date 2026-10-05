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

/**
 * Reachable without a session cookie. `/invitations/accept` is where an
 * invitee lands from the emailed link - often before they have an account at
 * all (the token travels in the URL fragment, which never reaches this code).
 * Phase 9: the public site (`/` is handled below) and the self-service account
 * pages - signup, and the verification and reset links, whose tokens are in
 * the fragment too. Phase 10: `/owner-invitations/accept`, where a customer's
 * owner activates the account a partner set up for them.
 */
const PUBLIC_PATHS = [
  "/login",
  "/invitations/accept",
  "/owner-invitations/accept",
  "/products",
  "/pricing",
  "/services",
  "/contact",
  "/signup",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/**
 * The policy, as a pure function so it can be asserted without booting a
 * server (`src/proxy.test.ts`). Every clause below is load-bearing.
 *
 * **`script-src`: nonce + `'strict-dynamic'`, and therefore no static
 * prerendering.** `'strict-dynamic'` makes a CSP3 browser ignore `'self'`
 * and every host-source in this directive, so a script tag runs only with
 * the matching nonce. Next.js injects that nonce during *server* rendering,
 * which is why `app/layout.tsx` forces dynamic rendering for the whole route
 * tree - a prerendered page carries no nonce and every one of its scripts is
 * blocked.
 *
 * **`style-src`: `'unsafe-inline'`, deliberately, and a nonce would be
 * worse than useless here.** This app renders inline `style` *attributes*
 * (~70 of them across the pages and the shell). A nonce cannot be attached
 * to an attribute, and CSP3's "does a source list allow all inline
 * behavior?" algorithm returns *Does Not Allow* for inline style the moment
 * the source list contains any nonce or hash - so `style-src 'self'
 * 'nonce-X'` blocks every one of those attributes, and adding
 * `'unsafe-inline'` alongside the nonce does not rescue them, because the
 * nonce's presence is what disqualifies the list. `style-src-attr` would
 * express this precisely but is not implemented in every target browser, so
 * a page relying on it renders unstyled wherever it is unsupported.
 *
 * The residual risk is CSS-injection-based exfiltration, which needs a
 * markup-injection vector this app does not have (no `dangerouslySetInnerHTML`
 * anywhere, asserted by `src/proxy.test.ts`'s sibling check in the backend
 * architecture suite) - and `script-src` stays strict regardless. Recorded
 * rather than papered over: the way to earn a strict `style-src` back is to
 * move those attributes into the CSS modules this app already uses, not to
 * ship a policy that only looks strict.
 */
export function contentSecurityPolicy(nonce: string, isDev: boolean): string {
  return `
    default-src 'self';
    script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""};
    style-src 'self' 'unsafe-inline';
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
}

/**
 * Phase 12, D-035 decision B (ADR-039): a partner's custom domain serves the
 * branded landing (`/login`) and nothing else of this app. Its `/` goes to that
 * landing; every other page goes to the same path on the canonical host,
 * because no page that could hold a session - or ask for a credential - may be
 * served on a host a partner controls. Returns the redirect target, or `null`
 * to serve the request. `canonical` empty (development, tests) means every
 * host is canonical.
 */
export function offCanonicalRedirect(
  host: string,
  pathname: string,
  search: string,
  canonical: string,
): string | null {
  const canonicalHost = canonical.trim().toLowerCase();
  const requestHost = host.split(":")[0].trim().toLowerCase();
  if (!canonicalHost || !requestHost || requestHost === canonicalHost) return null;
  if (pathname === "/login") return null;
  if (pathname === "/") return "/login";
  return `https://${canonicalHost}${pathname}${search}`;
}

/**
 * Headers the backend already sets on `/api` responses and HTML pages lacked
 * (Phase 12, G-21). HSTS only outside development, and with
 * `includeSubDomains; preload` only on the canonical host - a partner's own
 * domain gets the plain form, since its subdomains are the partner's.
 */
export function transportHeaders(isDev: boolean, onCanonicalHost: boolean): Record<string, string> {
  const headers: Record<string, string> = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  };
  if (!isDev) {
    headers["Strict-Transport-Security"] = onCanonicalHost
      ? "max-age=63072000; includeSubDomains; preload"
      : "max-age=31536000";
  }
  return headers;
}

export function proxy(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";
  const canonical = process.env.W360_CANONICAL_HOST ?? "";
  const host = request.headers.get("host") ?? "";

  const handoff = offCanonicalRedirect(host, pathname, request.nextUrl.search, canonical);
  if (handoff) {
    if (handoff.startsWith("/")) {
      const url = request.nextUrl.clone();
      url.pathname = handoff;
      url.search = "";
      return NextResponse.redirect(url, 302);
    }
    return NextResponse.redirect(handoff, 302);
  }

  const hasSession = request.cookies.has(SESSION_COOKIE);
  const isPublic = isPublicPath(pathname);

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

  const csp = contentSecurityPolicy(nonce, isDev);

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
  const onCanonicalHost = offCanonicalRedirect(host, "/", "", canonical) === null;
  for (const [name, value] of Object.entries(transportHeaders(isDev, onCanonicalHost))) {
    response.headers.set(name, value);
  }
  return response;
}

export const config = {
  // `robots.txt` joins the exclusions alongside the static-asset paths: it
  // is a file for crawlers, and the session redirect below would otherwise
  // answer every crawler with a 307 to /login - which is exactly the page
  // the file exists to tell them not to fetch.
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico|robots.txt).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
