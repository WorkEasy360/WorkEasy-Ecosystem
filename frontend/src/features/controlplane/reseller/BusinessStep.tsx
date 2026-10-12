"use client";

import { SUPPORTED_CURRENCIES } from "../amounts";
import { BusinessDetailsForm, ContactsForm } from "../ProfileForms";
import { Segmented, SelectField, TextField } from "../widgets";
import styles from "../ControlPlane.module.css";
import type { StepProps } from "./draft";

/** Step 1: who the company is, and the three facts a partner cannot exist without. */
export function BusinessStep({
  draft,
  set,
  patchProfile,
  errors,
  onName,
}: StepProps & { onName: (value: string) => void }) {
  return (
    <>
      <p className={styles.muted}>Fields marked * are required. Everything else is optional and can be added later from the partner&apos;s page.</p>
      <TextField
        label="Company or partner name"
        value={draft.profile.displayName}
        onChange={onName}
        maxLength={120}
        autoComplete="off"
        required
        showRequired
        error={errors.display_name ?? errors["business.display_name"]}
      />
      <TextField
        label="Code"
        value={draft.code}
        onChange={(v) => {
          set("code", v.toLowerCase());
          set("codeEdited", true);
        }}
        maxLength={48}
        autoComplete="off"
        spellCheck={false}
        required
        showRequired
        hint="A permanent identifier: lower-case letters, digits and hyphens. Suggested from the name; it cannot be changed later."
        error={errors.code}
      />
      <SelectField
        label="Payout currency"
        value={draft.currency}
        onChange={(v) => set("currency", v)}
        options={SUPPORTED_CURRENCIES.map((c) => ({ value: c, label: c }))}
        required
        showRequired
        hint="Commission and payouts are in this one currency, fixed when the partner is created. It is never converted."
        error={errors.payout_currency}
      />
      <TextField
        label="Legal business name (optional)"
        value={draft.profile.legalName}
        onChange={(v) => patchProfile({ legalName: v })}
        maxLength={200}
        autoComplete="off"
        hint="Only if it differs from the name above."
        error={errors.legal_name ?? errors["business.legal_name"]}
      />
      <BusinessDetailsForm
        value={draft.profile}
        onChange={patchProfile}
        errors={errors}
        showNames={false}
        required={["businessEmail", "country"]}
      />
      <Segmented<"draft" | "active">
        legend="Partner status"
        options={[
          { value: "draft", label: "Draft" },
          { value: "active", label: "Active (activate when set up)" },
        ]}
        value={draft.status}
        onChange={(v) => set("status", v)}
        hint="A draft exists but can do nothing until it is activated. This only chooses which action is highlighted on the last step: nothing is activated until you confirm it there. The primary contact is asked on the next step."
      />
    </>
  );
}

/** Step 2: who to reach. Contacts are people recorded as text, never accounts. */
export function ContactsStep({ draft, patchProfile, errors }: StepProps) {
  return (
    <>
      <p className={styles.muted}>Fields marked * are required.</p>
      <ContactsForm value={draft.profile} onChange={patchProfile} errors={errors} />
    </>
  );
}
