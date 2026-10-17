/**
 * The partner portal's menu - the one list (ADR-046).
 *
 * The sidebar, the legacy-route redirects (`next.config.ts`) and the
 * architecture test that pins the approved set are all read from this file, so
 * there is no second menu to drift from the first. It is plain data and has no
 * imports on purpose: `next.config.ts` loads it before the app's path aliases
 * exist.
 *
 * Three rules hold for every entry:
 *
 * - The list is the **maximum**. An entry shows only when the server resolved
 *   a permission that makes its page useful (`needs`); a partner never sees an
 *   item it could only be refused. That is navigation convenience, never
 *   security - every request behind a link is authorized again by the backend.
 * - An entry is a **top-level** page. Its sections ("Plans" and "Add-ons",
 *   "Conversions" and "Payouts") are tabs inside the page, not sidebar items.
 * - Nothing here names a partner type. A permission is the only input.
 */

/** Answers "does the signed-in member hold any of these `platform.partner_<key>` permissions?" */
export type Can = (...keys: string[]) => boolean;

export interface PartnerMenuEntry {
  /** Stable identifier (tests and telemetry refer to it, the label may change). */
  key: string;
  label: string;
  icon: string;
  /** The page's path under `/partner/<id>`; `""` is the dashboard. */
  path: string;
  /** The path prefix the entry is current under, when it differs from `path`
   * (an entry whose link opens the first of several tabs). */
  match?: string;
  /** Whether the person's permissions make this page useful. */
  needs: (can: Can) => boolean;
  /** Where the link opens when the first tab is not one this person can read
   * (a payout-only affiliate lands on Payouts, an ended partner on its
   * commission). Defaults to `path`. */
  landing?: (can: Can) => string;
}

export interface PartnerMenuGroup {
  title: string;
  entries: PartnerMenuEntry[];
}

/** Selling: seeing what is for sale, or the customers it is sold to. */
const sells: (can: Can) => boolean = (can) => can("customer.view", "pricing.view");

export const PARTNER_MENU: readonly PartnerMenuGroup[] = [
  {
    title: "Menu",
    entries: [{ key: "dashboards", label: "Dashboards", icon: "dashboard", path: "", needs: (can) => can("dashboard.view") }],
  },
  {
    title: "Audience",
    entries: [
      { key: "users", label: "Users", icon: "users", path: "/users", needs: (can) => can("customer.view") },
      { key: "agency", label: "Agency", icon: "handshake", path: "/agency", needs: (can) => can("subpartner.view") },
      {
        key: "affiliates",
        label: "Affiliates",
        icon: "gift",
        path: "/affiliates",
        needs: (can) => can("referral.view", "payout.view"),
        landing: (can) => (can("referral.view") ? "/affiliates" : "/affiliates/payouts"),
      },
    ],
  },
  {
    title: "Billing",
    entries: [
      { key: "plans", label: "Plans & Add-ons", icon: "tag", path: "/plans", needs: (can) => can("pricing.view") },
      {
        key: "pricing",
        label: "Currency & Pricing",
        icon: "currency",
        path: "/pricing/currency",
        match: "/pricing",
        needs: (can) => can("pricing.view"),
      },
      {
        key: "payments",
        label: "Payments",
        icon: "receipt",
        path: "/payments",
        needs: (can) => can("customer.view", "commission.view"),
        landing: (can) => (can("customer.view") ? "/payments" : "/payments/commissions"),
      },
      { key: "product-billing", label: "Product Billing", icon: "card", path: "/product-billing", needs: sells },
    ],
  },
  {
    title: "Panel customization",
    entries: [
      {
        key: "brand-settings",
        label: "Brand Settings",
        icon: "palette",
        path: "/brand-settings",
        needs: (can) => can("branding.view", "domain.view"),
        landing: (can) => (can("branding.view") ? "/brand-settings" : "/brand-settings/url-manager"),
      },
      { key: "product-manager", label: "Product Manager", icon: "apps", path: "/product-manager", needs: sells },
      { key: "appearance", label: "Appearance", icon: "sparkles", path: "/appearance", needs: (can) => can("branding.view") },
      {
        key: "communication",
        label: "Communication",
        icon: "mail",
        path: "/communication/channel",
        match: "/communication",
        needs: (can) => can("profile.view"),
      },
      { key: "integrations", label: "Integrations", icon: "puzzle", path: "/integrations", needs: (can) => can("profile.view") },
    ],
  },
  {
    title: "New product settings",
    entries: [
      { key: "partner-products", label: "Partner Products", icon: "store", path: "/partner-products", needs: (can) => can("product.view") },
      { key: "auth-configuration", label: "Auth Configuration", icon: "shield", path: "/auth-configuration", needs: (can) => can("profile.view") },
      { key: "shared-resources", label: "Shared Resources", icon: "building", path: "/shared-resources", needs: (can) => can("profile.view") },
    ],
  },
  {
    title: "Settings",
    entries: [
      { key: "account", label: "Account", icon: "settings", path: "/account", needs: (can) => can("profile.view") },
      { key: "channel-manager", label: "Channel Manager", icon: "network", path: "/channel-manager", needs: (can) => can("profile.view") },
      { key: "api-keys", label: "API Key Manager", icon: "key", path: "/api-keys", needs: (can) => can("apikey.view") },
      { key: "translations", label: "Translation Manager", icon: "device", path: "/translations", needs: (can) => can("branding.view") },
    ],
  },
  {
    title: "Team",
    entries: [
      { key: "roles", label: "Role & Permissions", icon: "lock", path: "/roles", needs: (can) => can("member.view") },
      { key: "team", label: "Team Members", icon: "users", path: "/team", needs: (can) => can("member.view") },
    ],
  },
  {
    title: "Help",
    entries: [
      { key: "support", label: "Support Manager", icon: "help", path: "/support-manager", needs: (can) => can("dashboard.view") },
      { key: "notifications", label: "Notification Center", icon: "bell", path: "/notifications", needs: (can) => can("dashboard.view") },
    ],
  },
];

