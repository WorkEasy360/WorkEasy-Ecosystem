"use client";

import { Suspense } from "react";
import { LoadingBlock } from "@/features/controlplane/kit";
import { InvoicesScreen } from "@/features/controlplane/AdminBillingViews";

/** Suspense: the list keeps its filters in the address (an invoice number from the
 * global search) and reads them itself (admin-ux phase 3). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="invoices" />}>
      <InvoicesScreen />
    </Suspense>
  );
}
