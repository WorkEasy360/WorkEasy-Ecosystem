import type { CapabilityCatalog, ResellerDraftBody, ResellerReview } from "@/lib/api/partnerConsole";
import { SUPPORTED_CURRENCIES, toBasisPoints, toMinor } from "../amounts";
import {
  emptyProfile,
  errorsFromProblems,
  localProblems,
  toProfileInput,
  type FieldErrors,
  type ProfileDraft,
} from "../profileDraft";

/**
 * The Add Reseller wizard's form model: what is typed, how it becomes the
 * review request, and which of it is wrong or missing at each step.
 *
 * Everything here is a string, a choice or a selection. Nothing computes a
 * price, discount, margin or commission: the only conversions are text to
 * integer minor units (`toMinor`) and text to basis points (`toBasisPoints`),
 * exactly as before, and the server answers with every figure shown.
 */

// --- purposes -------------------------------------------------------------------------------------

/**
 * Why the partner is being added (Partner Control Center → Add partner). A
 * purpose is **only the capability preset the form starts from** - it is not
 * sent to the server, not stored, and not a kind of partner: the result is an
 * ordinary partner account holding whichever capabilities were ticked when
 * the person saved, and its labels follow from those grants.
 */
export type Purpose = "reseller" | "white_label" | "affiliate" | "custom";

export const PURPOSES: Record<
  Purpose,
  { title: string; noun: string; preset: string | null; lead: string; list: string; reason: string }
> = {
  reseller: {
    title: "Add reseller",
    noun: "reseller",
    preset: "standard_reseller",
    lead: "A reseller is an ordinary partner account that can sell. Nothing is created until you choose Save draft or Activate reseller.",
    list: "/admin/partners/resellers",
    reason: "Set up through the Add Reseller wizard",
  },
  white_label: {
    title: "Add white-label partner",
    noun: "partner",
    preset: "white_label",
    lead: "A white-label partner is an ordinary partner account that can set its own brand and domains. Nothing is created until you choose Save draft or Activate.",
    list: "/admin/partners/white-label",
    reason: "Set up through the Add Partner flow",
  },
  affiliate: {
    title: "Add affiliate",
    noun: "affiliate",
    preset: "affiliate",
    lead: "An affiliate is an ordinary partner account that refers customers and earns commission on them. Nothing is created until you choose Save draft or Activate.",
    list: "/admin/partners/affiliates",
    reason: "Set up through the Add Partner flow",
  },
  custom: {
    title: "Add partner",
    noun: "partner",
    preset: null,
    lead: "Choose exactly the capabilities this partner holds. Nothing is created until you choose Save draft or Activate.",
    list: "/admin/partners/all",
    reason: "Set up through the Add Partner flow",
  },
};

export function isPurpose(value: string | null): value is Purpose {
  return value !== null && Object.prototype.hasOwnProperty.call(PURPOSES, value);
}

export const capitalize = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : text);

// --- steps -----------------------------------------------------------------------------------------

export const STEPS = [
  "business",
  "contacts",
  "capabilities",
  "products",
  "pricing",
  "commission",
  "administrator",
  "whitelabel",
  "review",
] as const;
export type StepKey = (typeof STEPS)[number];

export const STEP_LABELS: Record<StepKey, string> = {
  business: "Business details",
  contacts: "Contacts",
  capabilities: "Capabilities",
  products: "Products and trials",
  pricing: "Pricing",
  commission: "Commission and payouts",
  administrator: "Administrator and support",
  whitelabel: "White-label",
  review: "Review",
};

/** The two capabilities that together make a partner white-label (the server
 * grants the same pair: `enable_white_label`). */
export const WHITE_LABEL_KEYS = ["branding.manage", "domain.manage"] as const;

// --- the draft -------------------------------------------------------------------------------------

export interface AdministratorDraft {
  firstName: string;
  lastName: string;
  email: string;
  role: "admin" | "viewer";
  invitation: "now" | "on_activation";
}

