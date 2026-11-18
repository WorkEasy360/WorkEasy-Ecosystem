/**
 * The console's one way to write a date, a time, an amount and an empty value
 * (2026-11-07 design system). Display only: nothing here computes a price, and
 * every input is whatever the server sent.
 *
 * - date:        "24 Oct 2026"
 * - date-time:   "24 Oct 2026, 6:45 AM"   (no seconds; `exactTime` for a tooltip)
 * - relative:    "5 min ago", "3 days ago", then the date
 * - money:       "₹1,250.00"              (Intl.NumberFormat, minor units in)
 * - empty:       "—"
 */

export const EMPTY = "—";

const LOCALE = "en-GB";

const toDate = (iso: string | number | Date | null | undefined): Date | null => {
  if (iso === null || iso === undefined || iso === "") return null;
  const d = iso instanceof Date ? iso : new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

const DATE = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", year: "numeric" });
const TIME = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
const EXACT = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
});

/** "24 Oct 2026" ("Sept" is written "Sep", as everywhere else). */
export function formatDate(iso: string | number | Date | null | undefined): string {
  const d = toDate(iso);
  return d ? DATE.format(d).replace("Sept", "Sep") : EMPTY;
}

/** "24 Oct 2026, 6:45 AM". */
export function formatDateTime(iso: string | number | Date | null | undefined): string {
  const d = toDate(iso);
  return d ? `${formatDate(d)}, ${TIME.format(d)}` : EMPTY;
}

/** The exact moment, seconds and zone included - for a tooltip. */
export function exactTime(iso: string | number | Date | null | undefined): string | undefined {
  const d = toDate(iso);
  return d ? EXACT.format(d).replace("Sept", "Sep") : undefined;
}

/** "Just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", then the date.
 * A future moment reads "in 5 min" and so on. */
export function formatRelative(iso: string | number | Date | null | undefined, now: number = Date.now()): string {
  const d = toDate(iso);
  if (!d) return EMPTY;
  const diff = now - d.getTime();
  const future = diff < 0;
  const s = Math.abs(diff) / 1000;
  const say = (text: string) => (future ? `in ${text}` : `${text} ago`);
  if (s < 45) return future ? "In a moment" : "Just now";
  const m = Math.round(s / 60);
  if (m < 60) return say(`${m} min`);
  const h = Math.round(m / 60);
  if (h < 24) return say(`${h} h`);
  const days = Math.round(h / 24);
  if (days === 1) return future ? "Tomorrow" : "Yesterday";
  if (days < 30) return say(`${days} days`);
  return formatDate(d);
}

/** Integer minor units and a currency code to "₹1,250.00". */
export function formatMoney(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined || !currency) return EMPTY;
  const exponent = currency === "JPY" ? 0 : 2;
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(minor / 10 ** exponent);
  } catch {
    return `${(minor / 10 ** exponent).toFixed(exponent)} ${currency}`;
  }
}

/** 1234 to "1,234". */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return new Intl.NumberFormat("en-IN").format(value);
}

/** A value, or the single empty mark. */
export function orEmpty(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  const text = String(value).trim();
  return text === "" || /^(none|null|n\/a|not set|none recorded|not configured)$/i.test(text) ? EMPTY : text;
}

// --- readable names for system keys ------------------------------------------------

const WORDS: Record<string, string> = {
  ai: "AI",
  api: "API",
  crm: "CRM",
  csv: "CSV",
  dns: "DNS",
  gst: "GST",
  id: "ID",
  ip: "IP",
  mfa: "MFA",
  oidc: "OIDC",
  otp: "OTP",
  pdf: "PDF",
  sms: "SMS",
  sso: "SSO",
  tds: "TDS",
  ui: "UI",
  url: "URL",
  upi: "UPI",
  utm: "UTM",
};

/** One word of a key, written for a person. */
const word = (w: string, first: boolean) => {
  const lower = w.toLowerCase();
  if (WORDS[lower]) return WORDS[lower];
  return first ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
};

/** Sentence case from a system key: "long_context" -> "Long context". */
export function sentence(key: string | null | undefined): string {
  if (!key) return EMPTY;
  const words = key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_\-.]+/)
    .filter(Boolean);
  return words.map((w, i) => word(w, i === 0)).join(" ") || EMPTY;
}

/** Title Case for a status: "past_due" -> "Past Due", "AFFILIATE" -> "Affiliate". */
export function titleCase(key: string | null | undefined): string {
  if (!key) return EMPTY;
  return key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_\-]+/)
    .filter(Boolean)
    .map((w) => WORDS[w.toLowerCase()] ?? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

/** Verbs written in the past tense when they end an event key. */
const EVENT_VERBS: Record<string, string> = {
  signed_in: "signed in",
  signed_out: "signed out",
  sign_in: "sign-in",
};

/**
 * An audit or event key to a readable phrase: "commercial.invoice.issued" ->
 * "Invoice issued", "staff.signed_in" -> "Staff signed in", "identity.user" ->
 * "User". The domain prefix (commercial., platform., identity.) is dropped when
 * there is more after it.
 */
export function eventLabel(key: string | null | undefined): string {
  if (!key) return EMPTY;
  const parts = key.split(".").filter(Boolean);
  const DOMAINS = new Set(["commercial", "platform", "identity", "integration", "integrations", "commerce", "catalog", "auth", "admin"]);
  while (parts.length > 1 && DOMAINS.has(parts[0])) parts.shift();
  const text = parts
    .map((p) => EVENT_VERBS[p] ?? p.replace(/_/g, " "))
    .join(" ")
    .trim();
  return sentence(text.replace(/\s+/g, "_"));
}

/** "Easy CRM (easy_crm)" -> "Easy CRM": a product's name without its code. */
export function productName(label: string | null | undefined): string {
  if (!label) return EMPTY;
  return label.replace(/\s*\([a-z0-9_\-]+\)\s*$/i, "").trim() || label;
}
