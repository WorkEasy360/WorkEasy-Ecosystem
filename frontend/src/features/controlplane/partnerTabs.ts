import type { PartnerDetail } from "@/lib/api/controlPlane";
import type { PartnerSummaryView } from "@/lib/api/partnerConsole";
import type { Load } from "./ui";

/**
 * The sections of one partner's page (ADR-048), shared by the page (which lays
 * the tabs out) and the tabs themselves (which link to one another).
 *
 * Overview, Profile, Products, Users & Permissions and Activity are every
 * partner's; the others appear for the partners whose capabilities they belong
 * to (Pricing and Customers for a partner that sells, Commission for one that
 * earns, Branding and Domains for white-label, Referrals for an affiliate).
 */

export type TabKey =
  | "overview"
  | "profile"
  | "products"
  | "pricing"
  | "commission"
  | "customers"
  | "branding"
  | "domains"
  | "referrals"
  | "payouts"
  | "users"
  | "activity";

export const TAB_LABELS: Record<TabKey, string> = {
  overview: "Overview",
  profile: "Profile",
  products: "Products",
  pricing: "Pricing",
  commission: "Commission",
  customers: "Customers",
  branding: "Branding",
  domains: "Domains",
  referrals: "Referrals",
  payouts: "Payouts",
  users: "Users & Permissions",
  activity: "Activity",
};

export const TAB_ORDER = Object.keys(TAB_LABELS) as TabKey[];

/** Old `?tab=` values, from links made before the page was simplified. */
export const TAB_ALIASES: Record<string, TabKey> = {
  business: "profile",
  contacts: "profile",
  commissions: "commission",
  subscriptions: "customers",
};

export function tabFromParam(raw: string | null): TabKey | null {
  if (!raw) return null;
  if (TAB_ORDER.includes(raw as TabKey)) return raw as TabKey;
  return TAB_ALIASES[raw] ?? null;
}

export interface TabProps {
  partnerId: string;
  partner: PartnerDetail;
  /** The overview read the page keeps, so the header, the Overview and the
   * product options all show one answer and a change refreshes it in place. */
  summary: Load<PartnerSummaryView>;
  /** Bumped after any change, so panels re-read. */
  rev: number;
  bump: () => void;
}
