"use client";

import { Suspense } from "react";
import { ProductRequestsConsole } from "@/features/controlplane/ProductRequests";

/** Partners → Product Requests (ADR-055): affiliates asking to promote a Store
 * product. Suspense: the page reads its status, search and open request from the query. */
export default function Page() {
  return (
    <Suspense fallback={<p aria-live="polite">Loading product requests…</p>}>
      <ProductRequestsConsole />
    </Suspense>
  );
}
