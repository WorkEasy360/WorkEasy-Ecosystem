"use client";

import { SupportForm } from "../ProfileForms";
import { Segmented, SelectField, TextField } from "../widgets";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import type { AdministratorDraft, StepProps } from "./draft";

/** Step 7: who runs the partner account, and the customer-facing support details.
 * No password is asked for or shown here: the person chooses their own through a
 * one-time link. */
export function AdministratorStep({ draft, set, patchProfile, errors }: StepProps) {
  const admin = draft.admin;
  const change = (patch: Partial<AdministratorDraft>) => set("admin", { ...admin, ...patch });
  return (
    <>
      <h3 className={styles.cardTitle}>Partner administrator</h3>
      <p className={styles.muted}>
        The person who will run this partner account. You never set or see a password: if the address has no Workeasy360 account one
        is created, and they choose their own through a one-time link. An existing account is simply attached. An administrator is
        needed to activate the partner, but a draft can be saved without one - leave these empty to add them later.
      </p>
      <div className={local.split}>
        <TextField
          label="First name (optional)"
          value={admin.firstName}
          onChange={(v) => change({ firstName: v })}
          maxLength={100}
          autoComplete="off"
          error={errors["administrator.first_name"]}
        />
        <TextField
          label="Last name (optional)"
          value={admin.lastName}
          onChange={(v) => change({ lastName: v })}
          maxLength={100}
          autoComplete="off"
          error={errors["administrator.last_name"]}
        />
      </div>
      <TextField
        label="Administrator's email"
        type="email"
        value={admin.email}
        onChange={(v) => change({ email: v })}
        maxLength={254}
        autoComplete="off"
        hint="Needed to activate the partner."
        error={errors["administrator.email"]}
      />
      <SelectField
        label="Role"
        value={admin.role}
        onChange={(v) => change({ role: v === "viewer" ? "viewer" : "admin" })}
        options={[
          { value: "admin", label: "Administrator (can act)" },
          { value: "viewer", label: "Viewer (read only)" },
        ]}
        hint="An active partner needs at least one administrator."
        error={errors["administrator.role"]}
      />
      <Segmented<"now" | "on_activation">
        legend="When should they be invited?"
        options={[
          { value: "now", label: "Send now" },
          { value: "on_activation", label: "After the partner is activated" },
        ]}
        value={admin.invitation}
        onChange={(v) => change({ invitation: v })}
        hint="Send now emails the setup link as soon as the partner is saved, even while it is still a draft. The other choice names them now and sends the link when the partner is activated."
        error={errors["administrator.send_invitation"]}
      />

      <h3 className={styles.cardTitle}>Customer support</h3>
      <SupportForm value={draft.profile} onChange={patchProfile} errors={errors} />
    </>
  );
}
