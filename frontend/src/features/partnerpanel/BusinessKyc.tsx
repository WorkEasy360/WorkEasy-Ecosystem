"use client";

import { BusinessProfile } from "./Account";
import { PartnerKycScreen } from "./KycApplication";
import { PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/**
 * Settings → Business & KYC (ADR-063, the specification's 2.2 and 11.1): the
 * business profile, then the KYC application - its required documents, upload,
 * submission, the correction asked for, the Admin's decision and its reason, and
 * the history of decisions. Open before approval: it is how a partner gets
 * approved. Only the partner's own owner or administrator may change the
 * application; the server checks that on every write.
 */
export function BusinessKyc() {
  const { id, partner } = usePanel();
  const onboarding = partner?.onboarding;
  return (
    <div className={styles.page}>
      <PageHeader title="Business & KYC" lead="Your business details and the KYC application Workeasy360 reviews before your account can trade." />
      {onboarding && (
        <ul className={styles.statusStrip} aria-label="Where your account stands">
          <li>
            KYC: <strong>{KYC_WORDS[onboarding.kyc_status] ?? onboarding.kyc_status}</strong>
          </li>
          <li>
            Account: <strong>{ACCOUNT_WORDS[onboarding.account_status] ?? onboarding.account_status}</strong>
          </li>
          {onboarding.legacy && <li>Your account was active before KYC was required.</li>}
        </ul>
      )}
      <section className={styles.panel} aria-labelledby="business-profile">
        <h2 id="business-profile">Business profile</h2>
        <BusinessProfile />
      </section>
      <section className={styles.panel} aria-label="KYC application">
        <PartnerKycScreen partnerId={id} embedded />
      </section>
    </div>
  );
}

export const KYC_WORDS: Record<string, string> = {
  draft: "Draft",
  submitted: "Submitted",
  under_review: "Under Review",
  correction_required: "Correction Required",
  approved: "Approved",
  rejected: "Rejected",
};

export const ACCOUNT_WORDS: Record<string, string> = {
  draft: "Not yet active",
  active: "Active",
  suspended: "Suspended",
  terminated: "Ended",
};
