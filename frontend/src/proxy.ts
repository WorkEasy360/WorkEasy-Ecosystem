import { NextRequest, NextResponse } from "next/server";
import { adminSignInUrl, parseMode } from "@/features/auth/signInMode";
import {
  MODE_TO_PORTAL,
  pageOwner,
  portalForHost,
  portalSignInPath,
  readPortalOrigins,
  servesPage,
  type Destination,
  type Portal,
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
  // ADR-066: Switch Account's second step ends the central sign-in's session
  // when there is one, and moves on to a sign-in page either way.
  "/switch-account",
  // ADR-066: the change-of-sign-in-email link, opened in whichever browser the
  // mail is read in - the link is the credential.
  "/confirm-email",
  // 2026-11-02: the public Store - browsing is open; buying signs in first.
  "/store",
  // 2026-11-09: the partner application, filled in before any account exists.
  "/partner/apply",
  // 2026-11-10: a new partner's invitation link, opened before they have a password.
  "/partner/accept-invitation",
  // 2026-11-10: "Continue as Guest" - a static preview, no session and no API call.
  "/partner/preview",
];

/** ADR-053: a partner's branded Store (`/store/<partner>`) is browsable signed
 * out; buying signs in first. */
const PUBLIC_PREFIXES = ["/store/"];

