"use client";

import { useMemo } from "react";
import { SUPPORTED_CURRENCIES } from "./amounts";
import { countryOptions } from "./countries";
import { SECONDARY_KINDS, type ContactDraft, type FieldErrors, type ProfileDraft } from "./profileDraft";
import { SelectField, TextAreaField, TextField } from "./widgets";
import type { ContactKind } from "@/lib/api/partnerConsole";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The forms a partner's business profile is typed into - shared by the Add
 * Reseller wizard (steps 1, 2, 7 and 8) and the partner's Business Details and
 * Contacts tabs, so a field is the same field in both places.
 *
 * Each is a controlled set of inputs over a {@link ProfileDraft}: `value` in,
 * `onChange(patch)` out, and `errors` keyed by the server's own field names
 * (`business.website`, `contacts.billing.email`) so a problem the server found
 * and one found here land on the same input. They validate nothing themselves,
 * submit nothing and decide nothing; what is required is the caller's choice
 * (`required`), and the server re-validates every value.
 */

type Patch = (patch: Partial<ProfileDraft>) => void;

interface FormProps {
  value: ProfileDraft;
  onChange: Patch;
  errors?: FieldErrors;
}

const CONTACT_TITLES: Record<ContactKind, string> = {
  primary: "Primary contact",
  billing: "Billing contact",
  technical: "Technical contact",
  support: "Support contact",
};

/** The company: names, how to reach it, registration and an address. */
export function BusinessDetailsForm({
  value,
  onChange,
  errors = {},
  required = ["displayName", "businessEmail", "country"],
  showNames = true,
}: FormProps & {
  /** Which fields are required (and marked so). */
  required?: ("displayName" | "businessEmail" | "country")[];
  /** The company-name fields are asked by the wizard's own first field and edited here on the tab. */
  showNames?: boolean;
}) {
  const countries = useMemo(() => countryOptions(), []);
  const must = (key: "displayName" | "businessEmail" | "country") => required.includes(key);
  const setAddress = (patch: Partial<ProfileDraft["address"]>) => onChange({ address: { ...value.address, ...patch } });
  return (
    <>
      {showNames && (
        <>
          <TextField
            label="Company or partner name"
            value={value.displayName}
            onChange={(v) => onChange({ displayName: v })}
            maxLength={120}
            autoComplete="off"
            required={must("displayName")}
            showRequired={must("displayName")}
            error={errors["business.display_name"]}
          />
          <TextField
            label="Legal business name (optional)"
            value={value.legalName}
            onChange={(v) => onChange({ legalName: v })}
            maxLength={200}
            autoComplete="off"
            error={errors["business.legal_name"]}
          />
        </>
      )}
      <div className={local.split}>
        <TextField
          label="Business email"
          type="email"
          value={value.businessEmail}
          onChange={(v) => onChange({ businessEmail: v })}
          maxLength={254}
          autoComplete="off"
          required={must("businessEmail")}
          showRequired={must("businessEmail")}
          error={errors["business.business_email"]}
        />
        <TextField
          label="Alternative email (optional)"
          type="email"
          value={value.alternativeEmail}
          onChange={(v) => onChange({ alternativeEmail: v })}
          maxLength={254}
          autoComplete="off"
          error={errors["business.alternative_email"]}
        />
      </div>
      <div className={local.split}>
        <TextField
          label="Website (optional)"
          value={value.website}
          onChange={(v) => onChange({ website: v })}
          maxLength={300}
          autoComplete="off"
          spellCheck={false}
          hint="A plain https:// address. It is shown as text and never fetched."
          error={errors["business.website"]}
        />
        <TextField
          label="Business phone (optional)"
          type="tel"
          value={value.phone}
          onChange={(v) => onChange({ phone: v })}
          maxLength={32}
          autoComplete="off"
          error={errors["business.phone"]}
        />
      </div>
      <div className={local.split}>
        <TextField
          label="Registration or company number (optional)"
          value={value.registrationNumber}
          onChange={(v) => onChange({ registrationNumber: v })}
          maxLength={64}
          autoComplete="off"
          error={errors["business.registration_number"]}
        />
        <TextField
          label="Tax, GST or VAT number (optional)"
          value={value.taxNumber}
          onChange={(v) => onChange({ taxNumber: v })}
          maxLength={64}
          autoComplete="off"
          hint="Not checked against any country's format."
          error={errors["business.tax_number"]}
        />
      </div>
      <fieldset className={local.checkGroup}>
        <legend>Address (optional)</legend>
        <TextField
          label="Address line 1"
          value={value.address.line1}
          onChange={(v) => setAddress({ line1: v })}
          maxLength={120}
          autoComplete="off"
          error={errors["business.address.line1"]}
        />
        <TextField
          label="Address line 2"
          value={value.address.line2}
          onChange={(v) => setAddress({ line2: v })}
          maxLength={120}
          autoComplete="off"
          error={errors["business.address.line2"]}
        />
        <div className={local.split}>
          <TextField
            label="City"
            value={value.address.city}
            onChange={(v) => setAddress({ city: v })}
            maxLength={120}
            autoComplete="off"
            error={errors["business.address.city"]}
          />
          <TextField
            label="State or province"
            value={value.address.region}
            onChange={(v) => setAddress({ region: v })}
            maxLength={120}
            autoComplete="off"
            error={errors["business.address.region"]}
          />
        </div>
        <TextField
          label="Postal or ZIP code"
          value={value.address.postalCode}
          onChange={(v) => setAddress({ postalCode: v })}
          maxLength={20}
          autoComplete="off"
          error={errors["business.address.postal_code"]}
        />
      </fieldset>
      <SelectField
        label="Country"
        value={value.country}
        onChange={(v) => onChange({ country: v })}
        options={countries}
        required={must("country")}
        showRequired={must("country")}
        error={errors["business.country"]}
      />
      <SelectField
        label="Time zone (optional)"
        value={value.timezone}
        onChange={(v) => onChange({ timezone: v })}
        options={timeZoneOptions(value.timezone)}
        error={errors["business.timezone"]}
      />
    </>
  );
}

