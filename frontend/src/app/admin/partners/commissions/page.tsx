"use client";

import { Suspense } from "react";
import { Commissions } from "@/features/controlplane/AdminMoney";

/** Partners → Commissions: the commission ledger, inside the Partner Control
 * Center. Suspense: the page reads `?unpaid=1` (the dashboard's card). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading commissions…</p>}>
      <Commissions />
    </Suspense>
  );
}
