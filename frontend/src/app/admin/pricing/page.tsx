"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PricingScreen } from "@/features/controlplane/AdminPricing";

/** `?product=<code>`: the Product Manager's "Manage Pricing" opens this filtered. */
function Pricing() {
  const product = useSearchParams().get("product") || undefined;
  return <PricingScreen key={product ?? ""} productCode={product} />;
}

export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading price books…</p>}>
      <Pricing />
    </Suspense>
  );
}
