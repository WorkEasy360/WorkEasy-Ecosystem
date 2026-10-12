import type {
  ContactInput,
  ContactKind,
  PartnerContactView,
  PartnerProfile,
  PayoutFrequency,
  ProfileInput,
} from "@/lib/api/partnerConsole";
import { COUNTRY_CODES } from "./countries";

/**
 * The business profile as a form holds it, and how it becomes a request.
 *
 * One model for both places the profile is typed: the Add Reseller wizard (a
 * draft that does not exist yet) and a partner's Business Details and Contacts
 * tabs (an edit of what is saved). Everything is a string the person typed; the
 * server validates, cleans and decides. The checks here are the shallow ones that
 * spare a round trip - an address without an @, a link that is not https - and
 * they use the **same dotted field names the server reports its own problems
 * under** (`business.website`, `contacts.billing.email`), so a server problem and
 * a local one land on the same input.
 */

export const CONTACT_KINDS: ContactKind[] = ["primary", "billing", "technical", "support"];
export const SECONDARY_KINDS: Exclude<ContactKind, "primary">[] = ["billing", "technical", "support"];

export interface ContactDraft {
  name: string;
  jobTitle: string;
  email: string;
  phone: string;
}

export interface ProfileDraft {
  displayName: string;
  legalName: string;
  businessEmail: string;
  website: string;
  phone: string;
  alternativeEmail: string;
  registrationNumber: string;
  taxNumber: string;
  country: string;
  address: { line1: string; line2: string; city: string; region: string; postalCode: string };
  contacts: Record<ContactKind, ContactDraft>;
  /** A billing, technical or support contact that is the primary one: stores no row. */
  sameAsPrimary: Record<Exclude<ContactKind, "primary">, boolean>;
  supportEmail: string;
  supportPhone: string;
  supportUrl: string;
  payoutFrequency: PayoutFrequency;
  payoutContactEmail: string;
  internalNotes: string;
}

const emptyContact = (): ContactDraft => ({ name: "", jobTitle: "", email: "", phone: "" });

export function emptyProfile(): ProfileDraft {
  return {
    displayName: "",
    legalName: "",
    businessEmail: "",
    website: "",
    phone: "",
    alternativeEmail: "",
    registrationNumber: "",
    taxNumber: "",
    country: "",
    address: { line1: "", line2: "", city: "", region: "", postalCode: "" },
    contacts: { primary: emptyContact(), billing: emptyContact(), technical: emptyContact(), support: emptyContact() },
    sameAsPrimary: { billing: true, technical: true, support: true },
    supportEmail: "",
    supportPhone: "",
    supportUrl: "",
    payoutFrequency: "manual",
    payoutContactEmail: "",
    internalNotes: "",
  };
}

const contactOf = (view: PartnerContactView | undefined): ContactDraft => ({
  name: view?.name ?? "",
  jobTitle: view?.job_title ?? "",
  email: view?.email ?? "",
  phone: view?.phone ?? "",
});

/** The form's state for a partner that already exists. */
export function profileDraftFrom(view: PartnerProfile): ProfileDraft {
  const b = view.business;
  return {
    displayName: b.display_name,
    legalName: b.legal_name,
    businessEmail: b.business_email,
    website: b.website,
    phone: b.phone,
    alternativeEmail: b.alternative_email,
    registrationNumber: b.registration_number,
    taxNumber: b.tax_number,
    country: b.country,
    address: {
      line1: b.address.line1,
      line2: b.address.line2,
      city: b.address.city,
      region: b.address.region,
      postalCode: b.address.postal_code,
    },
    contacts: {
      primary: contactOf(view.contacts.primary),
      billing: contactOf(view.contacts.billing),
      technical: contactOf(view.contacts.technical),
      support: contactOf(view.contacts.support),
    },
    sameAsPrimary: {
      billing: view.contacts.billing.same_as_primary === true,
      technical: view.contacts.technical.same_as_primary === true,
      support: view.contacts.support.same_as_primary === true,
    },
    supportEmail: view.support.email,
    supportPhone: view.support.phone,
    supportUrl: view.support.url,
    payoutFrequency: view.payout.frequency,
    payoutContactEmail: view.payout.contact_email,
    internalNotes: view.internal_notes,
  };
}

function contactInput(d: ContactDraft): ContactInput {
  return { name: d.name.trim(), job_title: d.jobTitle.trim(), email: d.email.trim(), phone: d.phone.trim() };
}

/** Which parts of the profile a request carries. A wizard sends all of them; one
 * tab sends only its own, so saving contacts can never overwrite the business
 * details with stale form state. */
