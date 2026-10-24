"use client";

import { useState } from "react";
import { countryOptions } from "@/features/controlplane/countries";
import { SelectField, TextAreaField, TextField } from "@/features/controlplane/widgets";
import type { OwnProfile, UpgradeProposal, UpgradeRequest } from "@/lib/api/partnerPanel";
import styles from "./PartnerPanel.module.css";

/**
 * The forms of the upgrade wizard (ADR-049), one per step.
 *
 * Every form only collects and hands back a value; the wizard saves it, and the
 * server validates it again in full - these screens grant nothing and decide
 * nothing. The business step edits the partner's own profile (the same fields as
 * Account, so nothing is asked twice and nothing is copied); the other steps edit
 * the request. The partner's sign-in is never asked for: it is the one it already
 * has.
 */

export interface StepFormProps {
  busy: boolean;
  /** Called with whatever the step collected; the wizard saves it and moves on. */
  onNext: (value: unknown) => void;
  onBack: () => void;
  backLabel?: string;
  nextLabel?: string;
}

function Actions({ busy, onBack, backLabel = "Back", nextLabel = "Save and continue", disabled }: Omit<StepFormProps, "onNext"> & { disabled?: boolean }) {
  return (
    <div className={styles.wizardActions}>
      <button type="button" className="w360-btn w360-btn-secondary" onClick={onBack}>
        {backLabel}
      </button>
      <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || disabled}>
        {nextLabel}
      </button>
    </div>
  );
}

// --- 1. business (the partner's own profile) -----------------------------------------------------

export type BusinessValue = {
  business: Record<string, string>;
  contacts: Record<string, unknown>;
};

export function BusinessStep({ profile, ...props }: StepFormProps & { profile: OwnProfile }) {
  const b = profile.business;
  const primary = profile.contacts.primary ?? {};
  const billing = profile.contacts.billing ?? {};
  const [name, setName] = useState(b.display_name);
  const [legal, setLegal] = useState(b.legal_name);
  const [email, setEmail] = useState(b.business_email);
  const [phone, setPhone] = useState(b.phone);
  const [website, setWebsite] = useState(b.website);
  const [country, setCountry] = useState(b.country);
  const [tax, setTax] = useState(b.tax_number);
  const [pName, setPName] = useState(primary.name ?? "");
  const [pEmail, setPEmail] = useState(primary.email ?? "");
  const [bName, setBName] = useState(billing.same_as_primary ? "" : (billing.name ?? ""));
  const [bEmail, setBEmail] = useState(billing.same_as_primary ? "" : (billing.email ?? ""));

  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        const value: BusinessValue = {
          business: {
            display_name: name.trim(),
            legal_name: legal.trim(),
            business_email: email.trim(),
            phone: phone.trim(),
            website: website.trim(),
            country,
            tax_number: tax.trim(),
          },
          contacts: {
            primary: { name: pName.trim(), email: pEmail.trim() },
            billing: bName.trim() || bEmail.trim() ? { name: bName.trim(), email: bEmail.trim() } : { same_as_primary: true },
          },
        };
        props.onNext(value);
      }}
    >
      <p className={styles.muted}>
        These are your existing business details - change only what the upgrade needs. They are saved to your account, not to a new one.
      </p>
      <fieldset className={styles.panel}>
        <legend>Business</legend>
        <div className={styles.formGrid}>
          <TextField label="Business name" value={name} onChange={setName} required showRequired maxLength={120} />
          <TextField label="Legal name" value={legal} onChange={setLegal} maxLength={200} />
          <TextField label="Business email" value={email} onChange={setEmail} type="email" required showRequired />
          <TextField label="Phone" value={phone} onChange={setPhone} type="tel" required showRequired />
          <SelectField label="Country" value={country} onChange={setCountry} options={countryOptions("Choose a country")} required showRequired />
          <TextField label="Tax / GST / VAT number" value={tax} onChange={setTax} maxLength={64} />
          <TextField label="Website" value={website} onChange={setWebsite} type="url" hint="Must start with https://" />
        </div>
      </fieldset>
      <fieldset className={styles.panel}>
        <legend>Contacts</legend>
        <div className={styles.formGrid}>
          <TextField label="Primary contact name" value={pName} onChange={setPName} required showRequired maxLength={120} />
          <TextField label="Primary contact email" value={pEmail} onChange={setPEmail} type="email" required showRequired />
          <TextField label="Billing contact name" value={bName} onChange={setBName} maxLength={120} hint="Leave both billing fields empty to use the primary contact." />
          <TextField label="Billing contact email" value={bEmail} onChange={setBEmail} type="email" />
        </div>
      </fieldset>
      <Actions {...props} />
    </form>
  );
}

