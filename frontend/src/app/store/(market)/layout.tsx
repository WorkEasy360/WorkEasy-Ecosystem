import { StoreShell } from "@/features/store/StoreShell";

/** The Digital Store's own pages (ADR-065) share its frame: header, menus,
 * account and footer. A partner's branded Store (`/store/<partner>`) is outside
 * this group and keeps its own. */
export default function StoreLayout({ children }: { children: React.ReactNode }) {
  return <StoreShell>{children}</StoreShell>;
}
