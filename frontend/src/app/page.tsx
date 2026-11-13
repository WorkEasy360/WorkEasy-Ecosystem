import { headers } from "next/headers";
import { LandingPage } from "@/features/site/SitePages";
import { SiteShell } from "@/features/site/SiteShell";
import { StoreHomeScreen } from "@/features/store/StoreHome";
import { StoreShell } from "@/features/store/StoreShell";
import { portalForHost, readPortalOrigins } from "@/lib/portals";
import { storeMetadata } from "@/lib/storeMeta";

/** Whether this request is on the Digital Store's own host (ADR-065). */
async function onStoreHost(): Promise<boolean> {
  const origins = readPortalOrigins(process.env);
  if (!origins?.store) return false;
  return portalForHost((await headers()).get("host") ?? "", origins) === "store";
}

export async function generateMetadata() {
  if (!(await onStoreHost())) return {};
  return storeMetadata({
    path: "/store",
    title: "WorkEasy360 Store - Business apps, plans and credits",
    description: "Discover WorkEasy360 business apps, compare plans and prices, buy subscriptions, recharge wallets and join the partner programme.",
  });
}

/**
 * `/` - the public front page, for visitors and signed-in people alike (a
 * signed-in person sees "Go to dashboard" in the header instead of sign-up).
 *
 * ADR-065: on the Digital Store's own host the root *is* the marketplace
 * (https://store.workeasy360.com/), served here rather than rewritten, so the
 * address stays the host's root.
 */
export default async function HomePage() {
  if (await onStoreHost()) {
    return (
      <StoreShell>
        <StoreHomeScreen />
      </StoreShell>
    );
  }
  return (
    <SiteShell>
      <LandingPage />
    </SiteShell>
  );
}
