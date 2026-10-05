"use client";

import { Suspense } from "react";
import { Payouts } from "@/features/controlplane/AdminMoney";

/** Suspense: the page reads `?status=` (the Partners overview's card). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading payouts…</p>}>
      <Payouts />
    </Suspense>
  );
}
