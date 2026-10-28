import { ApiError } from "@/lib/api/client";
import type { BrandPortalFields } from "@/lib/api/controlPlane";
import type { BrandDraftBody, StoredBrand, WhiteLabelSetup } from "@/lib/api/partnerConsole";
import { httpsHref } from "../brandLinks";

/**
 * The staff white-label wizard's model: the ten steps, the brand draft a person
 * types, how a draft is checked locally, and how it becomes a ``PUT brand``.
 *
 * Everything here is plain text the server validates again. The checks are the
 * shallow ones that spare a round trip, and they use the same wording the server
 * uses, so a local and a server message read alike.
 *
 * Not here, on purpose: a secondary colour and a favicon. The theme the app
 * renders has one derived accent, so those would store something nothing shows.
 */

export const STEPS = [
  "partner",
  "brand",
  "appearance",
  "products",
  "portal",
  "communication",
  "domain",
  "login",
  "connection",
  "review",
] as const;
export type StepKey = (typeof STEPS)[number];

export const STEP_LABELS: Record<StepKey, string> = {
  partner: "Select partner",
  brand: "Brand identity",
  appearance: "Portal appearance",
  products: "Products",
  portal: "Customer portal",
  communication: "Communication",
  domain: "Custom domain",
  login: "Login experience",
  connection: "Reseller connection",
  review: "Review",
};

// --- the draft ---------------------------------------------------------------------------

export interface BrandDraft {
  brandName: string;
  shortName: string;
  accent: string;
  supportName: string;
  supportEmail: string;
  supportPhone: string;
  supportUrl: string;
  websiteUrl: string;
  termsUrl: string;
  privacyUrl: string;
  welcome: string;
  footer: string;
  senderName: string;
  replyTo: string;
  useForInvitations: boolean;
  loginShowWelcome: boolean;
  loginShowSupport: boolean;
}
export type DraftKey = keyof BrandDraft;

/** The fields one step edits. A step saves its own group. */
export type Group = "identity" | "portal" | "communication" | "login";
export const ALL_GROUPS: Group[] = ["identity", "portal", "communication", "login"];

export const GROUP_KEYS: Record<Group, DraftKey[]> = {
  identity: ["brandName", "shortName", "accent", "supportName", "supportEmail", "supportPhone", "supportUrl", "websiteUrl", "termsUrl", "privacyUrl"],
  portal: ["welcome", "footer"],
  communication: ["senderName", "replyTo", "useForInvitations"],
  login: ["loginShowWelcome", "loginShowSupport"],
};

export const EMPTY_DRAFT: BrandDraft = {
  brandName: "",
  shortName: "",
  accent: "",
  supportName: "",
  supportEmail: "",
  supportPhone: "",
  supportUrl: "",
  websiteUrl: "",
  termsUrl: "",
  privacyUrl: "",
  welcome: "",
  footer: "",
  senderName: "",
  replyTo: "",
  useForInvitations: false,
  loginShowWelcome: true,
  loginShowSupport: true,
};

export function draftOf(brand: StoredBrand | null): BrandDraft {
  if (!brand) return { ...EMPTY_DRAFT };
  const p: Partial<BrandPortalFields> = brand.portal ?? {};
  return {
    brandName: brand.brand_name,
    shortName: p.short_name ?? "",
    accent: brand.accent_color,
    supportName: p.support_name ?? "",
    supportEmail: brand.support_email,
    supportPhone: p.support_phone ?? "",
    supportUrl: brand.support_url,
    websiteUrl: p.website_url ?? "",
    termsUrl: p.terms_url ?? "",
    privacyUrl: p.privacy_url ?? "",
    welcome: p.welcome_message ?? "",
    footer: p.footer_text ?? "",
    senderName: p.sender_name ?? "",
    replyTo: p.reply_to_email ?? "",
    useForInvitations: p.use_for_invitations ?? false,
    loginShowWelcome: p.login_show_welcome ?? true,
    loginShowSupport: p.login_show_support ?? true,
  };
}

const same = (a: string | boolean, b: string | boolean, key: DraftKey): boolean => {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  // The server trims, and lower-cases a colour.
  return key === "accent" ? a.trim().toLowerCase() === b.trim().toLowerCase() : a.trim() === b.trim();
};

/** Does the draft differ from what is stored, for this group? Without a stored
 * brand the identity group is always unsaved: it has to be saved to exist. */
export function groupDirty(draft: BrandDraft, brand: StoredBrand | null, group: Group): boolean {
  if (brand === null && group === "identity") return true;
  const stored = draftOf(brand);
  return GROUP_KEYS[group].some((key) => !same(draft[key], stored[key], key));
}

// --- local checks ----------------------------------------------------------------------------------

export type FieldErrors = Partial<Record<DraftKey | "logo" | "hostname", string>>;

const HEX = /^#[0-9a-fA-F]{6}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^\+?[0-9][0-9 ()\-.]{3,30}$/;
const HOSTNAME = /^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

export const isHexColour = (value: string): boolean => HEX.test(value);

