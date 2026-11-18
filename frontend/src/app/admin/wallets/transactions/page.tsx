"use client";

import { Suspense } from "react";
import { LoadingBlock } from "@/features/controlplane/kit";
import { TransactionControlsScreen } from "@/features/controlplane/AdminWallets";

/** Suspense: the screen keeps its tab and filters in the address (the AI Credits page
 * links here with them) and reads them itself (admin-ux phase 3). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="transactions" />}>
      <TransactionControlsScreen />
    </Suspense>
  );
}
