"use client";

import { Suspense } from "react";
import { BillingReturnRedirect } from "@/features/console/BillingScreen";

/** A short address for the billing page (`/o/<slug>/billing`): forwards to
 * Settings -> Subscription & billing, keeping only a well-formed payment
 * attempt id and outcome. Providers return payers to the settings page
 * directly. */
export default function BillingReturnPage() {
  return (
    <Suspense fallback={<p aria-live="polite">Opening billing…</p>}>
      <BillingReturnRedirect />
    </Suspense>
  );
}
