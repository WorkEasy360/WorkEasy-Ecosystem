"use client";

import { Suspense } from "react";
import { AllPartnerCustomers } from "@/features/controlplane/PartnerCustomers";

/** Partners → Customers. Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading partner customers…</p>}>
      <AllPartnerCustomers />
    </Suspense>
  );
}
