import type { PartnerDetail } from "@/lib/api/controlPlane";
import type { PartnerSummaryView } from "@/lib/api/partnerConsole";
import type { Load } from "./ui";

/**
 * The sections of one partner's page, shared by the page (which lays the tabs
 * out) and the tabs themselves (which link to one another: a setup step that is
 * not finished points at the tab that finishes it).
 */

export type TabKey =
  | "overview"
  | "business"
  | "contacts"
  | "customers"
  | "products"
  | "subscriptions"
  | "commissions"
  | "payouts"
  | "branding"
  | "domains"
  | "users"
  | "referrals"
  | "activity";

export const TAB_LABELS: Record<TabKey, string> = {
  overview: "Overview",
  business: "Business Details",
  contacts: "Contacts",
  customers: "Customers",
  products: "Products & Pricing",
  subscriptions: "Subscriptions",
  commissions: "Commissions",
  payouts: "Payouts",
  branding: "Branding",
  domains: "Domains",
  users: "Users & Permissions",
  referrals: "Referrals",
  activity: "Activity",
};

export const TAB_ORDER = Object.keys(TAB_LABELS) as TabKey[];

/** Which tab finishes each step of the server's setup progress. */
export const STEP_TAB: Record<string, TabKey> = {
  profile: "business",
  capabilities: "users",
  administrator: "users",
  products: "products",
  pricing: "products",
  commission: "products",
  branding: "branding",
  domain: "domains",
};

/** Which tab supplies each thing the server says is missing before activation. */
export const MISSING_TAB: Record<string, TabKey> = {
  primary_contact: "contacts",
  business_email: "business",
  capabilities: "users",
  products: "products",
  administrator: "users",
};

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
