"use client";

import { Suspense } from "react";
import { Payouts } from "@/features/controlplane/AdminMoney";

/** Partners → Payouts, inside the Partner Control Center. Suspense: the page
 * reads `?status=` (the dashboard's card and "Review payouts"). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading payouts…</p>}>
      <Payouts />
    </Suspense>
  );
}
