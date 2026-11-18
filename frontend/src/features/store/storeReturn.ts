/**
 * The Store page a sign-up should come back to (ADR-065): the checkout a buyer
 * left to create an account, with what they had chosen. The emailed verification
 * link opens on the Store's own host (the server builds it there), often in a new
 * tab, so the page is kept in `localStorage` - for a day at most, and only ever a
 * plain `/store/...` path, never an address.
 */

const KEY = "w360.store-return";
const TTL_MS = 24 * 60 * 60 * 1000;

/** A same-site Store path, or null: no scheme, no host, no backslash, no `..`,
 * no control character, no encoded escape. */
export function safeStorePath(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 1024) return null;
  if (!(raw === "/store" || raw.startsWith("/store/") || raw.startsWith("/store?"))) return null;
  if (raw.startsWith("//") || raw.includes("\\") || /%2e|%2f|%5c/i.test(raw)) return null;
  if ([...raw].some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return null;
  if (raw.split(/[?#]/, 1)[0].split("/").some((s) => s === "." || s === "..")) return null;
  try {
    const url = new URL(raw, "https://store.invalid");
    if (url.origin !== "https://store.invalid") return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export function rememberStoreReturn(path: string): void {
  const safe = safeStorePath(path);
  if (!safe) return;
  try {
    localStorage.setItem(KEY, JSON.stringify({ path: safe, savedAt: Date.now() }));
  } catch {
    // Private mode: the buyer opens the Store again themselves.
  }
}

/** The remembered Store page, once (it is forgotten as it is read). */
export function takeStoreReturn(): string | null {
  try {
    const raw = localStorage.getItem(KEY);
    localStorage.removeItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { path?: unknown; savedAt?: unknown };
    if (typeof parsed.savedAt !== "number" || Date.now() - parsed.savedAt > TTL_MS) return null;
    return safeStorePath(parsed.path);
  } catch {
    return null;
  }
}
