"use client";

import { Suspense } from "react";
import { BillingScreen } from "@/features/console/BillingScreen";

/** Organization → Subscription & billing. Suspense: the page reads the
 * `?payment=...&outcome=...` a payment provider returns with. */
export default function BillingPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading billing…</p>}>
      <BillingScreen />
    </Suspense>
  );
}