/** Every approved top-level label, in sidebar order. The architecture test
 * compares the rendered sidebar against this and against the approved list in
 * the brief, so adding a menu item is a deliberate, two-place change. */
export const APPROVED_LABELS: readonly string[] = PARTNER_MENU.flatMap((g) => g.entries.map((e) => e.label));

/**
 * Routes the portal used to serve, and where each now lives. A page that moved
 * keeps answering at its old address with a permanent redirect (bookmarks,
 * emailed links, the browser's history); a feature that was merged into
 * another page redirects to the page that now holds it. No old route renders
 * a page of its own, so there is never a second experience beside the first.
 *
 * `from` and `to` are relative to `/partner/<id>`. Query strings pass through.
 */
export const LEGACY_REDIRECTS: readonly { from: string; to: string }[] = [
  { from: "/customers", to: "/users" },
  { from: "/subscriptions", to: "/users/subscriptions" },
  { from: "/sub-partners", to: "/agency" },
  { from: "/referrals", to: "/affiliates" },
  { from: "/commissions", to: "/payments/commissions" },
  { from: "/payouts", to: "/affiliates/payouts" },
  { from: "/balance", to: "/payments/balance" },
  { from: "/orders", to: "/payments" },
  { from: "/payment-links", to: "/payments" },
  { from: "/currencies", to: "/pricing/currency" },
  { from: "/pricing", to: "/pricing/currency" },
  { from: "/pricing/currencies", to: "/pricing/currency" },
  { from: "/branding", to: "/brand-settings" },
  { from: "/domains", to: "/brand-settings/url-manager" },
  { from: "/white-label", to: "/brand-settings/white-label-setup" },
  { from: "/products", to: "/partner-products" },
  { from: "/authentication", to: "/auth-configuration" },
  { from: "/settings", to: "/account" },
  { from: "/activity", to: "/notifications" },
  { from: "/communication", to: "/communication/channel" },
  { from: "/communication/channels", to: "/communication/channel" },
  // The brief's /support: a folder named after a product code ("support") is refused
  // in the platform frontend (ADR-017), so the page lives at /support-manager.
  { from: "/support", to: "/support-manager" },
];
