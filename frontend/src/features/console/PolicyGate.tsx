"use client";

import Link from "next/link";
import { logout } from "@/lib/api/auth";
import type { AccessPolicyDenial } from "@/lib/api/console";
import { MfaSettings } from "@/features/security/MfaSettings";
import { StepUpForm } from "@/features/security/StepUpForm";
import { ChangePasswordForm } from "./ChangePasswordForm";
import styles from "./Console.module.css";
import { usePortalRouter } from "@/lib/portalRouter";

/**
 * What the organization console shows when the organization's own access
 * policy refused this session (after membership was verified). Each denial
 * names its remedy, and the remedies that happen in Workeasy360 happen right
 * here - the pages under the organization are exactly what the policy is
 * refusing, so they cannot host them:
 *
 * - `mfa_required`: set up an authenticator app, or verify a code;
 * - `password_change_required`: change the password now;
 * - `reauthentication_required`: sign in again;
 * - `network_not_allowed`: nothing to do from here - connect from an allowed
 *   network, or choose another organization.
 */
export function PolicyGate({
  reason,
  mfaEnrolled,
  onResolved,
}: {
  reason: AccessPolicyDenial;
  mfaEnrolled: boolean;
  onResolved: () => void;
}) {
  const router = usePortalRouter();

  const signInAgain = () => logout().finally(() => router.replace("/login"));

  return (
    <main id="w360-main" className={styles.gate}>
      <div className={`w360-card ${styles.gateCard}`}>
        {reason === "mfa_required" && (
          <>
            <h1 className={styles.gateTitle}>Two-step verification is required</h1>
            <p>This organization requires everyone to verify a code from an authenticator app before continuing.</p>
            {mfaEnrolled ? (
              <StepUpForm
                prompt="Enter a code from your authenticator app, or a recovery code."
                onVerified={onResolved}
                onCancel={() => router.push("/organizations/select")}
              />
            ) : (
              <>
                <p>Set up an authenticator app first. When it is confirmed, choose Continue.</p>
                <MfaSettings />
                <button type="button" className="w360-btn w360-btn-primary" onClick={onResolved}>
                  Continue
                </button>
              </>
            )}
          </>
        )}
        {reason === "password_change_required" && (
          <>
            <h1 className={styles.gateTitle}>Update your password</h1>
            <p>
              Your password is shorter than this organization requires. Choose a new one to continue - your
              other sessions will be signed out.
            </p>
            <ChangePasswordForm onChanged={onResolved} />
          </>
        )}
        {reason === "reauthentication_required" && (
          <>
            <h1 className={styles.gateTitle}>Sign in again</h1>
            <p>Your session for this organization has ended. Sign in again to continue.</p>
            <button type="button" className="w360-btn w360-btn-primary" onClick={signInAgain}>
              Sign in again
            </button>
          </>
        )}
        {reason === "network_not_allowed" && (
          <>
            <h1 className={styles.gateTitle}>Not available from this network</h1>
            <p>
              This organization only allows access from approved networks, and this connection isn&apos;t one
              of them. Connect from your organization&apos;s network, or ask an administrator.
            </p>
          </>
        )}
        <p>
          <Link href="/organizations/select">Choose another organization</Link>
        </p>
      </div>
    </main>
  );
}
