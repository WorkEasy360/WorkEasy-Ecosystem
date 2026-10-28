"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { ProductDetail } from "@/features/controlplane/productmanager/ProductDetail";

/** Product Manager → one product. Suspense: the page reads its tab from the query. */
export default function Page() {
  const { productId } = useParams<{ productId: string }>();
  return (
    <Suspense fallback={<p aria-live="polite">Loading this product…</p>}>
      <ProductDetail productId={productId} />
    </Suspense>
  );
}
