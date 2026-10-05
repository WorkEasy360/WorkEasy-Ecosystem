"use client";

import { Suspense } from "react";
import { CheckoutScreen } from "@/features/console/CommerceScreens";

/** Checkout (Phase 9). Suspense: the page reads what to buy from the query. */
export default function CheckoutPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading checkout…</p>}>
      <CheckoutScreen />
    </Suspense>
  );
}