/** The same check the customer's page makes before it draws a link. */
export const isHttpsUrl = (value: string): boolean => httpsHref(value) !== null;

/** A control character, or one that reorders or breaks a line: refused by the server. */
function hasControl(value: string): boolean {
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (code < 32 || code === 127 || code === 8232 || code === 8233 || (code >= 8234 && code <= 8238) || (code >= 8294 && code <= 8297)) return true;
  }
  return false;
}

function plainTextProblem(value: string, label: string, max: number): string | null {
  const text = value.trim();
  if (text.length > max) return `${label} must be ${max} characters or fewer.`;
  if (text.includes("<") || text.includes(">")) return `${label}: use plain text only, with no < or > characters.`;
  if (hasControl(text)) return `${label} must be on one line: remove the line breaks.`;
  return null;
}

export function hostnameProblem(value: string): string | null {
  const name = value.trim().toLowerCase();
  if (!name) return "Enter the host name to request, such as app.example.com.";
  if (!HOSTNAME.test(name) || /^[0-9.]+$/.test(name)) {
    return "Enter a host name such as app.example.com: letters, digits, hyphens and dots, with no https:// and no path.";
  }
  return null;
}

/** What is plainly wrong with the fields of one group. */
export function validateGroup(d: BrandDraft, group: Group): FieldErrors {
  const errors: FieldErrors = {};
  const put = (key: DraftKey, message: string | null) => {
    if (message && !(key in errors)) errors[key] = message;
  };
  if (group === "identity") {
    if (!d.brandName.trim()) put("brandName", "Enter the brand name.");
    else put("brandName", plainTextProblem(d.brandName, "The brand name", 80));
    put("shortName", plainTextProblem(d.shortName, "The short name", 24));
    if (d.accent.trim() && !HEX.test(d.accent.trim())) put("accent", "The primary colour must be a six-digit hex colour such as #1a56db.");
    put("supportName", plainTextProblem(d.supportName, "The support contact name", 80));
    if (d.supportEmail.trim() && (!EMAIL.test(d.supportEmail.trim()) || d.supportEmail.trim().length > 254)) {
      put("supportEmail", "Enter a valid support email, or leave it empty.");
    }
    const phone = d.supportPhone.trim();
    if (phone && (!PHONE.test(phone) || (phone.match(/[0-9]/g) ?? []).length < 5)) {
      put("supportPhone", "Enter a phone number using digits, spaces and + ( ) - . only.");
    }
    if (d.supportUrl.trim() && !isHttpsUrl(d.supportUrl.trim())) put("supportUrl", "The support page must be a plain https:// address, or empty.");
    if (d.websiteUrl.trim() && !isHttpsUrl(d.websiteUrl.trim())) put("websiteUrl", "The company website must be a plain https:// address, or empty.");
    if (d.termsUrl.trim() && !isHttpsUrl(d.termsUrl.trim())) put("termsUrl", "The terms page must be a plain https:// address, or empty.");
    if (d.privacyUrl.trim() && !isHttpsUrl(d.privacyUrl.trim())) put("privacyUrl", "The privacy page must be a plain https:// address, or empty.");
  }
  if (group === "portal") {
    put("welcome", plainTextProblem(d.welcome, "The welcome message", 280));
    put("footer", plainTextProblem(d.footer, "The footer text", 160));
  }
  if (group === "communication") {
    put("senderName", plainTextProblem(d.senderName, "The sender display name", 80));
    if (d.replyTo.trim() && (!EMAIL.test(d.replyTo.trim()) || d.replyTo.trim().length > 254)) {
      put("replyTo", "Enter a valid reply-to address, or leave it empty.");
    }
  }
  return errors;
}

export function validateGroups(d: BrandDraft, groups: Group[]): FieldErrors {
  return groups.reduce<FieldErrors>((all, group) => ({ ...validateGroup(d, group), ...all }), {});
}

// --- what a save sends ---------------------------------------------------------------------------

const PORTAL_OF: Record<Group, (d: BrandDraft) => Partial<BrandPortalFields>> = {
  identity: (d) => ({
    short_name: d.shortName.trim(),
    support_name: d.supportName.trim(),
    support_phone: d.supportPhone.trim(),
    website_url: d.websiteUrl.trim(),
    terms_url: d.termsUrl.trim(),
    privacy_url: d.privacyUrl.trim(),
  }),
  portal: (d) => ({ welcome_message: d.welcome.trim(), footer_text: d.footer.trim() }),
  communication: (d) => ({
    sender_name: d.senderName.trim(),
    reply_to_email: d.replyTo.trim(),
    use_for_invitations: d.useForInvitations,
  }),
  login: (d) => ({ login_show_welcome: d.loginShowWelcome, login_show_support: d.loginShowSupport }),
};

/**
 * The body of a ``PUT brand`` for these groups.
 *
 * The brand's own fields (name, colour, support, theme, logo) are *replaced* by
 * every save, so a step that does not edit them sends the stored ones back:
 * saving the customer-portal step must never blank the colour or drop the logo.
 * ``portal`` is merged by the server, so only the groups sent change. The very
 * first save of a brand always carries the identity group - a name is required.
 */
