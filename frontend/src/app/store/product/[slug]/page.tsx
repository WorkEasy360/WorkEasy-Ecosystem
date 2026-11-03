"use client";

import { useParams } from "next/navigation";
import { SiteShell } from "@/features/site/SiteShell";
import { PublicStoreProductScreen } from "@/features/storefront/PublicStoreProduct";

/** `/store/product/<slug>` - one product of the public Store (2026-11-02):
 * its full details and the ways to buy it. Public, like `/store`. (A fixed
 * segment, so it is never read as a partner's branded `/store/<partner>`.) */
export default function StoreProductPage() {
  const params = useParams<{ slug: string }>();
  return (
    <SiteShell>
      <PublicStoreProductScreen slug={String(params?.slug ?? "")} />
    </SiteShell>
  );
}
