/**
 * UTM campaign tags on a partner's referral link (2026-11-03, the unified
 * partner dashboard's UTM builder).
 *
 * The referral link itself is the server's (`/r/<code>`); these are standard
 * campaign tags a partner adds for its own analytics. The referral page carries
 * them to the page the link opens. Only the five standard keys and plain values
 * (letters, digits, `_ . -`) - nothing else is forwarded or built.
 */

export const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"] as const;
export type UtmKey = (typeof UTM_KEYS)[number];
export const UTM_VALUE = /^[A-Za-z0-9_.-]{1,100}$/;

/** `destination` with the valid UTM tags of `search` appended. */
export function withUtm(destination: string, search: string): string {
  const incoming = new URLSearchParams(search);
  const tags = UTM_KEYS.flatMap((key) => {
    const value = incoming.get(key);
    return value && UTM_VALUE.test(value) ? [`${key}=${value}`] : [];
  });
  if (tags.length === 0) return destination;
  return `${destination}${destination.includes("?") ? "&" : "?"}${tags.join("&")}`;
}

/** A referral link with tags added (blank or invalid tags are left out). */
export function taggedLink(url: string, tags: Partial<Record<UtmKey, string>>): string {
  const parts = UTM_KEYS.flatMap((key) => {
    const value = (tags[key] ?? "").trim();
    return value && UTM_VALUE.test(value) ? [`${key}=${value}`] : [];
  });
  if (parts.length === 0) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${parts.join("&")}`;
}
