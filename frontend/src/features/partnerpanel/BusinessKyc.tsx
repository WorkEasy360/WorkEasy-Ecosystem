"use client";

import { Icon } from "@/components/sidebar/icons";
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
        <section className={styles.panel} aria-labelledby="kyc-progress">
          <h2 id="kyc-progress">Onboarding progress</h2>
          <KycProgress kyc={onboarding.kyc_status} account={onboarding.account_status} />
          {onboarding.legacy && <p className={styles.muted}>Your account was active before KYC was required.</p>}
        </section>
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

/** The six onboarding steps of the 11 November 2026 brief, in order. */
export const KYC_STEPS = [
  "Account Verification",
  "Business Profile",
  "KYC Documents",
  "Application Submission",
  "Admin Review",
  "Approval / Activation",
] as const;

type StepState = "done" | "current" | "attention" | "upcoming";

/**
 * Where each step stands, from the two facts the server gives: the KYC status
 * and the account status. Signing in means the address was verified.
 */
export function kycStepStates(kyc: string, account: string): { states: StepState[]; note: string } {
  const all = (doneThrough: number, at: number, state: StepState): StepState[] =>
    KYC_STEPS.map((_, i) => (i < doneThrough ? "done" : i === at ? state : "upcoming"));
  switch (kyc) {
    case "correction_required":
      return {
        states: all(2, 2, "attention"),
        note: "Workeasy360 asked for corrections. The reviewer's note below says what to change; update the application and submit it again.",
      };
    case "submitted":
      return { states: all(4, 4, "current"), note: "Your application is with Workeasy360. You are emailed when there is a decision." };
    case "under_review":
      return { states: all(4, 4, "current"), note: "A reviewer is checking your application. You are emailed when there is a decision." };
    case "rejected":
      return {
        states: all(4, 4, "attention"),
        note: "Your application was not approved. The reason is below; contact support to ask what you can do next.",
      };
    case "approved":
      return account === "active"
        ? { states: all(6, -1, "done"), note: "Your KYC is approved and your account is active." }
        : { states: all(5, 5, "current"), note: "Your KYC is approved. Workeasy360 activates your account next; you are emailed." };
    default:
      return {
        states: all(1, 1, "current"),
        note: "Complete your business profile, upload the KYC documents and submit your application.",
      };
  }
}

const STEP_WORDS: Record<StepState, string> = {
  done: "Done",
  current: "In progress",
  attention: "Needs your attention",
  upcoming: "Not started",
};

/** The onboarding progress: six numbered steps, each with its state in words
 * (never colour alone), the current one marked for assistive technology. */
export function KycProgress({ kyc, account }: { kyc: string; account: string }) {
  const { states, note } = kycStepStates(kyc, account);
  return (
    <div className={styles.kycProgress}>
      <ol className={styles.kycSteps}>
        {KYC_STEPS.map((step, i) => (
          <li key={step} data-state={states[i]} aria-current={states[i] === "current" || states[i] === "attention" ? "step" : undefined}>
            <span className={styles.kycDot} aria-hidden="true">
              {states[i] === "done" ? <Icon name="check" size={14} /> : states[i] === "attention" ? "!" : i + 1}
            </span>
            <span className={styles.kycStepText}>
              <span className={styles.kycStepName}>{step}</span>
              <span className={styles.kycStepState}>
                {kyc === "rejected" && i === 4 ? "Rejected" : kyc === "correction_required" && i === 2 ? "Correction required" : STEP_WORDS[states[i]]}
              </span>
            </span>
          </li>
        ))}
      </ol>
      <p className={styles.kycNote}>{note}</p>
    </div>
  );
}
