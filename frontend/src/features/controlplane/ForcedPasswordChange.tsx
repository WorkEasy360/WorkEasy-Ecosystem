"use client";

import { ChangePasswordForm } from "@/features/console/ChangePasswordForm";
import styles from "./ControlPlane.module.css";

/**
 * Shown instead of the partner portal while the account's password is a
 * temporary one someone else chose (a development demo partner, for example).
 * The server refuses every other call until it is replaced, so this is the only
 * thing that can work; changing it clears the requirement and the page reloads
 * into the portal.
 */
export function ForcedPasswordChange() {
  return (
    <main id="w360-main" className={styles.main}>
      <div className="w360-container">
        <div className={`w360-card ${styles.card} ${styles.stack}`}>
          <h1>Choose your own password</h1>
          <p className={styles.lead}>
            Your account was set up with a temporary password. Enter it as the current password, then choose one only you
            know, before continuing.
          </p>
          <ChangePasswordForm onChanged={() => window.location.reload()} />
        </div>
      </div>
    </main>
  );
}