export function isPublicPath(pathname: string): boolean {
  return (
    PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`)) ||
    PUBLIC_PREFIXES.some((p) => pathname.startsWith(p) && pathname.length > p.length)
  );
}

/** Where a portal host lands a sign-in hand-off (ADR-043): reached before
 * the portal has a session, by definition. Kept for a hand-off already in
 * flight; no portal starts one any more (ADR-059). */
const HANDOFF_LANDING = "/auth/continue";

export type PortalRouting = { kind: "serve" } | { kind: "redirect"; location: string };

/**
 * The multi-portal routing decision for one page request (ADR-043), or `null`
 * when the host is not one of the platform's portals (a partner's white-label
 * domain, handled by {@link offCanonicalRedirect}). Display only: it picks
 * which host shows a page and sends a signed-out visitor to a sign-in page;
 * every permission is still decided by the server.
 *
 * ADR-059: each portal signs in on its **own** host. A signed-out visit to the
 * Admin, Partner or customer portal goes to that portal's `/login`, the form
 * there signs in against the same one endpoint, and the session cookie is set
 * on that host directly - no central sign-in, no hand-off code, no binding
 * cookie. `?mode=` on a sign-in address only says which portal's sign-in page
 * to show. The central sign-in host keeps the product sign-in (`?resume=`).
 */
export function routePortalRequest(args: {
  origins: PortalOrigins;
  host: string;
  pathname: string;
  search: string;
  hasSession: boolean;
}): PortalRouting | null {
  const { origins, pathname, search, hasSession } = args;
  const portal = portalForHost(args.host, origins);
  if (portal === null) return null;
  const params = new URLSearchParams(search);
  const signIn = (destination: Destination, next: string | null): PortalRouting => ({
    kind: "redirect",
    location: `${origins[destination]}${portalSignInPath(destination, next)}`,
  });

  if (pathname === "/login") {
    // A product's sign-in (OIDC, ADR-044) completes on the central sign-in host.
    if (params.get("resume")) {
      return portal === "auth" ? { kind: "serve" } : { kind: "redirect", location: `${origins.auth}/login${search}` };
    }
    const mode = parseMode(params.get("mode"));
    // ADR-065: the Store's own host signs its customers in itself - with or
    // without `?mode=user`, which names the same people.
    if (portal === "store" && (mode === null || mode === "user")) return { kind: "serve" };
    const target: Portal = mode ? MODE_TO_PORTAL[mode] : portal === "auth" ? "app" : portal;
    if (target === portal) return { kind: "serve" };
    // Another portal's sign-in page: the same address on its own host, less
    // what no longer applies there (the mode it names, an old binding value).
    params.delete("mode");
    params.delete("state");
    const rest = params.toString();
    return { kind: "redirect", location: `${origins[target]}/login${rest ? `?${rest}` : ""}` };
  }
  if (pathname === "/") {
    // ADR-065: https://store.workeasy360.com/ is the marketplace itself
    // (app/page.tsx renders it on that host).
    if (portal === "store") return { kind: "serve" };
    if (portal === "admin") return { kind: "redirect", location: `${origins.admin}/admin` };
    if (portal === "partner") return { kind: "redirect", location: `${origins.partner}/partner` };
    if (portal === "auth") return { kind: "redirect", location: `${origins.app}/login` };
    return { kind: "serve" }; // the public site's home, on the customer portal
  }
  if (!servesPage(portal, pathname, origins)) {
    // Including the Store's old addresses on the customer portal (ADR-065):
    // `app.../store/...` opens the same page on the Store's own host.
    const owner = pageOwner(pathname, origins);
    if (owner && origins[owner]) return { kind: "redirect", location: `${origins[owner]}${pathname}${search}` };
  }
  const isPublic = isPublicPath(pathname) || (portal !== "auth" && pathname === HANDOFF_LANDING);
  if (!hasSession && !isPublic) {
    if (portal === "auth") {
      const resume = params.get("resume");
      return resume
        ? { kind: "redirect", location: `${origins.auth}/login?resume=${encodeURIComponent(resume)}` }
        : { kind: "redirect", location: `${origins.app}/login` };
    }
    return signIn(portal, `${pathname}${search}`);
  }
  return { kind: "serve" };
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
/*
 * `frame-src` (2026-11-04): a Store product page plays its video in YouTube's
 * no-cookie player or Vimeo's player - those two origins only, and the page
 * frames a player address the server built from a parsed video id, never a
 * typed link. Nothing else of this app embeds a frame.
 *
 * `form-action` (2026-11-10, ADR-062): `'self'`, plus `formTargets` - on the
 * Super Admin host only, the partner portal's origin, so Login as Guest can post
 * its one-time ticket there in a form body (never a URL). Nothing else of this
 * app submits a form to another origin, and no other host gets a target.
 */
export function contentSecurityPolicy(nonce: string, isDev: boolean, formTargets: readonly string[] = []): string {
  return `
    default-src 'self';
    script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""};
    style-src 'self' 'unsafe-inline';
    img-src 'self' data:;
    font-src 'self';
    connect-src 'self';
    frame-src 'self' https://www.youtube-nocookie.com https://player.vimeo.com;
    object-src 'none';
    base-uri 'self';
    form-action 'self'${formTargets.map((target) => ` ${target}`).join("")};
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
    const routed = routePortalRequest({
      origins,
      host,
      pathname,
      search: request.nextUrl.search,
      hasSession: hasSessionCookie,
    });
    if (routed?.kind === "redirect") {
      return NextResponse.redirect(new URL(routed.location, request.url), 302);
    }
    if (routed?.kind === "serve") {
      return withSecurityHeaders(request, nonce, isDev, true, formTargetsFor(host, origins));
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
  return withSecurityHeaders(request, nonce, isDev, onCanonicalHost, origins ? formTargetsFor(host, origins) : []);
}

/** The other origins a page on `host` may submit a form to: the partner
 * portal, from the Super Admin host only (Login as Guest, ADR-062); the
 * customer portal, from the Store's own host only ("My Account", ADR-065). */
export function formTargetsFor(host: string, origins: PortalOrigins): string[] {
  const portal = portalForHost(host, origins);
  if (portal === "admin") return [origins.partner];
  if (portal === "store") return [origins.app];
  return [];
}

/** Serve the page, with the CSP and the security headers every page gets. */
function withSecurityHeaders(
  request: NextRequest,
  nonce: string,
  isDev: boolean,
  onCanonicalHost: boolean,
  formTargets: readonly string[] = [],
): NextResponse {
  const csp = contentSecurityPolicy(nonce, isDev, formTargets);

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
      source: "/((?!api|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
