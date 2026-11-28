"use client";

import { LaunchButton } from "./LaunchButton";
import styles from "./AppCard.module.css";

/** What a product's connection being set up tells its holder: their access is
 * safe, and Open appears once the product is connected. Shared with tests. */
export function notConnectedCopy(productName: string): string {
  return `Your access to ${productName} is active. ${productName} is still being connected to WorkEasy360 sign-in - Open will appear here as soon as it is ready.`;
}

/**
 * Open for a product the organization holds - only when the server says it can
 * be opened at all (`launch_ready`, the launcher's own structural test). A
 * product bought before its integration is connected would otherwise show an
 * Open button the launcher refuses with "That product isn't available yet."
 * Here it says what is true instead. The launch endpoint still decides every
 * launch; this only stops offering a button that cannot work.
 */
export function HeldProductOpen({
  orgSlug,
  productCode,
  productName,
  launchReady,
}: {
  orgSlug: string;
  productCode: string;
  productName: string;
  /** Absent from an older answer: treated as ready, as before. */
  launchReady?: boolean;
}) {
  if (launchReady === false) {
    return (
      <p role="status" className={styles.hint}>
        {notConnectedCopy(productName)}
      </p>
    );
  }
  return <LaunchButton orgSlug={orgSlug} productCode={productCode} productName={productName} />;
}
