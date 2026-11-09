"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "@/features/controlplane/kit";
import { InvoicesScreen } from "@/features/controlplane/AdminBillingViews";

/** Suspense: the page reads an invoice number from the address (the global search). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="invoices" />}>
      <InvoicesFromAddress />
    </Suspense>
  );
}

function InvoicesFromAddress() {
  const q = (useSearchParams().get("q") ?? "").trim().slice(0, 40);
  return <InvoicesScreen key={q} initialQuery={q} />;
}
