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
  const low = useSearchParams().get("low") === "1";
  return <PlatformWalletScreen key={low ? "low" : "all"} initialLow={low} />;
}
