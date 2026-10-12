/**
 * The only three kinds of link a partner's brand can become on a customer's
 * screen: ``mailto:``, ``tel:`` and ``https``. Each is built here from a value
 * that has just been checked, never from the text the server sent, so nothing a
 * partner typed can turn into ``javascript:``, ``data:`` or a link to another
 * scheme. A value that fails its check yields ``null``: the caller then shows the
 * text without a link, or nothing.
 */

const SAFE_EMAIL = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;
const PHONE = /^\+?[0-9][0-9 ()\-.]{3,30}$/;

export function mailtoHref(value: string): string | null {
  const email = value.trim();
  return email.length <= 254 && SAFE_EMAIL.test(email) ? `mailto:${email}` : null;
}

export function telHref(value: string): string | null {
  const phone = value.trim();
  const digits = phone.replace(/[^0-9]/g, "");
  if (!PHONE.test(phone) || digits.length < 5) return null;
  return `tel:${phone.startsWith("+") ? "+" : ""}${digits}`;
}

/** A plain ``https`` address: no spaces, no credentials, a dotted host name. */
export function httpsHref(value: string): string | null {
  const url = value.trim();
  if (url.length > 300 || /\s/.test(url) || !/^https:\/\//i.test(url)) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || !parsed.hostname.includes(".") || parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}
