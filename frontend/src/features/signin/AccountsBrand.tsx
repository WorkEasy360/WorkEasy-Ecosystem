import { Logo } from "@/components/brand/Logo";
import styles from "./AccountsBrand.module.css";

/** ADR-067: the customer-facing name of the central sign-in host
 * (accounts.workeasy360.com) - the product sign-in, the access chooser, the
 * product sign-out and Switch Account. */
export const ACCOUNTS_NAME = "WorkEasy360 Accounts";

/** The page title for one of WorkEasy360 Accounts' pages. */
export function accountsTitle(page?: string): string {
  return page ? `${page} - ${ACCOUNTS_NAME}` : ACCOUNTS_NAME;
}

/**
 * The service's mark above a WorkEasy360 Accounts card: the WorkEasy360 logo
 * and the word "Accounts". The logo carries the name "WorkEasy360" for
 * assistive technology, so the whole mark reads "WorkEasy360 Accounts".
 */
export function AccountsBrand() {
  return (
    <p className={styles.brand} data-accounts-brand="">
      <Logo />
      <span className={styles.service}>Accounts</span>
    </p>
  );
}
