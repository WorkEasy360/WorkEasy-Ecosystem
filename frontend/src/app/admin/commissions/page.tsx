"use client";

import { Suspense } from "react";
import { Commissions } from "@/features/controlplane/AdminMoney";

/** Suspense: the page reads `?unpaid=1` (the Partners overview's card). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading commissions…</p>}>
      <Commissions />
    </Suspense>
  );
}
