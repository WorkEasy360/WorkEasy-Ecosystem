"use client";

import { useId, useState } from "react";
import { consoleApi } from "@/lib/api/console";
import { orgAccountApi, type BillingProfile, type BillingProfileChanges } from "@/lib/api/orgAccount";
import { Alert, Card, DescriptionList, Page, PageHeader, formatDateTime } from "@/features/controlplane/kit";
import { FormPopup, dialog } from "@/features/controlplane/Modal";
import { describeError, useLoad } from "@/features/controlplane/ui";
import { OpenAccountForm } from "./BillingScreen";
import { ContactOrgAdmin } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";
import styles from "./wallets/Wallets.module.css";

/**
 * Billing Profile (User Admin Panel, 2026-11-11): who the organization's invoices
 * are made out to and where - business name, billing address, tax details and the
 * billing email. Read with `platform.billing.view`; changed in a popup by owners and
 * billing managers (`platform.billing.manage`). The billing currency, how invoices
 * are collected and the price region are Workeasy360's to set and never edited here.
 */

const FIELDS: { key: keyof BillingProfileChanges; label: string; autoComplete?: string; span?: boolean; max: number; hint?: string }[] = [
  { key: "legal_name", label: "Business name", autoComplete: "organization", span: true, max: 200 },
  { key: "address_line1", label: "Address line 1", autoComplete: "address-line1", span: true, max: 200 },
  { key: "address_line2", label: "Address line 2", autoComplete: "address-line2", span: true, max: 200 },
  { key: "city", label: "City", autoComplete: "address-level2", max: 100 },
  { key: "state", label: "State or region", autoComplete: "address-level1", max: 100 },
  { key: "postal_code", label: "Postal code", autoComplete: "postal-code", max: 20 },
  { key: "country", label: "Country code", autoComplete: "country", max: 2, hint: "Two letters, such as IN." },
  { key: "tax_reference", label: "Tax ID (GSTIN / VAT)", max: 64 },
  { key: "billing_email", label: "Billing email", autoComplete: "email", max: 254, hint: "Where invoices and payment receipts are sent." },
];

const COLLECTION: Record<string, string> = {
  automatic: "Paid online",
  invoice: "Invoiced by Workeasy360",
  partner_billed: "Billed through your partner",
  partner_wallet: "Paid by your partner",
};

function address(p: BillingProfile): string {
  const lines = [p.address_line1, p.address_line2, [p.city, p.state, p.postal_code].filter(Boolean).join(", "), p.country].filter(Boolean);
  return lines.length ? lines.join("\n") : "Not set";
}

function EditProfile({ slug, profile, onSaved }: { slug: string; profile: BillingProfile; onSaved: () => void }) {
  const formId = useId();
  const [values, setValues] = useState<Record<string, string>>({});
  const set = (key: string, value: string) => setValues((v) => ({ ...v, [key]: value }));
  const country = (values.country ?? "").trim();
  const valid = country === "" || /^[A-Za-z]{2}$/.test(country);
  return (
    <FormPopup
      label="Edit billing information"
      buttonClassName="w360-btn w360-btn-primary"
      title="Edit billing information"
      intro="Shown on your invoices from now on. Invoices already issued keep the details they were issued with."
      submitLabel="Save billing information"
      canSubmit={valid}
      onOpen={() =>
        setValues(Object.fromEntries(FIELDS.map((f) => [f.key, String(profile[f.key] ?? "")])))
      }
      onSubmit={async () => {
        const changes = Object.fromEntries(
          FIELDS.filter((f) => (values[f.key] ?? "") !== String(profile[f.key] ?? "")).map((f) => [f.key, (values[f.key] ?? "").trim()]),
        ) as BillingProfileChanges;
        if (Object.keys(changes).length === 0) return "Nothing to change.";
        await orgAccountApi.updateBillingProfile(slug, changes);
        onSaved();
        return "Billing information saved.";
      }}
    >
      {FIELDS.map((f) => (
        <div key={f.key} className={f.span ? `w360-field ${dialog.span}` : "w360-field"}>
          <label htmlFor={`${formId}-${f.key}`}>{f.label}</label>
          <input
            id={`${formId}-${f.key}`}
            className="w360-input"
            type={f.key === "billing_email" ? "email" : "text"}
            autoComplete={f.autoComplete}
            maxLength={f.max}
            value={values[f.key] ?? ""}
            onChange={(e) => set(f.key, f.key === "country" ? e.target.value.toUpperCase() : e.target.value)}
            aria-describedby={f.hint ? `${formId}-${f.key}-hint` : undefined}
            aria-invalid={f.key === "country" && !valid ? true : undefined}
          />
          {f.hint && (
            <p id={`${formId}-${f.key}-hint`} className="w360-hint">
              {f.hint}
            </p>
          )}
        </div>
      ))}
    </FormPopup>
  );
}

