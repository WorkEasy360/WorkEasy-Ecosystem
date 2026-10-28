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

export type Portal = "admin" | "partner" | "app" | "auth";
export type PortalOrigins = Record<Portal, string>;
/** Where a sign-in is headed - the sign-in page's `?mode=`. */
export type Destination = Exclude<Portal, "auth">;

export const PORTALS: readonly Portal[] = ["admin", "partner", "app", "auth"];

/** `?mode=` on the sign-in page -> the portal it aims at (intent only). */
export const MODE_TO_PORTAL = { admin: "admin", partner: "partner", user: "app" } as const;
export const PORTAL_TO_MODE = { admin: "admin", partner: "partner", app: "user" } as const;

/** Where each destination opens when no (valid) page was asked for. */
export const PORTAL_ROOT: Record<Destination, string> = { admin: "/admin", partner: "/partner", app: "/dashboard" };

/** The return-URL allowlist, per destination - the same table as the
 * server's (`contracts/values/portals.py`), which validates it again. */
const NEXT_PREFIXES: Record<Destination, readonly string[]> = {
  admin: ["/admin"],
  partner: ["/partner"],
  app: ["/dashboard", "/o", "/organizations", "/onboarding", "/billing", "/settings", "/store"],
};

/** Which portal serves each page. A page not listed here (`/auth/continue`,
 * a 404) is served on whichever host it was asked for. */
const PAGE_OWNERS: readonly (readonly [string, Portal])[] = [
  ["/admin", "admin"],
  ["/partner", "partner"],
  ["/login", "auth"],
  ["/signup", "auth"],
  // ADR-053: a referral link records the visit where sign-up will read it.
  ["/r", "auth"],
  ["/verify-email", "auth"],
  ["/forgot-password", "auth"],
  ["/reset-password", "auth"],
  ["/invitations", "auth"],
  ["/owner-invitations", "auth"],
  // ADR-044: the access chooser and the RP-initiated sign-out confirmation
  // act on the central sign-in's own session.
  ["/product-access", "auth"],
  ["/sign-out", "auth"],
  ["/dashboard", "app"],
  // The Store's address (ADR-052): opens the organization's Explore.
  ["/store", "app"],
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
 * the central sign-in asks which organization a product sign-in is for. */
const ALSO_ON: Record<string, readonly Portal[]> = { "/organizations/select": ["auth"] };

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
 * valid origin. */
export function readPortalOrigins(env: Record<string, string | undefined>): PortalOrigins | null {
  const origins = {
    admin: parseOrigin(env.W360_ADMIN_ORIGIN),
    partner: parseOrigin(env.W360_PARTNER_ORIGIN),
    app: parseOrigin(env.W360_APP_ORIGIN),
    auth: parseOrigin(env.W360_AUTH_ORIGIN),
  };
  return Object.values(origins).every(Boolean) ? (origins as PortalOrigins) : null;
}

const hostOf = (origin: string) => new URL(origin).host.toLowerCase();

/** Which portal a request's `Host` header names, or `null` for any other
 * host (a partner's white-label domain). */
export function portalForHost(host: string | null | undefined, origins: PortalOrigins): Portal | null {
  const wanted = (host ?? "").trim().toLowerCase().replace(/\.$/, "");
  if (!wanted) return null;
  return PORTALS.find((p) => hostOf(origins[p]) === wanted) ?? null;
}

/** The portal that serves `pathname`, or `null` when every host does. */
export function pageOwner(pathname: string): Portal | null {
  return PAGE_OWNERS.find(([prefix]) => matches(pathname, prefix))?.[1] ?? null;
}

/** Whether `portal` serves `pathname`. */
export function servesPage(portal: Portal, pathname: string): boolean {
  const owner = pageOwner(pathname);
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
 * portal's binding value (its host-only cookie), never a credential. */
export function signInUrl(origins: PortalOrigins, portal: Destination, next: string | null, state: string): string {
  const params = new URLSearchParams({ mode: PORTAL_TO_MODE[portal] });
  const page = safeNext(portal, next);
  if (page !== PORTAL_ROOT[portal]) params.set("next", page);
  params.set("state", state);
  return `${origins.auth}/login?${params.toString()}`;
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
