/**
 * "Administrator", "Partner" or "User" on the sign-in page: a **destination**,
 * never a privilege.
 *
 * There is one sign-in (the same endpoint, rate limits, lockout, CSRF and
 * session for everyone). The mode only decides where the browser goes once it
 * succeeds - the Admin Console or the person's own workspace - and it lives in
 * the address bar (`?mode=`), nowhere else: it is not sent to the server, not
 * kept in browser storage, and never read by anything that decides access. The
 * Admin Console asks the server on every load (`/api/v1/admin/session`), which
 * answers from the person's live StaffGrants; choosing "Administrator" without
 * them just leads to a polite refusal.
 */

export type SignInMode = "admin" | "partner" | "user";

export function parseMode(raw: string | null): SignInMode | null {
  return raw === "admin" || raw === "partner" || raw === "user" ? raw : null;
}

const ORIGIN = "https://sign-in.invalid";

/**
 * The console page to return to after an administrator signs in (`?next=`,
 * set when an unauthenticated visit to `/admin/...` was sent to sign in).
 * Only a same-site path under `/admin` is accepted - anything else (another
 * site, `//host`, a backslash, a control character, a `..` that climbs out of
 * `/admin`) is ignored, so the parameter can never become an open redirect.
 */
export function safeAdminNext(raw: string | null): string | null {
  if (!raw || raw.length > 512 || !raw.startsWith("/admin")) return null;
  if (raw.startsWith("//") || raw.includes("\\")) return null;
  if ([...raw].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return null;
  let url: URL;
  try {
    url = new URL(raw, ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== ORIGIN) return null;
  if (!(url.pathname === "/admin" || url.pathname.startsWith("/admin/"))) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Where a person goes after signing in with this mode. */
export function destinationAfterSignIn(mode: SignInMode | null, next: string | null): string {
  if (mode === "admin") return safeAdminNext(next) ?? "/admin";
  // The partner portal lists this person's partner accounts (and says so
  // plainly when there are none); it checks membership on every request.
  if (mode === "partner") return "/partner";
  // The central dashboard picks the organization (or the chooser, or
  // onboarding). `mode=user` tells it not to send staff to the console.
  if (mode === "user") return "/dashboard?mode=user";
  return "/dashboard";
}

/** The sign-in address for someone who tried to open a console page. */
export function adminSignInUrl(path: string): string {
  const next = safeAdminNext(path);
  return next && next !== "/admin" ? `/login?mode=admin&next=${encodeURIComponent(next)}` : "/login?mode=admin";
}
