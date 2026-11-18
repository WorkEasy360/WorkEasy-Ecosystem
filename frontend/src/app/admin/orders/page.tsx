"use client";

import { Suspense } from "react";
import { LoadingBlock } from "@/features/controlplane/kit";
import { StaffOrdersScreen } from "@/features/controlplane/AdminCommerce";

/** Suspense: the list keeps its filters in the address (an order number from the
 * command palette, `?source=`) and reads them itself (admin-ux phase 3) - no
 * remount on a filter change, so the search box keeps its focus. */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="orders" />}>
      <StaffOrdersScreen />
    </Suspense>
  );
}