export interface Draft {
  /** Business details, contacts, support and payout preferences. */
  profile: ProfileDraft;
  code: string;
  codeEdited: boolean;
  currency: string;
  /** Which action is highlighted on the review. It activates nothing by itself. */
  status: "draft" | "active";
  /** ``null`` until the person chooses: the purpose's preset applies. */
  capabilities: string[] | null;
  /** The capabilities step has been shown. The preset is a pre-ticked starting
   * point on that step, so until it has been seen nothing has been chosen: a
   * draft saved from an earlier step grants no capability. */
  capabilitiesSeen: boolean;
  products: string[];
  /** Whether this partner's customers may start the platform's trial. Absent = yes. */
  trialAllowed: Record<string, boolean>;
  pricingMode: "standard" | "custom";
  /** Typed partner prices, keyed ``planVersionId|interval``. Empty = list price. */
  prices: Record<string, string>;
  commissionMode: "default" | "custom";
  ratePercent: string;
  commissionScope: "all" | "products";
  commissionProducts: string[];
  /** A ``datetime-local`` value, or empty for "when it is assigned". */
  effectiveFrom: string;
  admin: AdministratorDraft;
  whiteLabel: boolean;
}

/** What the server's dry run last said, and whether the draft has changed since. */
export interface ReviewState {
  load: "idle" | "loading" | "ready" | "error";
  data?: ResellerReview;
  stale: boolean;
}

export type SetDraft = <K extends keyof Draft>(key: K, value: Draft[K]) => void;

/** What every step receives: the draft, how to change it, and the inline errors
 * (keyed by the server's field names) it should show. */
export interface StepProps {
  draft: Draft;
  set: SetDraft;
  patchProfile: (patch: Partial<ProfileDraft>) => void;
  errors: FieldErrors;
}

export function emptyDraft(purpose: Purpose = "reseller"): Draft {
  return {
    profile: emptyProfile(),
    code: "",
    codeEdited: false,
    currency: "GBP",
    status: "draft",
    capabilities: null,
    capabilitiesSeen: false,
    products: [],
    trialAllowed: {},
    pricingMode: "standard",
    prices: {},
    commissionMode: "default",
    ratePercent: "",
    commissionScope: "all",
    commissionProducts: [],
    effectiveFrom: "",
    admin: { firstName: "", lastName: "", email: "", role: "admin", invitation: "on_activation" },
    whiteLabel: purpose === "white_label",
  };
}

/** A suggested code from the display name: input convenience only - the server
 * validates the code and refuses one that is taken. */
export function suggestCode(name: string): string {
  const slug = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return /^[a-z]/.test(slug) ? slug : slug ? `p-${slug}`.slice(0, 48) : "";
}

const CODE_PATTERN = /^[a-z][a-z0-9-]{2,47}$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function presetKeys(catalog: CapabilityCatalog | null, preset: string | null = "standard_reseller"): string[] {
  if (preset === null) return [];
  return catalog?.presets.find((p) => p.key === preset)?.capabilities ?? [];
}

export function priceKey(planVersionId: string, interval: string): string {
  return `${planVersionId}|${interval}`;
}

/** What the person ticked, or - once they have seen the step - the purpose's preset. */
export function chosenCapabilities(d: Draft, catalog: CapabilityCatalog | null, preset: string | null): string[] {
  return d.capabilities ?? (d.capabilitiesSeen ? presetKeys(catalog, preset) : []);
}

/** Whether the administrator section has anything in it. An untouched one is
 * "not yet" (a draft needs none); a started one needs an email. */
export function administratorStarted(a: AdministratorDraft): boolean {
  return Boolean(a.firstName.trim() || a.lastName.trim() || a.email.trim());
}

/** A ``datetime-local`` value as the timestamp the server takes, or ``null``
 * when it is not a moment. The browser's own zone is what the person meant. */
export function effectiveFromIso(text: string): string | null {
  if (!text.trim()) return null;
  const moment = new Date(text);
  return Number.isNaN(moment.getTime()) ? null : moment.toISOString();
}

/** Now, in the form a ``datetime-local`` input takes (the browser's own zone):
 * the earliest moment its picker should offer. */
