/**
 * Typed amounts to integer minor units, and back, without floating point.
 *
 * The browser never computes a price; it converts what an administrator typed
 * ("499.99" INR) into the integer the server stores (49999) - once, with
 * string and integer arithmetic only - and the server validates it again.
 */

/** Minor-unit exponent. Mirrors the backend's table for the currencies the
 * platform supports: JPY has none, every other one has two. */
export function exponentOf(currency: string): number {
  return currency === "JPY" ? 0 : 2;
}

const WHOLE = /^\d{1,12}$/;
const TWO_PLACES = /^\d{1,12}(\.\d{1,2})?$/;

/** "499.99" with INR → 49999; "1500" with JPY → 1500; null when the text is
 * not a plain non-negative amount with at most the currency's decimals. */
export function toMinor(value: string, currency: string): number | null {
  const text = value.trim();
  const exponent = exponentOf(currency);
  if (!(exponent === 0 ? WHOLE : TWO_PLACES).test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const minor = Number(whole) * 10 ** exponent + Number(fraction.padEnd(exponent, "0") || "0");
  return Number.isSafeInteger(minor) ? minor : null;
}

const PERCENT = /^\d{1,3}(\.\d{1,2})?$/;

/** "15" → 1500, "12.5" → 1250, "100" → 10000; null when the text is not a
 * percentage of 0-100 with at most two decimals. Like {@link toMinor} this only
 * converts what was typed into the integer the server stores (basis points) -
 * no rate is ever applied to an amount here. */
export function toBasisPoints(value: string): number | null {
  const text = value.trim().replace(/%$/, "").trim();
  if (!PERCENT.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const bp = Number(whole) * 100 + Number(fraction.padEnd(2, "0") || "0");
  return bp <= 10000 ? bp : null;
}

/** 1250 → "12.5%" for display of a stored rate (integer arithmetic only). */
export function formatBasisPoints(bp: number): string {
  const whole = Math.floor(bp / 100);
  const fraction = bp % 100;
  return fraction === 0 ? `${whole}%` : `${whole}.${String(fraction).padStart(2, "0").replace(/0$/, "")}%`;
}

/** 49999 with INR → "499.99" (for pre-filling an input; display uses money()). */
export function toMajor(minor: number, currency: string): string {
  const exponent = exponentOf(currency);
  if (exponent === 0) return String(minor);
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const unit = 10 ** exponent;
  return `${sign}${Math.floor(abs / unit)}.${String(abs % unit).padStart(exponent, "0")}`;
}

/** The currencies the platform supports (the backend's CurrencyCode). Used
 * only to offer choices; the server rejects anything else. */
export const SUPPORTED_CURRENCIES = ["AED", "AUD", "CAD", "EUR", "GBP", "INR", "JPY", "USD"];
