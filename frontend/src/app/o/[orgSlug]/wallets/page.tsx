"use client";

import { Suspense } from "react";
import { WalletsOverviewScreen } from "@/features/console/wallets/WalletScreens";

/** Wallets & Credits: the three balances, each on its own (User Admin Panel). */
export default function WalletsPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading wallets…</p>}>
      <WalletsOverviewScreen />
    </Suspense>
  );
}