export function localNowInput(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

/** The products a commission rule is limited to, among those the partner may sell. */
export function commissionScopeCodes(d: Draft): string[] {
  return d.commissionScope === "products" ? d.commissionProducts.filter((c) => d.products.includes(c)) : [];
}

/** The typed partner prices that are real amounts, in custom pricing only. */
export function typedPrices(d: Draft): ResellerDraftBody["partner_prices"] {
  const prices: ResellerDraftBody["partner_prices"] = [];
  if (d.pricingMode !== "custom") return prices;
  for (const [key, text] of Object.entries(d.prices)) {
    if (!text.trim()) continue;
    const minor = toMinor(text, d.currency);
    if (minor === null) continue;
    const [plan_version_id, interval] = key.split("|");
    prices.push({ plan_version_id, interval, amount_minor: minor });
  }
  return prices;
}

/** The request the review takes, from the draft. Typed prices and rates that do
 * not parse are left out (their step flags them). */
export function toBody(d: Draft, catalog: CapabilityCatalog | null, preset: string | null = "standard_reseller"): ResellerDraftBody {
  const primary = d.profile.contacts.primary;
  const bp = d.commissionMode === "custom" ? toBasisPoints(d.ratePercent) : null;
  const scope = commissionScopeCodes(d);
  const effective = effectiveFromIso(d.effectiveFrom);
  const commission: ResellerDraftBody["commission"] =
    d.commissionMode === "custom"
      ? {
          mode: "custom",
          ...(bp !== null ? { rate_bp: bp } : {}),
          ...(scope.length > 0 ? { product_codes: scope } : {}),
          ...(effective ? { effective_from: effective } : {}),
        }
      : { mode: "default" };
  const options: NonNullable<ResellerDraftBody["product_options"]> = {};
  for (const code of d.products) options[code] = { trial_allowed: d.trialAllowed[code] ?? true, portal_visible: true };
  const a = d.admin;
  return {
    partner: {
      code: d.code.trim(),
      display_name: d.profile.displayName.trim(),
      legal_name: d.profile.legalName.trim(),
      contact_name: primary.name.trim(),
      contact_email: primary.email.trim(),
      payout_currency: d.currency,
    },
    profile: toProfileInput(d.profile),
    capabilities: chosenCapabilities(d, catalog, preset),
    white_label: d.whiteLabel,
    products: d.products,
    product_options: options,
    pricing_mode: d.pricingMode,
    partner_prices: typedPrices(d),
    commission,
    ...(administratorStarted(a)
      ? {
          administrator: {
            email: a.email.trim(),
            role: a.role,
            first_name: a.firstName.trim(),
            last_name: a.lastName.trim(),
            send_invitation: a.invitation,
          },
        }
      : {}),
  };
}

// --- what is wrong, and what is missing ----------------------------------------------------------------------

/** ``continue`` is a step's own "Save & continue": what is wrong *and* what that
 * step needs. ``draft`` is Save draft: only what is wrong, plus the identity a
 * partner cannot exist without - a draft needs no contact, product or administrator. */
export type CheckMode = "continue" | "draft";

export const IDENTITY_KEYS = ["display_name", "code", "payout_currency"] as const;

function identityErrors(d: Draft): FieldErrors {
  const e: FieldErrors = {};
  if (!d.profile.displayName.trim()) e.display_name = "Enter the company or partner name.";
  if (!CODE_PATTERN.test(d.code.trim())) {
    e.code = "The code must be 3-48 characters: lower-case letters, digits and hyphens, starting with a letter.";
  }
  if (!SUPPORTED_CURRENCIES.includes(d.currency)) e.payout_currency = "Choose the payout currency.";
  return e;
}

/** Whether Save draft may be offered: the name, the code and the currency. */
export function identityValid(d: Draft): boolean {
  return Object.keys(identityErrors(d)).length === 0;
}

const NOT_AN_AMOUNT = (text: string, currency: string) =>
  `“${text}” is not an amount in ${currency}. Use digits and at most ${currency === "JPY" ? "no" : "two"} decimals${
    currency === "JPY" ? "" : ", for example 45.00"
  }.`;

/** What is wrong (and, in ``continue`` mode, missing) on one step, keyed by the
 * server's own field names where it has them, so a local problem and a server
 * problem land on the same input. */
export function validateStep(
  step: StepKey,
  d: Draft,
  mode: CheckMode,
  options: { catalog?: CapabilityCatalog | null; preset?: string | null } = {},
): FieldErrors {
  const strict = mode === "continue";
  const e: FieldErrors = {};
  if (step === "business") {
    Object.assign(e, identityErrors(d), localProblems(d.profile, ["business"]));
    if (strict && !d.profile.businessEmail.trim()) e["business.business_email"] = "Enter the business email address.";
    if (strict && !d.profile.country) e["business.country"] = "Choose the country.";
  }
  if (step === "contacts") {
    Object.assign(e, localProblems(d.profile, ["contacts"]));
    const primary = d.profile.contacts.primary;
    if (strict && !primary.name.trim()) e["contacts.primary.name"] = "Enter the primary contact's name.";
    if (strict && !primary.email.trim()) e["contacts.primary.email"] = "Enter the primary contact's email address.";
  }
  if (step === "capabilities" && strict) {
    const preset = options.preset ?? null;
    if (chosenCapabilities(d, options.catalog ?? null, preset).length === 0) {
      e.capabilities =
        preset === "standard_reseller"
          ? "Choose at least one capability, or start from Standard Reseller."
          : "Choose at least one capability.";
    }
  }
  if (step === "pricing" && d.pricingMode === "custom") {
    for (const [key, text] of Object.entries(d.prices)) {
      if (text.trim() && toMinor(text, d.currency) === null) e[`price.${key}`] = NOT_AN_AMOUNT(text.trim(), d.currency);
    }
  }
  if (step === "commission") {
    Object.assign(e, localProblems(d.profile, ["payout"]));
    if (d.commissionMode === "custom") {
      if (toBasisPoints(d.ratePercent) === null) {
        e["commission.rate"] =
          "Enter the commission as a percentage from 0 to 100, with at most two decimals, for example 15 or 12.5.";
      }
      if (d.commissionScope === "products" && commissionScopeCodes(d).length === 0) {
        e["commission.scope"] = "Choose at least one product, or apply the rate to all products.";
      }
      if (d.effectiveFrom.trim()) {
        const iso = effectiveFromIso(d.effectiveFrom);
        if (iso === null) e["commission.effective_from"] = "Enter a valid date and time.";
        else if (new Date(iso).getTime() < Date.now()) {
          e["commission.effective_from"] =
            "Choose a time that is not in the past: a new rate never changes what earlier sales are worth. Leave it empty to start when it is assigned.";
        }
      }
    }
  }
  if (step === "administrator") {
    Object.assign(e, localProblems(d.profile, ["support"]));
    if (administratorStarted(d.admin)) {
      const email = d.admin.email.trim();
      if (!email) e["administrator.email"] = "Enter the administrator's email address.";
      else if (!EMAIL_PATTERN.test(email)) e["administrator.email"] = "Enter a valid email address.";
    }
  }
  return e;
}

/** Every step's problems, for Save draft: nothing is saved with something wrong in it. */
export function validateAll(
  d: Draft,
  options: { catalog?: CapabilityCatalog | null; preset?: string | null } = {},
): Partial<Record<StepKey, FieldErrors>> {
  const out: Partial<Record<StepKey, FieldErrors>> = {};
  for (const step of STEPS) {
    const found = validateStep(step, d, "draft", options);
    if (Object.keys(found).length > 0) out[step] = found;
  }
  return out;
}

/** The distinct messages of a set of field errors, in order. */
export function messagesOf(errors: FieldErrors): string[] {
  return [...new Set(Object.values(errors))];
}

// --- the server's problems ---------------------------------------------------------------------------------------

/** Which step shows the input a server problem names. */
export function stepOfField(field: string): StepKey {
  if (
    field === "code" ||
    field === "display_name" ||
    field === "legal_name" ||
    field === "payout_currency" ||
    field.startsWith("business.")
  ) {
    return "business";
  }
  if (field === "contact_name" || field === "contact_email" || field.startsWith("contacts")) return "contacts";
  if (field === "capabilities") return "capabilities";
  if (field === "products" || field === "product_options") return "products";
  if (field === "pricing_mode" || field === "partner_prices") return "pricing";
  if (field === "commission" || field.startsWith("payout.")) return "commission";
  if (field.startsWith("administrator") || field.startsWith("support.") || field === "internal_notes") return "administrator";
  if (field === "white_label") return "whitelabel";
  return "review";
}

/** The server's problems as inline errors. A problem the server reports against
 * the account's own name or contact fields is shown on the matching input of
 * the form, which uses the profile's field names. */
export function adaptServerErrors(problems: { field: string; message: string }[]): FieldErrors {
  const out = errorsFromProblems(problems);
  const alias: [string, string][] = [
    ["display_name", "business.display_name"],
    ["legal_name", "business.legal_name"],
    ["contact_name", "contacts.primary.name"],
    ["contact_email", "contacts.primary.email"],
  ];
  for (const [from, to] of alias) {
    if (out[from] && !out[to]) out[to] = out[from];
  }
  return out;
}
