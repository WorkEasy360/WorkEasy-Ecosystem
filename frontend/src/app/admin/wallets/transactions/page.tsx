"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "@/features/controlplane/kit";
import { TransactionControlsScreen } from "@/features/controlplane/AdminWallets";

/** Suspense: the AI Credits page links here with a tab and filters in the address. */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="transactions" />}>
      <ControlsFromAddress />
    </Suspense>
  );
}

function ControlsFromAddress() {
  const params = useSearchParams();
  return (
    <TransactionControlsScreen
      key={params.toString()}
      initialTab={params.get("tab") === "events" ? "events" : "transactions"}
      initialKind={params.get("kind") ?? undefined}
      initialType={params.get("type") ?? undefined}
      initialMeter={params.get("meter") ?? undefined}
    />
  );
}