export function BillingProfileScreen() {
  const { slug, can } = useOrgAccess();
  const canView = can("platform.billing.view");
  const canManage = can("platform.billing.manage");
  const [load, reload] = useLoad(
    () => (canView ? orgAccountApi.billingProfile(slug) : Promise.reject(new Error("forbidden"))),
    [slug, canView],
  );
  const [opening, setOpening] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(0);

  const header = (profile: BillingProfile | null) => (
    <PageHeader
      title="Billing Profile"
      subtitle="Who your invoices are made out to, and where they are sent."
      primary={profile && canManage ? <EditProfile key={saved} slug={slug} profile={profile} onSaved={() => { setSaved((n) => n + 1); reload(); }} /> : undefined}
    />
  );

  if (!canView) {
    return (
      <Page>
        {header(null)}
        <ContactOrgAdmin what="the billing profile" />
      </Page>
    );
  }
  if (load.status === "loading") {
    return (
      <Page>
        {header(null)}
        <Card title="Billing details">
          <p aria-live="polite">Loading the billing profile…</p>
        </Card>
      </Page>
    );
  }
  if (load.status !== "ready") {
    return (
      <Page>
        {header(null)}
        <Alert tone="danger" title="The billing profile could not be loaded">
          {load.status === "error" ? load.message : "You don't have access to it."}
        </Alert>
      </Page>
    );
  }
  const profile = load.data.billing_account;
  if (!profile) {
    return (
      <Page>
        {header(null)}
        <Card title="Open a billing account" description="Choose your billing currency once. Every subscription and wallet uses it.">
          {problem && <Alert tone="danger" title={problem} />}
          {can("commercial.subscription.manage") ? (
            <OpenAccountForm
              busy={opening}
              onOpen={(body) => {
                setOpening(true);
                setProblem(null);
                consoleApi.openBillingAccount(slug, body).then(
                  () => {
                    setOpening(false);
                    reload();
                  },
                  (err: unknown) => {
                    setOpening(false);
                    setProblem(describeError(err));
                  },
                );
              }}
            />
          ) : (
            <ContactOrgAdmin what="the billing profile" />
          )}
        </Card>
      </Page>
    );
  }
  return (
    <Page>
      {header(profile)}
      <div className={styles.fieldGrid}>
        <Card title="Business">
          <DescriptionList
            items={[
              { label: "Business name", value: profile.legal_name || "Not set" },
              { label: "Billing address", value: <span className={styles.preline}>{address(profile)}</span> },
              { label: "Tax ID", value: profile.tax_reference || "Not set" },
            ]}
          />
        </Card>
        <Card title="Billing">
          <DescriptionList
            items={[
              { label: "Billing email", value: profile.billing_email || "Not set" },
              { label: "Billing currency", value: profile.currency },
              { label: "How invoices are paid", value: COLLECTION[profile.collection_mode] ?? profile.collection_mode },
              { label: "Last updated", value: formatDateTime(profile.updated_at) },
            ]}
          />
        </Card>
      </div>
      {!canManage && <ContactOrgAdmin what="the billing profile" />}
      <p className={styles.muted}>The billing currency and how invoices are collected are set by Workeasy360. Ask support to change them.</p>
    </Page>
  );
}
