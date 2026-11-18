/**
 * A product's own page to go back to after a purchase it started (ADR-065). Kept
 * for this tab only, and only after the server approved the address for that
 * product (`GET public/store/return-check`) - the confirmation page shows it as a
 * link, never a redirect.
 */

const KEY = "w360.store-return-to";

export function rememberReturnTo(back: { url: string; label: string }): void {
  if (!/^https:\/\//.test(back.url) || back.url.length > 2048) return;
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ url: back.url, label: back.label.slice(0, 80) }));
  } catch {
    // Storage refused: the confirmation simply has no way back.
  }
}

export function takeReturnTo(): { url: string; label: string } | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { url?: unknown; label?: unknown };
    if (typeof parsed.url !== "string" || !/^https:\/\//.test(parsed.url)) return null;
    return { url: parsed.url, label: typeof parsed.label === "string" && parsed.label ? parsed.label : "the product" };
  } catch {
    return null;
  }
}
