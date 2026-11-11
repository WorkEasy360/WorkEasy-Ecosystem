import type { ExperienceKey, OnboardingBody, ReviewProblem } from "@/lib/api/partnerConsole";
import type { FieldErrors } from "../profileDraft";

/**
 * The Add Partner form's model (ADR-047, ADR-048): the few fields it has, what is
 * wrong with them, and how they become the request.
 *
 * - **Only what the account needs.** The partner's name, the administrator's
 *   email and password, the type and (optionally) the country. Everything else is
 *   set afterwards in the partner's Complete Your Profile.
 * - **The type is a preset, not a kind.** It is sent as `experience`; the server
 *   grants that experience's capability bundle, and nothing here names a capability.
 * - **The password is in memory and in one request.** It is sent only when an
 *   account is to be created, and the form empties it as soon as the request is made.
 */

export const EXPERIENCE_KEYS: ExperienceKey[] = ["reseller", "white_label", "affiliate"];

/** The three type cards. Words only: what each grants is the server's bundle. */
export const TYPE_CARDS: Record<ExperienceKey, { title: string; text: string }> = {
  reseller: { title: "Reseller", text: "Sell WorkEasy360 products and manage customers." },
  white_label: {
    title: "White-label",
    text: "Their own branding and domain. White Label alone sells and refers nothing: tick Reseller or Affiliate below to allow that too.",
  },
  affiliate: { title: "Affiliate", text: "Promote WorkEasy360 products and earn commissions." },
};

/** `?type=` (and the old `?purpose=`) on the address: a preselection only. */
export function experienceFromParam(raw: string | null): ExperienceKey | null {
  const value = (raw ?? "").replace(/-/g, "_");
  return EXPERIENCE_KEYS.includes(value as ExperienceKey) ? (value as ExperienceKey) : null;
}

/** Used until the server says otherwise; the server's own policy is what counts. */
export const DEFAULT_MIN_PASSWORD = 12;

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
/** Digits, spaces and + ( ) - . only, with at least five digits: the server's rule. */
const PHONE_PATTERN = /^\+?[0-9][0-9 ()\-.]{3,30}$/;

export const EXISTING_ACCOUNT = "This email already belongs to an existing Workeasy360 account.";

/** The server's refusal of a name a live partner already has starts this way. */
export const SAME_NAME_PREFIX = "A partner named ";

// --- the password: requirements and an indicative strength ---------------------------------------------

export interface PasswordCheck {
  key: string;
  label: string;
  ok: boolean;
}

/** The rules the person can see being met. Length and "the two match" are
 * checked here as a courtesy; the rest of the policy (not a common password, not
 * all digits, not like the address) is the server's, applied on Create. */
export function passwordChecks(password: string, confirm: string, minLength: number): PasswordCheck[] {
  return [
    { key: "length", label: `At least ${minLength} characters`, ok: password.length >= minLength },
    { key: "match", label: "Both passwords match", ok: password.length > 0 && password === confirm },
  ];
}

export type StrengthLabel = "Too short" | "Weak" | "Fair" | "Good" | "Strong";

/** An indicative strength, 0-4, from length and variety. It is a hint to the
 * person typing and decides nothing: the server's policy accepts or refuses. */
export function passwordStrength(password: string, minLength: number = DEFAULT_MIN_PASSWORD): { score: number; label: StrengthLabel } {
  if (password.length < minLength) return { score: 0, label: "Too short" };
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length;
  const distinct = new Set(password).size;
  let score = 1;
  if (password.length >= minLength + 4) score += 1;
  if (classes >= 3 || password.includes(" ")) score += 1;
  if (password.length >= 20 && distinct >= 10) score += 1;
  if (distinct < 6) score = Math.min(score, 1);
  const label: StrengthLabel[] = ["Too short", "Weak", "Fair", "Good", "Strong"];
  return { score, label: label[Math.min(score, 4)] };
}

// --- the form ------------------------------------------------------------------------------------------------

/** How a new administrator gets in (2026-11-07): an invitation email with a
 * link to set their own password (the default), or a password set here. */
export type AdminAccess = "invite" | "password";

