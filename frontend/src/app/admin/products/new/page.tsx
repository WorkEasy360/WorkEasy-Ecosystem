"use client";

import { ProductManager } from "@/features/controlplane/productmanager/ProductManager";

/** Products → Add Product (ADR-068): the product list with the one-page Product
 * Publisher open over it. Closing it returns to the list. */
export default function Page() {
  return <ProductManager openNew />;
}
