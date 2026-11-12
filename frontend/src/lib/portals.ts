/**
 * The platform's portals (docs/portals/design.md, ADR-043): which host shows
 * which experience, and where a sign-in may return to.
 *
 * **Display only.** The host picks the UI; it never decides what anyone may
 * do. Every request is still authorised by the server from live StaffGrants,
 * partner memberships and organization memberships, whatever host it arrives
 * on - and the server also refuses each portal's privileged API on every
 * other host.
 *
 * Pure functions (no React, no `process`), so the proxy, the pages and the
 * tests share one table. The origins come from server-side runtime
 * configuration (`W360_ADMIN_ORIGIN`, ...): when they are not all set, the app
 * is in single-host mode and behaves exactly as before.
 */

/** ADR-065: `store` is the Digital Store's own host (store.workeasy360.com).
 * Optional: its origin is `""` when `W360_STORE_ORIGIN` is not set, and then its
 * pages are the customer portal's, exactly as before. */
export type Portal = "admin" | "partner" | "app" | "auth" | "store";
export type PortalOrigins = Record<Portal, string>;
/** Where a sign-in is headed - the sign-in page's `?mode=` (and the Store's own host). */
export type Destination = Exclude<Portal, "auth">;

export const PORTALS: readonly Portal[] = ["admin", "partner", "app", "auth", "store"];

/** `?mode=` on the sign-in page -> the portal it aims at (intent only). The
 * Store has no mode: its own address is its sign-in. */
export const MODE_TO_PORTAL = { admin: "admin", partner: "partner", user: "app" } as const;
export const PORTAL_TO_MODE = { admin: "admin", partner: "partner", app: "user", store: "user" } as const;

/** Where each destination opens when no (valid) page was asked for. */
export const PORTAL_ROOT: Record<Destination, string> = { admin: "/admin", partner: "/partner", app: "/dashboard", store: "/store" };

/** The return-URL allowlist, per destination - the same table as the
 * server's (`contracts/values/portals.py`), which validates it again. */
const NEXT_PREFIXES: Record<Destination, readonly string[]> = {
  admin: ["/admin"],
  partner: ["/partner"],
  app: ["/dashboard", "/o", "/organizations", "/onboarding", "/billing", "/settings", "/store"],
  store: ["/store"],
};

/** Which portal serves each page. A page not listed here (`/auth/continue`,
 * a 404) is served on whichever host it was asked for.
 *
 * ADR-059: the sign-in pages are each portal's own. The Admin, Partner and
 * customer portals each serve `/login`, `/forgot-password` and
 * `/reset-password` on their own host (see {@link ALSO_ON}), so signing in
 * there sets that host's session directly - no trip to the central sign-in
 * and no hand-off. Sign-up, email verification and invitations are the
 * customer portal's. The central sign-in host keeps only the product
 * sign-in (OIDC) pages. */
const PAGE_OWNERS: readonly (readonly [string, Portal])[] = [
  ["/admin", "admin"],
  ["/partner", "partner"],
  ["/login", "app"],
  ["/signup", "app"],
  // ADR-053: a referral link records the visit where sign-up will read it.
  ["/r", "app"],
  ["/verify-email", "app"],
  ["/forgot-password", "app"],
  ["/reset-password", "app"],
  ["/invitations", "app"],
  ["/owner-invitations", "app"],
  // ADR-044: the access chooser and the RP-initiated sign-out confirmation
  // act on the central sign-in's own session.
  ["/product-access", "auth"],
  ["/sign-out", "auth"],
  ["/dashboard", "app"],
  // The Store (ADR-052) - on its own host since ADR-065 (store.workeasy360.com);
  // without one, on the customer portal (see ownerFor).
  ["/store", "store"],
  ["/o", "app"],
  ["/organizations", "app"],
  ["/onboarding", "app"],
  ["/billing", "app"],
  ["/settings", "app"],
  ["/products", "app"],
  ["/pricing", "app"],
  ["/services", "app"],
  ["/contact", "app"],
];

/** Pages more than one portal serves: the organization chooser is also how
 * the central sign-in asks which organization a product sign-in is for; each
 * portal has its own sign-in and password reset (ADR-059), and the central
 * sign-in host keeps `/login` for a product sign-in (`?resume=`). */
const ALSO_ON: Record<string, readonly Portal[]> = {
  "/organizations/select": ["auth"],
  "/login": ["admin", "partner", "auth", "store"],
  "/forgot-password": ["admin", "partner", "store"],
  "/reset-password": ["admin", "partner", "store"],
  // ADR-065: the Store signs its own customers up and in, so the referral
  // cookie, the saved purchase and the session all stay on one host; and the
  // development test provider's page returns a Store payment there.
  "/signup": ["store"],
  "/verify-email": ["store"],
  "/r": ["store"],
  "/billing/test-checkout": ["store"],
};

const matches = (path: string, prefix: string) => path === prefix || path.startsWith(`${prefix}/`);

/** `scheme://host[:port]`, lower-cased, of a bare origin; `null` otherwise. */
export function parseOrigin(raw: string | undefined | null): string | null {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  if (!value || value.includes("*")) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.username || url.password || url.search || url.hash || (url.pathname && url.pathname !== "/")) return null;
  return url.origin.toLowerCase();
}

