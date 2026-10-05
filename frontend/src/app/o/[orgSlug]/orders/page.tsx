"use client";

import { Suspense } from "react";
import { OrdersScreen } from "@/features/console/CommerceScreens";

/** Orders (Phase 9). Suspense: the page reads `?placed=` after a checkout. */
export default function OrdersPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading orders…</p>}>
      <OrdersScreen />
    </Suspense>
  );
}