export function brandBody(stored: StoredBrand | null, d: BrandDraft, groups: Group[], logoFileId?: string): BrandDraftBody {
  const wanted = new Set<Group>(groups);
  if (stored === null) wanted.add("identity");
  const identity = wanted.has("identity") || stored === null;
  const portal: Partial<BrandPortalFields> = {};
  for (const group of ALL_GROUPS) if (wanted.has(group)) Object.assign(portal, PORTAL_OF[group](d));
  const logo = logoFileId ?? stored?.logo_file_id ?? undefined;
  return {
    brand_name: identity ? d.brandName.trim() : (stored?.brand_name ?? ""),
    accent_color: identity ? d.accent.trim() : (stored?.accent_color ?? ""),
    support_email: identity ? d.supportEmail.trim() : (stored?.support_email ?? ""),
    support_url: identity ? d.supportUrl.trim() : (stored?.support_url ?? ""),
    theme: stored?.theme ?? {},
    ...(logo ? { logo_file_id: logo } : {}),
    portal,
  };
}

// --- the server's own problems ---------------------------------------------------------------------

/** The draft field each server field name belongs to. */
const SERVER_FIELDS: Record<string, DraftKey | "logo" | "hostname"> = {
  brand_name: "brandName",
  accent_color: "accent",
  support_email: "supportEmail",
  support_url: "supportUrl",
  short_name: "shortName",
  support_name: "supportName",
  support_phone: "supportPhone",
  website_url: "websiteUrl",
  terms_url: "termsUrl",
  privacy_url: "privacyUrl",
  welcome_message: "welcome",
  footer_text: "footer",
  sender_name: "senderName",
  reply_to_email: "replyTo",
  use_for_invitations: "useForInvitations",
  login_show_welcome: "loginShowWelcome",
  login_show_support: "loginShowSupport",
  logo_file_id: "logo",
  content_type: "logo",
  content: "logo",
  hostname: "hostname",
};

/** A problem the server reported against a field, as ``{field, message}``
 * worded for a person, or ``null`` when the failure is about something else. */
export function serverProblem(err: unknown): { key: DraftKey | "logo" | "hostname"; message: string } | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.body as { field?: unknown; detail?: unknown } | undefined;
  if (!body || typeof body.field !== "string" || typeof body.detail !== "string") return null;
  const name = body.field.replace(/^portal\./, "");
  const key = SERVER_FIELDS[name];
  return key ? { key, message: body.detail } : null;
}

// --- the logo ------------------------------------------------------------------------------------------

export const LOGO_MAX_BYTES = 512 * 1024;
export const LOGO_TYPES = ["image/png", "image/jpeg"];

const SIGNATURES: Record<string, number[]> = {
  "image/png": [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
  "image/jpeg": [0xff, 0xd8, 0xff],
};

export const LOGO_TYPE_MESSAGE = "Use a PNG or JPEG image. SVG, HTML and other files are not accepted.";
export const LOGO_SIZE_MESSAGE = "The logo must be 512 KB or smaller.";
export const LOGO_CONTENT_MESSAGE = "That file is not really a PNG or JPEG image: its contents do not match its name. Choose a real PNG or JPEG.";

function readBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

/** Why this file cannot be a logo, or ``null`` when it can. The declared type
 * and the size are checked first, then the first bytes of the file itself: a
 * page of HTML named logo.png is refused here, and the server checks again. */
export async function checkLogo(file: File): Promise<string | null> {
  if (!LOGO_TYPES.includes(file.type)) return LOGO_TYPE_MESSAGE;
  if (file.size === 0) return "That file is empty.";
  if (file.size > LOGO_MAX_BYTES) return LOGO_SIZE_MESSAGE;
  let head: Uint8Array;
  try {
    head = await readBytes(file.slice(0, 16));
  } catch {
    return "That file could not be read. Choose it again.";
  }
  const expected = SIGNATURES[file.type];
  return expected.every((byte, i) => head[i] === byte) ? null : LOGO_CONTENT_MESSAGE;
}

export function readDataUrl(file: Blob): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

// --- derived facts the server reports, as words -------------------------------------------------------

const BRANDING_TEXT: Record<WhiteLabelSetup["branding_on_custom_domain"], string> = {
  none: "No custom domain has been requested.",
  pending: "A domain was requested and is waiting for DNS verification.",
  verified_not_active: "The domain is verified but not activated, so it does not serve the brand yet.",
  active: "The domain is active and serves the brand.",
};

export function brandingOnDomain(state: WhiteLabelSetup["branding_on_custom_domain"]): { active: boolean; detail: string } {
  return { active: state === "active", detail: BRANDING_TEXT[state] ?? "" };
}

const CAPABILITY_WORDS: Record<string, string> = {
  "branding.manage": "manage branding",
  "domain.manage": "manage custom domains",
};
export const capabilityWords = (key: string): string => CAPABILITY_WORDS[key] ?? key;