/** Common time zones (IANA names the server accepts), with the stored one kept
 * even if it is not among them. A short list on purpose: a select of every zone
 * the browser knows is hundreds of options long. */
const COMMON_TIME_ZONES = [
  "UTC",
  "Europe/London", "Europe/Dublin", "Europe/Lisbon", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome",
  "Europe/Amsterdam", "Europe/Stockholm", "Europe/Warsaw", "Europe/Athens", "Europe/Istanbul",
  "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos", "Africa/Nairobi",
  "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Colombo", "Asia/Kathmandu", "Asia/Dhaka", "Asia/Bangkok",
  "Asia/Jakarta", "Asia/Singapore", "Asia/Kuala_Lumpur", "Asia/Manila", "Asia/Hong_Kong", "Asia/Shanghai",
  "Asia/Tokyo", "Asia/Seoul",
  "Australia/Perth", "Australia/Adelaide", "Australia/Brisbane", "Australia/Sydney", "Pacific/Auckland",
  "America/Sao_Paulo", "America/Argentina/Buenos_Aires", "America/Bogota", "America/Lima", "America/Mexico_City",
  "America/New_York", "America/Toronto", "America/Chicago", "America/Denver", "America/Phoenix",
  "America/Los_Angeles", "America/Vancouver", "America/Anchorage", "Pacific/Honolulu",
];

function timeZoneOptions(current: string): { value: string; label: string }[] {
  const zones = current && !COMMON_TIME_ZONES.includes(current) ? [current, ...COMMON_TIME_ZONES] : COMMON_TIME_ZONES;
  return [{ value: "", label: "Not set" }, ...zones.map((z) => ({ value: z, label: z.replace(/_/g, " ") }))];
}

function ContactFields({
  kind,
  contact,
  onChange,
  errors,
  jobTitle,
  required,
}: {
  kind: ContactKind;
  contact: ContactDraft;
  onChange: (patch: Partial<ContactDraft>) => void;
  errors: FieldErrors;
  jobTitle: boolean;
  required: boolean;
}) {
  return (
    <>
      <div className={local.split}>
        <TextField
          label={`${CONTACT_TITLES[kind]} name`}
          value={contact.name}
          onChange={(v) => onChange({ name: v })}
          maxLength={120}
          autoComplete="off"
          required={required}
          showRequired={required}
          error={errors[`contacts.${kind}.name`]}
        />
        {jobTitle && (
          <TextField
            label={`${CONTACT_TITLES[kind]} job title (optional)`}
            value={contact.jobTitle}
            onChange={(v) => onChange({ jobTitle: v })}
            maxLength={120}
            autoComplete="off"
            error={errors[`contacts.${kind}.job_title`]}
          />
        )}
      </div>
      <div className={local.split}>
        <TextField
          label={`${CONTACT_TITLES[kind]} email`}
          type="email"
          value={contact.email}
          onChange={(v) => onChange({ email: v })}
          maxLength={254}
          autoComplete="off"
          required={required}
          showRequired={required}
          error={errors[`contacts.${kind}.email`]}
        />
        <TextField
          label={`${CONTACT_TITLES[kind]} phone (optional)`}
          type="tel"
          value={contact.phone}
          onChange={(v) => onChange({ phone: v })}
          maxLength={32}
          autoComplete="off"
          error={errors[`contacts.${kind}.phone`]}
        />
      </div>
    </>
  );
}

/** The four contact roles. Billing, technical and support default to "the same
 * as the primary contact", which stores nothing: these are people to reach, not
 * Workeasy360 accounts, and nobody can sign in as one. */
