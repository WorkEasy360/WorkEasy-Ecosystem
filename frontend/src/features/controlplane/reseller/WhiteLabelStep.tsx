"use client";

import { Segmented } from "../widgets";
import styles from "../ControlPlane.module.css";
import type { StepProps } from "./draft";

/** Step 8: whether this reseller is also white-label. It is the same partner
 * gaining two capabilities, never a second account. */
export function WhiteLabelStep({ draft, set, errors }: Pick<StepProps, "draft" | "set" | "errors">) {
  return (
    <>
      <p className={styles.muted}>
        White-label lets a partner show its own brand, logo and custom domain to its customers. It is not a second account: it is the
        same partner gaining two capabilities, <strong>Manage branding</strong> and <strong>Manage custom domains</strong>.
      </p>
      <Segmented<"no" | "yes">
        legend="Enable white-label for this reseller?"
        options={[
          { value: "no", label: "Not now" },
          { value: "yes", label: "Yes, configure now" },
        ]}
        value={draft.whiteLabel ? "yes" : "no"}
        onChange={(v) => set("whiteLabel", v === "yes")}
        hint="You can enable white-label later from the partner's page."
        error={errors.white_label}
      />
      {draft.whiteLabel ? (
        <p role="status">
          Both capabilities are granted when the partner is saved. After it is created you can go straight on to the white-label setup:
          brand, logo and custom domain.
        </p>
      ) : (
        <p role="status">No branding or domain capability is added. The partner uses the standard Workeasy360 look.</p>
      )}
    </>
  );
}
