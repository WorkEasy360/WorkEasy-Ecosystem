"use client";

import { Suspense } from "react";
import { AddPartner } from "@/features/controlplane/AddPartner";
import { AllPartnersList } from "@/features/controlplane/PartnerConsole";

/** Partners → Add Partner: the Add New Partner dialog over All Partners; closing
 * it returns to the list. Suspense: both read the query (`?type=`, filters). */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading…</p>}>
      <AllPartnersList />
      <AddPartner />
    </Suspense>
  );
}
