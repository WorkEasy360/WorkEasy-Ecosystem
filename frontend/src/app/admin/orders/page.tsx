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
  const q = (useSearchParams().get("q") ?? "").trim().slice(0, 80);
  return <StaffOrdersScreen key={q} initialQuery={q} />;
}
