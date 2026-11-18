"use client";

import { Suspense } from "react";
import { LoadingBlock } from "@/features/controlplane/kit";
import { PlatformWalletScreen } from "@/features/controlplane/AdminWallets";

/** Suspense: the list keeps its filters in the address ("low balance only" from the
 * dashboard, the partner list's "View Partner Wallet") and reads them itself
 * (admin-ux phase 3) - no remount on a filter change, so focus stays put. */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="wallets" />}>
      <PlatformWalletScreen />
    </Suspense>
  );
}
