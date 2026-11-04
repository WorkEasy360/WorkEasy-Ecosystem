"use client";

import { Suspense } from "react";
import { SiteShell } from "@/features/site/SiteShell";
import { PublicStoreScreen } from "@/features/storefront/PublicStore";
import { StoreHeader } from "@/features/storefront/StoreHeader";

/**
 * `/store` - the Store's address (Store Working Flow PDF, page 2:
 * "app.workeasy360.com/store"): the public Store (2026-11-02), open to anyone
 * in the marketplace's own frame (2026-11-04). Buying signs in and opens the
 * organization's own checkout; an organization's Store (`/o/<org>/store`)
 * stays its own. `?q=`, `?category=`, `?type=`, `?best_for=` and
 * `?collection=` open it filtered - the header's menus link there.
 */
export default function StorePage() {
  return (
    <SiteShell header={<StoreHeader />}>
      <Suspense fallback={null}>
        <PublicStoreScreen />
      </Suspense>
    </SiteShell>
  );
}