// --- brand (white-label) ----------------------------------------------------------------------------

export interface BrandValue {
  brand: NonNullable<UpgradeProposal["brand"]>;
  supportPhone: string;
}

export function BrandStep({ request, profile, ...props }: StepFormProps & { request: UpgradeRequest; profile: OwnProfile }) {
  const saved = request.proposal.brand;
  const [brandName, setBrandName] = useState(saved?.brand_name ?? "");
  const [primary, setPrimary] = useState(saved?.primary_color ?? "");
  const [secondary, setSecondary] = useState(saved?.secondary_color ?? "");
  const [supportEmail, setSupportEmail] = useState(saved?.support_email ?? profile.support.email);
  const [supportPhone, setSupportPhone] = useState(profile.support.phone);

  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        const value: BrandValue = {
          brand: {
            brand_name: brandName.trim(),
            primary_color: primary.trim(),
            secondary_color: secondary.trim(),
            support_email: supportEmail.trim(),
          },
          supportPhone: supportPhone.trim(),
        };
        props.onNext(value);
      }}
    >
      <fieldset className={styles.panel}>
        <legend>Your brand</legend>
        <div className={styles.formGrid}>
          <TextField label="Brand name" value={brandName} onChange={setBrandName} required showRequired maxLength={80} />
          <TextField
            label="Primary brand colour"
            value={primary}
            onChange={setPrimary}
            hint="A hex colour such as #1a73e8. Optional."
            maxLength={7}
            pattern="^(#[0-9a-fA-F]{6})?$"
          />
          <TextField
            label="Secondary colour"
            value={secondary}
            onChange={setSecondary}
            hint="A hex colour such as #34a853. Optional."
            maxLength={7}
            pattern="^(#[0-9a-fA-F]{6})?$"
          />
          <TextField label="Support email" value={supportEmail} onChange={setSupportEmail} type="email" />
          <TextField label="Support phone" value={supportPhone} onChange={setSupportPhone} type="tel" />
        </div>
        <p className={styles.muted}>
          You add your logo in Brand Settings once the upgrade is approved. Favicons are not supported yet.
        </p>
      </fieldset>
      <Actions {...props} />
    </form>
  );
}

// --- products -------------------------------------------------------------------------------------------

export function ProductsStep({
  request,
  products,
  ...props
}: StepFormProps & { request: UpgradeRequest; products: { code: string; name: string }[] }) {
  const [chosen, setChosen] = useState<string[]>(request.proposal.products ?? []);
  const toggle = (code: string) => setChosen((cur) => (cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code]));
  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        props.onNext(chosen);
      }}
    >
      <fieldset className={`${styles.panel} ${styles.checks}`}>
        <legend>Products you want to sell</legend>
        {products.length === 0 ? (
          <p>No products are on sale yet, so there is nothing to choose.</p>
        ) : (
          products.map((p) => (
            <label key={p.code}>
              <input type="checkbox" checked={chosen.includes(p.code)} onChange={() => toggle(p.code)} />
              <span>{p.name}</span>
            </label>
          ))
        )}
      </fieldset>
      <p className={styles.muted}>Workeasy360 confirms which products you may sell when it reviews the request.</p>
      <Actions {...props} disabled={chosen.length === 0} />
    </form>
  );
}

