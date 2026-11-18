import { Suspense } from "react";
import { StoreCatalogScreen } from "@/features/store/StoreCatalog";
import { storeMetadata } from "@/lib/storeMeta";

export async function generateMetadata() {
  return storeMetadata({
    path: "/store/products",
    title: "All products - WorkEasy360 Store",
    description: "Every WorkEasy360 product on sale, with its plans and prices. Search and filter by department or industry.",
  });
}

export default function StoreProductsPage() {
  return (
    <Suspense fallback={null}>
      <StoreCatalogScreen />
    </Suspense>
  );
}
