"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "@/features/controlplane/kit";
import { CommissionEntriesScreen } from "@/features/controlplane/AdminBillingViews";

/** Suspense: the page reads one partner's id from the address. */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="commission entries" />}>
      <EntriesFromAddress />
    </Suspense>
  );
}

function EntriesFromAddress() {
  const partner = (useSearchParams().get("partner") ?? "").trim();
  return <CommissionEntriesScreen key={partner} partnerId={partner} />;
}
