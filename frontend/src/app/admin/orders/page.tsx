"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LoadingBlock } from "@/features/controlplane/kit";
import { StaffOrdersScreen } from "@/features/controlplane/AdminCommerce";

/** Suspense: the page reads an order number from the address (the command palette). */
export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock what="orders" />}>
      <OrdersFromAddress />
    </Suspense>
  );
}

function OrdersFromAddress() {
  const params = useSearchParams();
  const q = (params.get("q") ?? "").trim().slice(0, 80);
  // ?source=direct|partner|storefront: Store orders or partner activations.
  const source = params.get("source") ?? "";
  return <StaffOrdersScreen key={`${q}|${source}`} initialQuery={q} initialSource={source} />;
}
