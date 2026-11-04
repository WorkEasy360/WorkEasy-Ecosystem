"use client";

import { useParams } from "next/navigation";
import { StoreProductScreen } from "@/features/console/StoreProductScreen";

/** The Store → one product: its listing and plan comparison (ADR-056). */
export default function StoreProductPage() {
  const { productCode } = useParams<{ productCode: string }>();
  return <StoreProductScreen productCode={productCode} />;
}
