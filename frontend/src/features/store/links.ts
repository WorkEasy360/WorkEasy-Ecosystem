/**
 * The Digital Store's addresses (ADR-065). On its own host
 * (https://store.workeasy360.com) the marketplace is served at `/` and every other
 * page keeps its `/store/...` path - the same paths the Store always had on the
 * customer portal, so every existing deep link (`/store/product/<slug>`) still
 * opens, on whichever host serves the Store.
 */

export const STORE_HOME = "/store";

export const storePaths = {
  home: STORE_HOME,
  products: "/store/products",
  product: (slug: string) => `/store/product/${encodeURIComponent(slug)}`,
  checkout: "/store/checkout",
  complete: "/store/checkout/complete",
  recharge: "/store/recharge",
  partners: "/store/partners",
  help: "/store/help",
  policy: (key: PolicyKey) => `/store/policies/${key}`,
} as const;

/** The footer's policies: the Super Admin writes and publishes each one (Help
 * content, kind "policy"); the Store never shows a text of its own. The same keys
 * as the server's `help_content.POLICY_KEYS`. */
export const POLICIES = [
  { key: "terms", label: "Terms & Conditions" },
  { key: "privacy", label: "Privacy Policy" },
  { key: "refund-cancellation", label: "Refund & Cancellation Policy" },
  { key: "subscription-terms", label: "Subscription Terms" },
  { key: "platform-wallet-terms", label: "Platform Wallet Terms" },
  { key: "ai-credits-terms", label: "AI Credits Terms" },
  { key: "consumption-wallet-terms", label: "Consumption Wallet Terms" },
  { key: "partner-terms", label: "Partner Terms" },
] as const;

export type PolicyKey = (typeof POLICIES)[number]["key"];

export function policyLabel(key: string): string | null {
  return POLICIES.find((p) => p.key === key)?.label ?? null;
}

/** "My Account": the User Admin pages each link opens, for one organization -
 * the routes the customer portal actually serves (launcher/orgNav.ts). Without an
 * organization, User Admin's own front door decides. */
export function myAccountLinks(slug: string | null): { key: string; label: string; path: string; icon: string }[] {
  const o = (rest: string) => (slug ? `/o/${encodeURIComponent(slug)}/${rest}` : "/dashboard");
  return [
    { key: "products", label: "My Products", path: o("apps"), icon: "apps" },
    { key: "billing", label: "Billing & Subscriptions", path: o("settings/billing"), icon: "receipt" },
    { key: "wallets", label: "Wallets & Credits", path: o("wallets"), icon: "wallet" },
    { key: "team", label: "Organization & Team", path: o("settings/organization"), icon: "users" },
    { key: "support", label: "Support", path: o("help"), icon: "headset" },
    { key: "security", label: "Account & Security", path: o("settings/security"), icon: "shield" },
  ];
}

/** The partner portal's pages the Store links to (partner.workeasy360.com). */
export const partnerPaths = {
  apply: (programme?: "affiliate" | "reseller") => (programme ? `/partner/apply?type=${programme}` : "/partner/apply"),
  signIn: "/login?mode=partner",
} as const;
