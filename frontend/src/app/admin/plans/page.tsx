"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PlansScreen } from "@/features/controlplane/AdminPlans";

/** `?product=<code>`: the Product Manager's "Manage Plans" opens this filtered. */
function Plans() {
  const product = useSearchParams().get("product") || undefined;
  return <PlansScreen key={product ?? ""} productCode={product} />;
}

export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading the catalog…</p>}>
      <Plans />
    </Suspense>
  );
}
