"use client";

import { Suspense } from "react";
import { WalletUsageScreen } from "@/features/console/wallets/WalletScreens";

/** Wallets & Credits: usage by product and service, with the CSV export.
 * Suspense: `?meter=` and `?product=` preset the filters. */
export default function WalletUsagePage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading usage…</p>}>
      <WalletUsageScreen />
    </Suspense>
  );
}