export interface AddPartnerDraft {
  name: string;
  country: string;
  /** The Partner Email: the partner's own login (and contact address unless another is given). */
  email: string;
  /** Optional mobile number. */
  phone: string;
  /** Use a contact address other than the Partner Email. */
  separateContact: boolean;
  contactEmail: string;
  /** Confirmed after the server said a live partner already has this name. */
  allowSameName: boolean;
  /** With a password set here: make the partner choose their own at first sign-in. */
  forceChange: boolean;
  mode: "create" | "link_existing";
  access: AdminAccess;
  password: string;
  confirm: string;
  experience: ExperienceKey | null;
  /** ADR-063: base permissions granted explicitly beside the type. */
  also: ExperienceKey[];
}

export const emptyAddPartner = (experience: ExperienceKey | null = null): AddPartnerDraft => ({
  name: "",
  country: "",
  email: "",
  phone: "",
  separateContact: false,
  contactEmail: "",
  allowSameName: false,
  forceChange: true,
  mode: "create",
  access: "invite",
  password: "",
  confirm: "",
  experience,
  also: [],
});

/** What is plainly wrong, keyed by the server's own field names so a local
 * problem and a server problem land on the same input. */
export function validate(d: AddPartnerDraft, minLength: number = DEFAULT_MIN_PASSWORD): FieldErrors {
  const e: FieldErrors = {};
  if (!d.name.trim()) e["partner.display_name"] = "Enter the partner's business name.";
  const email = d.email.trim();
  if (!email) e["administrator.email"] = "Enter the partner's email address.";
  else if (!EMAIL_PATTERN.test(email)) e["administrator.email"] = "Enter a valid email address.";
  const phone = d.phone.trim();
  if (phone && (!PHONE_PATTERN.test(phone) || (phone.match(/[0-9]/g) ?? []).length < 5)) {
    e["partner.phone"] = "Enter a valid mobile number, for example +91 98765 43210.";
  }
  if (d.separateContact) {
    const contact = d.contactEmail.trim();
    if (!contact) e["partner.contact_email"] = "Enter the contact email, or untick the separate contact email.";
    else if (!EMAIL_PATTERN.test(contact)) e["partner.contact_email"] = "Enter a valid contact email address.";
    else if (contact.toLowerCase() === email.toLowerCase()) e["partner.contact_email"] = "The contact email is the same as the Partner Email: untick the separate contact email.";
  }
  if (d.mode === "create" && d.access === "password") {
    if (!d.password) e["administrator.password"] = "Enter a password.";
    else if (d.password.length < minLength) e["administrator.password"] = `The password must be at least ${minLength} characters.`;
    if (!d.confirm) e["administrator.password_confirm"] = "Enter the password again.";
    else if (d.confirm !== d.password) e["administrator.password_confirm"] = "The two passwords do not match.";
  }
  if (!d.experience) e.experience = "Choose the partner type.";
  return e;
}

/** The distinct messages of a set of errors, in order. */
export const messagesOf = (errors: FieldErrors): string[] => [...new Set(Object.values(errors))];

/** The server's problems as inline errors: the first message per field. */
export function errorsFromProblems(problems: ReviewProblem[]): FieldErrors {
  const out: FieldErrors = {};
  for (const p of problems) if (!(p.field in out)) out[p.field] = p.message;
  return out;
}

/** The request. The password and its confirmation are sent only when an account
 * is to be created with a password set here; an invited administrator sets their
 * own, and a linked account keeps the password it has. */
export function toOnboardingBody(d: AddPartnerDraft): OnboardingBody {
  const typed = d.mode === "create" && d.access === "password";
  const phone = d.phone.trim();
  const contact = d.separateContact ? d.contactEmail.trim() : "";
  const also = d.also.filter((k) => k !== d.experience);
  return {
    experience: d.experience as ExperienceKey,
    ...(also.length ? { also } : {}),
    partner: {
      display_name: d.name.trim(),
      ...(d.country ? { country: d.country } : {}),
      ...(phone ? { phone } : {}),
      ...(contact ? { contact_email: contact } : {}),
      ...(d.allowSameName ? { allow_same_name: true } : {}),
    },
    administrator: {
      email: d.email.trim(),
      mode: d.mode === "link_existing" ? "link_existing" : typed ? "create" : "invite",
      ...(typed ? { password: d.password, password_confirm: d.confirm, require_password_change: d.forceChange } : {}),
    },
  };
}