export type ProfileParts = ("business" | "contacts" | "support" | "payout" | "notes")[];

export function toProfileInput(d: ProfileDraft, parts: ProfileParts = ["business", "contacts", "support", "payout", "notes"]): ProfileInput {
  const out: ProfileInput = {};
  if (parts.includes("business")) {
    out.business = {
      display_name: d.displayName.trim(),
      legal_name: d.legalName.trim(),
      business_email: d.businessEmail.trim(),
      website: d.website.trim(),
      phone: d.phone.trim(),
      alternative_email: d.alternativeEmail.trim(),
      registration_number: d.registrationNumber.trim(),
      tax_number: d.taxNumber.trim(),
      country: d.country,
      address: {
        line1: d.address.line1.trim(),
        line2: d.address.line2.trim(),
        city: d.address.city.trim(),
        region: d.address.region.trim(),
        postal_code: d.address.postalCode.trim(),
      },
    };
  }
  if (parts.includes("contacts")) {
    out.contacts = { primary: contactInput(d.contacts.primary) };
    for (const kind of SECONDARY_KINDS) {
      out.contacts[kind] = d.sameAsPrimary[kind] ? { same_as_primary: true } : contactInput(d.contacts[kind]);
    }
  }
  if (parts.includes("support")) {
    out.support = { email: d.supportEmail.trim(), phone: d.supportPhone.trim(), url: d.supportUrl.trim() };
  }
  if (parts.includes("payout")) {
    out.payout = { frequency: d.payoutFrequency, contact_email: d.payoutContactEmail.trim() };
  }
  if (parts.includes("notes")) out.internal_notes = d.internalNotes.trim();
  return out;
}

// --- the shallow checks ---------------------------------------------------------------------

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const HTTPS = /^https:\/\/[^\s/?#]+\.[^\s]+$/i;
const PHONE = /^\+?[0-9][0-9 ()\-.]{3,30}$/;

export type FieldErrors = Record<string, string>;

const MESSAGES = {
  email: "Enter a valid email address.",
  https: "Enter a plain https:// address.",
  phone: "Enter a phone number using digits, spaces and + ( ) - . only.",
  country: "Choose a country from the list.",
};

function checkEmail(errors: FieldErrors, key: string, value: string) {
  if (value.trim() && !EMAIL.test(value.trim())) errors[key] = MESSAGES.email;
}
function checkUrl(errors: FieldErrors, key: string, value: string) {
  if (value.trim() && !HTTPS.test(value.trim())) errors[key] = MESSAGES.https;
}
function checkPhone(errors: FieldErrors, key: string, value: string) {
  const text = value.trim();
  if (text && (!PHONE.test(text) || (text.match(/\d/g) ?? []).length < 5)) errors[key] = MESSAGES.phone;
}

/** What is plainly wrong, keyed by the server's own field names. Required-ness
 * is the caller's: a wizard step and an edit tab require different things. */
export function localProblems(d: ProfileDraft, parts: ProfileParts = ["business", "contacts", "support", "payout"]): FieldErrors {
  const errors: FieldErrors = {};
  if (parts.includes("business")) {
    checkEmail(errors, "business.business_email", d.businessEmail);
    checkEmail(errors, "business.alternative_email", d.alternativeEmail);
    checkUrl(errors, "business.website", d.website);
    checkPhone(errors, "business.phone", d.phone);
    if (d.country && !COUNTRY_CODES.includes(d.country)) errors["business.country"] = MESSAGES.country;
  }
  if (parts.includes("contacts")) {
    for (const kind of CONTACT_KINDS) {
      if (kind !== "primary" && d.sameAsPrimary[kind as Exclude<ContactKind, "primary">]) continue;
      checkEmail(errors, `contacts.${kind}.email`, d.contacts[kind].email);
      checkPhone(errors, `contacts.${kind}.phone`, d.contacts[kind].phone);
    }
  }
  if (parts.includes("support")) {
    checkEmail(errors, "support.email", d.supportEmail);
    checkPhone(errors, "support.phone", d.supportPhone);
    checkUrl(errors, "support.url", d.supportUrl);
  }
  if (parts.includes("payout")) checkEmail(errors, "payout.contact_email", d.payoutContactEmail);
  return errors;
}

/** The server's problems as inline errors: the first message per field. */
export function errorsFromProblems(problems: { field: string; message: string }[]): FieldErrors {
  const out: FieldErrors = {};
  for (const p of problems) if (!(p.field in out)) out[p.field] = p.message;
  return out;
}