export function ContactsForm({ value, onChange, errors = {}, primaryRequired = true }: FormProps & { primaryRequired?: boolean }) {
  const setContact = (kind: ContactKind, patch: Partial<ContactDraft>) =>
    onChange({ contacts: { ...value.contacts, [kind]: { ...value.contacts[kind], ...patch } } });
  return (
    <>
      <p className={styles.muted}>
        Contacts are people to reach, recorded as text. They are not Workeasy360 accounts and cannot sign in.
      </p>
      <fieldset className={local.checkGroup}>
        <legend>{CONTACT_TITLES.primary}</legend>
        <ContactFields
          kind="primary"
          contact={value.contacts.primary}
          onChange={(p) => setContact("primary", p)}
          errors={errors}
          jobTitle
          required={primaryRequired}
        />
      </fieldset>
      {SECONDARY_KINDS.map((kind) => (
        <fieldset key={kind} className={local.checkGroup}>
          <legend>{CONTACT_TITLES[kind]} (optional)</legend>
          <label className="w360-label">
            <input
              type="checkbox"
              checked={value.sameAsPrimary[kind]}
              onChange={(e) => onChange({ sameAsPrimary: { ...value.sameAsPrimary, [kind]: e.target.checked } })}
            />{" "}
            Same as primary contact
          </label>
          {!value.sameAsPrimary[kind] && (
            <ContactFields
              kind={kind}
              contact={value.contacts[kind]}
              onChange={(p) => setContact(kind, p)}
              errors={errors}
              jobTitle={false}
              required={false}
            />
          )}
        </fieldset>
      ))}
    </>
  );
}

/** Customer-facing support details of the reseller, and staff-only notes. */
export function SupportForm({ value, onChange, errors = {}, showNotes = true }: FormProps & { showNotes?: boolean }) {
  return (
    <>
      <fieldset className={local.checkGroup}>
        <legend>Customer-facing support (optional)</legend>
        <div className={local.split}>
          <TextField
            label="Support email"
            type="email"
            value={value.supportEmail}
            onChange={(v) => onChange({ supportEmail: v })}
            maxLength={254}
            autoComplete="off"
            error={errors["support.email"]}
          />
          <TextField
            label="Support phone"
            type="tel"
            value={value.supportPhone}
            onChange={(v) => onChange({ supportPhone: v })}
            maxLength={32}
            autoComplete="off"
            error={errors["support.phone"]}
          />
        </div>
        <TextField
          label="Support page"
          value={value.supportUrl}
          onChange={(v) => onChange({ supportUrl: v })}
          maxLength={300}
          autoComplete="off"
          spellCheck={false}
          hint="A plain https:// address, shown as text and never fetched."
          error={errors["support.url"]}
        />
      </fieldset>
      {showNotes && (
        <TextAreaField
          label="Internal notes (staff only)"
          value={value.internalNotes}
          onChange={(v) => onChange({ internalNotes: v })}
          maxLength={2000}
          hint="Never shown to the partner or its customers."
          error={errors["internal_notes"]}
        />
      )}
    </>
  );
}

/** Payout preferences. They are a note for whoever prepares payouts: nothing
 * runs on them and nothing is paid automatically. */
export function PayoutPrefsForm({
  value,
  onChange,
  errors = {},
  currency,
  currencyEditable = false,
}: FormProps & {
  /** The partner's payout currency, shown as a fact once the partner is active. */
  currency?: string;
  /** While the partner is a draft its preferred currency can still be chosen. */
  currencyEditable?: boolean;
}) {
  return (
    <>
      <p className={styles.muted}>
        Payouts are prepared, approved and marked paid by staff, one step at a time. These are preferences for whoever prepares them -
        Workeasy360 moves no money and collects no bank details.
      </p>
      {currencyEditable ? (
        <SelectField
          label="Preferred currency"
          value={value.payoutCurrency || currency || ""}
          onChange={(v) => onChange({ payoutCurrency: v })}
          options={SUPPORTED_CURRENCIES.map((c) => ({ value: c, label: c }))}
          hint="Prices, commission and payouts are kept in this currency. It can be changed until the partner is activated (and before it has prices of its own)."
          error={errors["payout.currency"]}
        />
      ) : (
        currency && (
          <p>
            Paid in <strong>{currency}</strong>. Commission is never converted from another currency.
          </p>
        )
      )}
      <SelectField
        label="Payout frequency"
        value={value.payoutFrequency}
        onChange={(v) => onChange({ payoutFrequency: v === "monthly" || v === "quarterly" ? v : "manual" })}
        options={[
          { value: "manual", label: "Manual - when staff prepare one" },
          { value: "monthly", label: "Monthly" },
          { value: "quarterly", label: "Quarterly" },
        ]}
        error={errors["payout.frequency"]}
      />
      <TextField
        label="Payout contact email (optional)"
        type="email"
        value={value.payoutContactEmail}
        onChange={(v) => onChange({ payoutContactEmail: v })}
        maxLength={254}
        autoComplete="off"
        hint="Who to tell when a payout is approved. No bank or card details are collected."
        error={errors["payout.contact_email"]}
      />
    </>
  );
}