/** All four web origins, or `null` (single-host mode) unless every one is a
 * valid origin. The Store's own origin is optional (ADR-065): `""` when unset. */
export function readPortalOrigins(env: Record<string, string | undefined>): PortalOrigins | null {
  const origins = {
    admin: parseOrigin(env.W360_ADMIN_ORIGIN),
    partner: parseOrigin(env.W360_PARTNER_ORIGIN),
    app: parseOrigin(env.W360_APP_ORIGIN),
    auth: parseOrigin(env.W360_AUTH_ORIGIN),
  };
  if (!Object.values(origins).every(Boolean)) return null;
  return { ...(origins as Omit<PortalOrigins, "store">), store: parseOrigin(env.W360_STORE_ORIGIN) ?? "" };
}

const hostOf = (origin: string) => (origin ? new URL(origin).host.toLowerCase() : "");

/** Which portal a request's `Host` header names, or `null` for any other
 * host (a partner's white-label domain). */
export function portalForHost(host: string | null | undefined, origins: PortalOrigins): Portal | null {
  const wanted = (host ?? "").trim().toLowerCase().replace(/\.$/, "");
  if (!wanted) return null;
  return PORTALS.find((p) => origins[p] && hostOf(origins[p]) === wanted) ?? null;
}

/** The portal that serves `pathname`, or `null` when every host does. With
 * `origins`, the Store's pages belong to the customer portal while the Store
 * has no host of its own (ADR-065). */
export function pageOwner(pathname: string, origins?: PortalOrigins | null): Portal | null {
  const owner = PAGE_OWNERS.find(([prefix]) => matches(pathname, prefix))?.[1] ?? null;
  return owner === "store" && origins && !origins.store ? "app" : owner;
}

/** Whether `portal` serves `pathname`. */
export function servesPage(portal: Portal, pathname: string, origins?: PortalOrigins | null): boolean {
  const owner = pageOwner(pathname, origins);
  if (owner === null || owner === portal) return true;
  return Object.entries(ALSO_ON).some(([prefix, also]) => matches(pathname, prefix) && also.includes(portal));
}

/**
 * The page a sign-in for `portal` lands on: `raw` when it is a plain same-site
 * path that portal serves, otherwise the portal's root. Never a URL - a
 * scheme, `//host`, a backslash, a control character, a `..` or an encoded
 * escape all fall back - so `next` cannot become an open redirect.
 */
export function safeNext(portal: Destination, raw: string | null | undefined): string {
  const root = PORTAL_ROOT[portal];
  if (!raw || raw.length > 512 || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return root;
  if ([...raw].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return root;
  if (/%2e|%2f|%5c/i.test(raw)) return root;
  let url: URL;
  try {
    url = new URL(raw, "https://next.invalid");
  } catch {
    return root;
  }
  if (url.origin !== "https://next.invalid") return root;
  // `URL` resolves `..` itself; refuse the raw value instead of trusting that.
  if (raw.split(/[?#]/, 1)[0].split("/").some((s) => s === "." || s === "..")) return root;
  if (!NEXT_PREFIXES[portal].some((prefix) => matches(url.pathname, prefix))) return root;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** The central sign-in page for someone headed to `portal`. `state` is the
 * portal's binding value (its host-only cookie), never a credential.
 *
 * ADR-059: no longer where a portal sends a signed-out visitor (that is the
 * portal's own {@link portalSignInPath}); kept for the hand-off a sign-in
 * already in flight may still complete. */
export function signInUrl(origins: PortalOrigins, portal: Exclude<Destination, "store">, next: string | null, state: string): string {
  const params = new URLSearchParams({ mode: PORTAL_TO_MODE[portal] });
  const page = safeNext(portal, next);
  if (page !== PORTAL_ROOT[portal]) params.set("next", page);
  params.set("state", state);
  return `${origins.auth}/login?${params.toString()}`;
}

/** A portal's own sign-in page (ADR-059), returning to `next` afterwards
 * when it is a page that portal serves. A path on that portal's host. */
export function portalSignInPath(portal: Destination, next: string | null): string {
  const page = safeNext(portal, next);
  return page === PORTAL_ROOT[portal] ? "/login" : `/login?next=${encodeURIComponent(page)}`;
}

/** An absolute link to a page of another portal (or the path itself in
 * single-host mode). */
export function portalHref(origins: PortalOrigins | null, portal: Portal, path: string): string {
  return origins ? `${origins[portal]}${path}` : path;
}

/** The portal's binding cookie: `__Host-` (Secure, Path=/, no Domain) when
 * the portals are served over https, as in production. Must match the
 * server's `PORTAL_STATE_COOKIE_NAME`. */
export function stateCookieName(origins: PortalOrigins): string {
  return origins.auth.startsWith("https://") ? "__Host-w360_portal_state" : "w360_portal_state";
}

/** A binding value is base64url, 32-128 characters - the server's own rule. */
export function isState(raw: string | null | undefined): raw is string {
  return typeof raw === "string" && /^[A-Za-z0-9_-]{32,128}$/.test(raw);
}
