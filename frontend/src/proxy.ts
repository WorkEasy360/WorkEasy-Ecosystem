import { NextRequest, NextResponse } from "next/server";
import { adminSignInUrl } from "@/features/auth/signInMode";
import {
  isState,
  pageOwner,
  portalForHost,
  readPortalOrigins,
  servesPage,
  signInUrl,
  stateCookieName,
  type Destination,
  type PortalOrigins,
} from "@/lib/portals";

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
/** Production's name for the same cookie (backend production.py): the
 * `__Host-` prefix makes the browser refuse a same-named cookie planted for
 * a parent domain by a sibling host (ADR-043). */
const HOST_SESSION_COOKIE = "__Host-w360_session";

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
  // ADR-053: a partner's referral link, followed by a visitor with no account.
  "/r",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  // ADR-044: a product's "sign out of Workeasy360 too" lands here. A visitor
  // whose session has already gone still gets a page that returns them to
  // the product, rather than a sign-in form they did not ask for.
  "/sign-out",
];

/** ADR-053: a partner's branded Store (`/store/<partner>`) is browsable signed
 * out; buying signs in first. `/store` itself still needs a session. */
const PUBLIC_PREFIXES = ["/store/"];

export function isPublicPath(pathname: string): boolean {
  return (
    PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`)) ||
    PUBLIC_PREFIXES.some((p) => pathname.startsWith(p) && pathname.length > p.length)
  );
}

/** Where a portal host lands a sign-in hand-off (ADR-043): reached before
 * the portal has a session, by definition. */
const HANDOFF_LANDING = "/auth/continue";

export type PortalRouting = { kind: "serve" } | { kind: "redirect"; location: string; state?: string };

/**
 * The multi-portal routing decision for one page request (ADR-043), or `null`
 * when the host is not one of the platform's portals (a partner's white-label
 * domain, handled by {@link offCanonicalRedirect}). Display only: it picks
 * which host shows a page and sends a signed-out visitor to the central
 * sign-in; every permission is still decided by the server.
 *
 * `state` on a redirect is the portal's binding value for the hand-off: the
 * caller sets it as this host's own cookie. An existing valid one is reused,
 * so a second sign-in started before the first finishes does not void it. The
 * cookie is spent by the first redemption: a tab redeeming after that asks
 * for one more click ("Sign in again"), never a second password.
 */
export function routePortalRequest(args: {
  origins: PortalOrigins;
  host: string;
  pathname: string;
  search: string;
  hasSession: boolean;
  existingState?: string | null;
  newState: () => string;
}): PortalRouting | null {
  const { origins, pathname, search, hasSession } = args;
  const portal = portalForHost(args.host, origins);
  if (portal === null) return null;
  const params = new URLSearchParams(search);
  const startSignIn = (destination: Destination, next: string | null): PortalRouting => {
    const state = isState(args.existingState) ? args.existingState : args.newState();
    return { kind: "redirect", location: signInUrl(origins, destination, next, state), state };
  };

  if (portal !== "auth" && pathname === "/login") {
    const resume = params.get("resume");
    if (resume) return { kind: "redirect", location: `${origins.auth}/login?resume=${encodeURIComponent(resume)}` };
    return startSignIn(portal, params.get("next"));
  }
  if (pathname === "/") {
    if (portal === "admin") return { kind: "redirect", location: `${origins.admin}/admin` };
    if (portal === "partner") return { kind: "redirect", location: `${origins.partner}/partner` };
    if (portal === "auth") return { kind: "redirect", location: `${origins.auth}/login` };
    return { kind: "serve" }; // the public site's home, on the customer portal
  }
  if (!servesPage(portal, pathname)) {
    const owner = pageOwner(pathname);
    if (owner) return { kind: "redirect", location: `${origins[owner]}${pathname}${search}` };
  }
  const isPublic = isPublicPath(pathname) || (portal !== "auth" && pathname === HANDOFF_LANDING);
  if (!hasSession && !isPublic) {
    if (portal === "auth") {
      const resume = params.get("resume");
      return { kind: "redirect", location: `${origins.auth}/login${resume ? `?resume=${encodeURIComponent(resume)}` : ""}` };
    }
    return startSignIn(portal, `${pathname}${search}`);
  }
  return { kind: "serve" };
}

/** 32 random bytes, base64url: a hand-off binding value. */
function randomState(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Buffer.from(bytes).toString("base64url");
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
  const origins = readPortalOrigins(process.env);
  // In multi-portal mode a host that is none of the portals is a partner's
  // white-label domain, and its other pages go to the customer portal.
  const canonical = origins ? new URL(origins.app).host : (process.env.W360_CANONICAL_HOST ?? "");
  const host = request.headers.get("host") ?? "";
  const hasSessionCookie = request.cookies.has(SESSION_COOKIE) || request.cookies.has(HOST_SESSION_COOKIE);

  if (origins) {
    const cookieName = stateCookieName(origins);
    const routed = routePortalRequest({
      origins,
      host,
      pathname,
      search: request.nextUrl.search,
      hasSession: hasSessionCookie,
      existingState: request.cookies.get(cookieName)?.value,
      newState: randomState,
    });
    if (routed?.kind === "redirect") {
      const response = NextResponse.redirect(new URL(routed.location, request.url), 302);
      if (routed.state) {
        response.cookies.set(cookieName, routed.state, {
          httpOnly: true,
          sameSite: "lax",
          secure: cookieName.startsWith("__Host-"),
          path: "/",
          maxAge: 600,
        });
      }
      return response;
    }
    if (routed?.kind === "serve") {
      return withSecurityHeaders(request, nonce, isDev, true);
    }
  }

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

  const hasSession = hasSessionCookie;
  const isPublic = isPublicPath(pathname);

  // A partner's domain (multi-portal mode) serves only its branded landing,
  // which needs no session.
  if (!origins && !hasSession && !isPublic && pathname !== "/") {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Carry a resume token straight through (it is opaque to us) so a
    // product deep-link that landed here unauthenticated still resumes
    // correctly once the real login page finishes (Task 31/32).
    const resume = request.nextUrl.searchParams.get("resume");
    url.search = resume ? `?resume=${encodeURIComponent(resume)}` : "";
    // A console page asks for the Administrator sign-in and is returned to
    // afterwards. Only a destination: the mode grants nothing, and `next` is
    // accepted only as a same-site path under /admin (signInMode.ts).
    if (!resume && (pathname === "/admin" || pathname.startsWith("/admin/"))) {
      const target = new URL(adminSignInUrl(`${pathname}${request.nextUrl.search}`), url);
      url.search = target.search;
    }
    return NextResponse.redirect(url);
  }

  const onCanonicalHost = offCanonicalRedirect(host, "/", "", canonical) === null;
  return withSecurityHeaders(request, nonce, isDev, onCanonicalHost);
}

/** Serve the page, with the CSP and the security headers every page gets. */
function withSecurityHeaders(request: NextRequest, nonce: string, isDev: boolean, onCanonicalHost: boolean): NextResponse {
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
