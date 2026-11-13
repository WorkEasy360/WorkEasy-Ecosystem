"use client";

import { Suspense } from "react";
import { WalletLedgerScreen } from "@/features/console/wallets/WalletScreens";

/** Wallets & Credits: the Consumption Wallet and its transactions. Suspense: `?action=` opens the
 * spend dialog on arrival. */
export default function ConsumptionWalletPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading the wallet…</p>}>
      <WalletLedgerScreen kind="consumption" />
    </Suspense>
  );
}
