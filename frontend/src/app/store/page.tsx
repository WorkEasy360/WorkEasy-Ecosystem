"use client";

import { SiteShell } from "@/features/site/SiteShell";
import { PublicStoreScreen } from "@/features/storefront/PublicStore";

/**
 * `/store` - the Store's address (Store Working Flow PDF, page 2:
 * "app.workeasy360.com/store"): the public Store (2026-11-02), open to anyone
 * in the public site's frame. Buying signs in and opens the organization's
 * own checkout; an organization's Store (`/o/<org>/store`) stays its own.
 */
export default function StorePage() {
  return (
    <SiteShell>
      <PublicStoreScreen />
    </SiteShell>
  );
}
