"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "@/features/controlplane/kit";
import { PlatformWalletScreen } from "@/features/controlplane/AdminWallets";

/** Suspense: the page reads "low balance only" from the address (the dashboard links here). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="wallets" />}>
      <WalletsFromAddress />
    </Suspense>
  );
}

function WalletsFromAddress() {
  const params = useSearchParams();
  const low = params.get("low") === "1";
  // ADR-063: the partner list's "View Partner Wallet" names its partner.
  const ownerType = params.get("owner_type") === "partner" ? "partner" : "";
  const q = (params.get("q") ?? "").slice(0, 120);
  return <PlatformWalletScreen key={`${low}-${ownerType}-${q}`} initialLow={low} initialOwnerType={ownerType} initialQuery={q} />;
}
