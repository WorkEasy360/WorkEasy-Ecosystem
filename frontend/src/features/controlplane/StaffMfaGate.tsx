"use client";

import { useState } from "react";
import { MfaSettings } from "@/features/security/MfaSettings";
import { StepUpForm } from "@/features/security/StepUpForm";
import styles from "@/features/console/Console.module.css";
import { usePortalRouter } from "@/lib/portalRouter";

/**
 * What the Super Admin shell shows when the backend answers `mfa_required`
 * (Phase 12, D-037): a staff session must verify a second factor before any
 * control-plane page - even a read - because staff views span every tenant.
 * Verified once per sign-in; the shell then loads as usual. Someone with no
 * authenticator sets one up here first, as the organization policy gate does.
 */
export function StaffMfaGate({ mfaEnrolled, onVerified }: { mfaEnrolled: boolean; onVerified: () => void }) {
  const router = usePortalRouter();
  const [enrolled, setEnrolled] = useState(mfaEnrolled);

  return (
    <main id="w360-main" className={styles.gate}>
      <div className={`w360-card ${styles.gateCard}`}>
        <h1 className={styles.gateTitle}>Two-step verification is required</h1>
        <p>The control plane is open to Workeasy360 staff only after verifying a code in this session.</p>
        {enrolled ? (
          <StepUpForm
            prompt="Enter a code from your authenticator app, or a recovery code."
            onVerified={onVerified}
            onCancel={() => router.push("/organizations/select")}
          />
        ) : (
          <>
            <p>Set up an authenticator app first. When it is confirmed, choose Continue.</p>
            <MfaSettings />
            <button type="button" className="w360-btn w360-btn-primary" onClick={() => setEnrolled(true)}>
              Continue
            </button>
          </>
        )}
      </div>
    </main>
  );
}
