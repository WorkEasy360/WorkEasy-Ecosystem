/**
 * What one person leaves in this browser, forgotten when they sign out or
 * switch account (the unified-login brief 5.3: "clear or replace stale
 * client-side user/organization state"; test case 21). The server never
 * trusts any of it, so this is about the *next* person at the same browser
 * seeing a previous person's choices or names - a cached partner list, a cart,
 * a chosen organization, recently opened records - not about access.
 *
 * Kept on purpose: layout preferences that name nobody (sidebar order, table
 * columns), and what a visitor brought *before* signing in (a referral code, a
 * purchase picked before creating an account), which sign-up still needs.
 */

/** Keys (exact) or prefixes (ending in `.` or `-`) that belong to a person. */
const PERSONAL: readonly string[] = [
  "w360.org.notifications.seen.",
  "w360.admin.recent",
  "w360.admin.notifications.seen",
  "w360.admin.partners.list",
  "w360.cart.",
  "w360.cart",
  "w360.store-org",
  "w360.store-return-to",
  "w360.store-return",
];

function personal(key: string): boolean {
  return PERSONAL.some((entry) => (entry.endsWith(".") || entry.endsWith("-") ? key.startsWith(entry) : key === entry));
}

function forget(storage: Storage | undefined): void {
  if (!storage) return;
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (key && personal(key)) keys.push(key);
  }
  for (const key of keys) storage.removeItem(key);
}

/** Forget the signed-in person's browser state. Never throws: storage may be
 * blocked (private mode), and a sign-out must still complete. */
export function forgetAccountState(): void {
  for (const pick of [() => window.localStorage, () => window.sessionStorage]) {
    try {
      forget(pick());
    } catch {
      // Storage unavailable: nothing was kept there either.
    }
  }
}

/**
 * Leave the signed-in pages for good: a full page load, so nothing of the
 * previous person survives in memory (module caches, component state), and
 * `replace`, so Back does not reopen their pages from history.
 */
export function leaveTo(url: string): void {
  window.location.replace(url);
}
