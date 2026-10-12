"use client";

import { Suspense } from "react";
import { AffiliateList } from "@/features/controlplane/PartnerConsole";

/** Partners → Affiliates: partners holding a referral capability (a filter, not
 * a kind of account). Suspense: the page reads its filters from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading affiliates…</p>}>
      <AffiliateList />
    </Suspense>
  );
}
