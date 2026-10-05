import { SiteShell } from "@/features/site/SiteShell";

/** The public site's frame (Phase 9): the landing page, products, pricing,
 * services and contact share one header and footer. Every page here is
 * reachable without a session (`src/proxy.ts`'s public paths). */
export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return <SiteShell>{children}</SiteShell>;
}