// --- pricing ----------------------------------------------------------------------------------------------

export function PricingStep({ request, ...props }: StepFormProps & { request: UpgradeRequest }) {
  const saved = request.proposal.pricing;
  const [preference, setPreference] = useState<"standard" | "negotiated">(saved?.preference ?? "standard");
  const [notes, setNotes] = useState(saved?.notes ?? "");
  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        props.onNext({ preference, notes: notes.trim() });
      }}
    >
      <fieldset className={`${styles.panel} ${styles.checks}`}>
        <legend>How would you like to be priced?</legend>
        <label>
          <input type="radio" name="pricing" checked={preference === "standard"} onChange={() => setPreference("standard")} />
          <span>Standard Workeasy360 prices</span>
        </label>
        <label>
          <input type="radio" name="pricing" checked={preference === "negotiated"} onChange={() => setPreference("negotiated")} />
          <span>I would like to discuss my pricing</span>
        </label>
      </fieldset>
      <TextAreaField label="Notes for the review" value={notes} onChange={setNotes} maxLength={500} rows={4} />
      <p className={styles.muted}>
        Prices and commission are set by Workeasy360 when it approves the request. This only tells the reviewer what you are looking for.
      </p>
      <Actions {...props} />
    </form>
  );
}

// --- commercial -------------------------------------------------------------------------------------------

const toCount = (text: string): number | null => (text.trim() === "" ? null : Number(text));

export function CommercialStep({ request, ...props }: StepFormProps & { request: UpgradeRequest }) {
  const saved = request.proposal.commercial;
  const [customers, setCustomers] = useState(saved?.expected_customers == null ? "" : String(saved.expected_customers));
  const [team, setTeam] = useState(saved?.team_size == null ? "" : String(saved.team_size));
  const [management, setManagement] = useState(saved?.customer_management ?? "");
  const [notes, setNotes] = useState(saved?.notes ?? "");
  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        props.onNext({
          expected_customers: toCount(customers),
          team_size: toCount(team),
          customer_management: management.trim(),
          notes: notes.trim(),
        });
      }}
    >
      <fieldset className={styles.panel}>
        <legend>About your business</legend>
        <div className={styles.formGrid}>
          <TextField label="Customers you expect in the first year" value={customers} onChange={setCustomers} type="number" min={0} max={1000000} />
          <TextField label="People on your team" value={team} onChange={setTeam} type="number" min={0} max={1000000} />
        </div>
        <TextAreaField label="How will you manage your customers?" value={management} onChange={setManagement} maxLength={500} rows={3} />
        <TextAreaField label="Anything else the reviewer should know" value={notes} onChange={setNotes} maxLength={500} rows={3} />
      </fieldset>
      <p className={styles.muted}>
        Any set-up fee or subscription for this upgrade is set by Workeasy360 and agreed with you before it is approved. Nothing is charged
        here.
      </p>
      <Actions {...props} />
    </form>
  );
}

// --- domain (white-label) ----------------------------------------------------------------------------------

export function DomainStep({ request, ...props }: StepFormProps & { request: UpgradeRequest }) {
  const [hostname, setHostname] = useState(request.proposal.domain?.hostname ?? "");
  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        props.onNext({ hostname: hostname.trim() });
      }}
    >
      <fieldset className={styles.panel}>
        <legend>Custom domain</legend>
        <TextField
          label="Domain you want to use"
          value={hostname}
          onChange={setHostname}
          hint="For example app.yourcompany.com. Optional - your brand works on the Workeasy360 address without one."
          maxLength={253}
        />
        <p className={styles.muted}>
          After approval you verify the domain with a DNS record in Custom Domain. Nothing is served on it until that proof is seen.
        </p>
      </fieldset>
      <Actions {...props} />
    </form>
  );
}
