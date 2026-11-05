import { apiFetch } from "./client";

/**
 * The public site's API (Phase 9): the catalog, the site's own settings, and
 * self-service accounts. Anonymous, same-origin; the writes carry the CSRF
 * header `apiFetch` adds, from the cookie `/auth/session` issued on mount.
 *
 * Tokens from emailed links travel in the request **body**, never in a URL.
 */

export interface SiteSettings {
  signup_enabled: boolean;
  support_email: string;
  sales_email: string;
  catalog_currency: string;
}

export interface PublicPrice {
  interval: string;
  currency: string;
  model: string;
  amount_minor: number | null;
  from_tiers: boolean;
}

export interface PublicPlan {
  plan_version_id: string;
  plan_code: string;
  name: string;
  caption: string;
  description: string;
  billing_type: string;
  kind: string;
  addon_for: string | null;
  included_seats: number;
  max_seats: number | null;
  trial_days: number;
  prices: PublicPrice[];
  /** Phase 11 - this plan is one of the product's *configured* eligible
   * trial plans (independent of `trial_days`, the older per-plan trial). */
  trial_available: boolean;
}

export type PublicAvailability = "available" | "coming_soon";

/** What staff configured for this product's trial (Phase 11) - never a
 * default; a product with nothing configured shows `available: false`. */
export interface PublicTrialOffer {
  available: boolean;
  duration_days: number | null;
  seat_limit: number | null;
  payment_method_required: boolean;
  plan_codes: string[];
}

export interface PublicProduct {
  code: string;
  slug: string;
  name: string;
  tagline: string;
  description: string;
  marketing_url: string;
  availability: PublicAvailability;
  /** Phase 11 - presentation only. Empty strings are common: not every
   * product has been given a category or an icon key yet. */
  category: string;
  icon: string;
  features: { name: string; description: string }[];
  plans: PublicPlan[];
  starting_price: { amount_minor: number; currency: string; interval: string } | null;
  trial: PublicTrialOffer;
}

export interface PublicBundle {
  bundle_version_id: string;
  bundle_code: string;
  name: string;
  caption: string;
  description: string;
  billing_type: string;
  trial_days: number;
  includes: { product_code: string; product_name: string; plan_name: string; quantity: number }[];
  prices: PublicPrice[];
}

export interface PublicCatalog {
  currency: string | null;
  currencies: string[];
  products: PublicProduct[];
  bundles: PublicBundle[];
}

export interface PublicProductPage {
  currency: string | null;
  currencies: string[];
  product: PublicProduct;
  bundles: PublicBundle[];
}

/** One product of the public Store (`/store`, 2026-11-02): the public
 * catalog's product plus what a Store card shows - every value the server's. */
export interface PublicStoreCard extends PublicProduct {
  title: string;
  summary: string;
  status: "available" | "coming_soon";
  ribbon: { kind: "coming_soon" | "new" | "trial"; label: string } | null;
  price: { amount_minor: number; currency: string; interval: string; plan_name: string; list_amount_minor?: number | null; discount_bp?: number } | null;
  yearly_deal: { amount_minor: number; compare_at_minor: number; currency: string; save_percent: number } | null;
  badges?: string[];
  tags?: string[];
  subcategory?: string;
  /** 2026-11-04 marketplace: SaaS, AI tool or business suite; who it suits; what it connects to. */
  product_type?: "saas" | "ai_tool" | "business_suite";
  best_for?: string[];
  integrations?: string[];
  plan_types: string[];
  trial_days: number | null;
  logo_url: string | null;
  banner_url: string | null;
}

interface PublicStoreFacet {
  value: string;
  label: string;
  count: number;
}

/** The public Store: the public catalog as a marketplace page. */
export interface PublicStorePage {
  currency: string | null;
  currencies: string[];
  count: number;
  total: number;
  products: PublicStoreCard[];
  facets: {
    categories: PublicStoreFacet[];
    statuses: PublicStoreFacet[];
    plan_types: PublicStoreFacet[];
    collections?: PublicStoreFacet[];
    product_types?: PublicStoreFacet[];
    best_for?: PublicStoreFacet[];
    integrations?: PublicStoreFacet[];
    price: { min_minor: number | null; max_minor: number | null };
  };
  sorts: { value: string; label: string }[];
}

/** A listing image of a Store product. */
export interface PublicStoreImage {
  url: string;
  caption: string;
  width: number;
  height: number;
}

/** A product page's video (2026-11-04). `embed_url` is the server's fixed
 * player address (YouTube's no-cookie host or Vimeo's player), never the link
 * an admin typed; the CSP's `frame-src` names exactly those two origins. */
export type PublicStoreVideo =
  | {
      provider: "youtube" | "vimeo";
      id: string;
      embed_url: string;
      watch_url: string;
    }
  /** A video uploaded from the admin's computer: same-origin, played by the
   * browser's own player (`url` answers Range requests). */
  | { provider: "upload"; id: string; url: string; content_type: string };

/** A plan as the public Store's comparison shows it. */
export type PublicStorePlan = Omit<PublicPlan, "billing_type" | "kind" | "prices"> & {
  billing_type: "paid" | "free_trial";
  kind: "standard" | "add_on";
  recommended?: boolean;
  /** ADR-056: a promotional discount's list price and a one-time set-up fee
   * ride on each price row. */
  prices: (PublicPlan["prices"][number] & { list_amount_minor?: number | null; discount_bp?: number; setup_fee_minor?: number | null })[];
};

/** One product of the public Store (`/store/product/<slug>`): its full
 * listing, the plan comparison and related products - every value the server's. */
export interface PublicStoreProductPage {
  currency: string | null;
  currencies: string[];
  product: {
    code: string;
    slug: string;
    name: string;
    tagline: string;
    availability: PublicAvailability;
    trial: PublicTrialOffer;
    category: string;
    subcategory: string;
    icon: string;
    title: string;
    summary: string;
    description: string;
    highlights: string[];
    benefits: string[];
    use_cases: string[];
    tags: string[];
    badges: string[];
    documentation_url: string;
    support_url: string;
    status: "available" | "coming_soon";
    logo: PublicStoreImage | null;
    banner: PublicStoreImage | null;
    screenshots: PublicStoreImage[];
    /** Absent from an older server's answer. */
    video?: PublicStoreVideo | null;
    price: PublicStoreCard["price"];
    yearly_deal: PublicStoreCard["yearly_deal"];
    /** ADR-058: the refund promise the Super Admin set, in days; absent/null = none. */
    refund_days?: number | null;
  };
  /** ADR-058: GST added on top of the prices (INR), or null. Absent from an older server. */
  tax?: PublicTax | null;
  plans: PublicStorePlan[];
  comparison: { key: string; label: string; description?: string; kind: string; unit: string; values: Record<string, unknown> }[];
  related: { code: string; slug: string; name: string; title: string; summary: string; icon: string; category: string; price: PublicStoreCard["price"] }[];
}

/** ADR-058: the tax a currency's prices are before (INR: GST 18%). */
export interface PublicTax {
  label: string;
  rate_bp: number;
  description: string;
}

/** ADR-058: what a coupon would take off - a preview; the checkout prices it again. */
export type CouponCheck =
  | { valid: true; code: string; label: string; discount_minor: number }
  | { valid: false; reason?: string; message: string };

export interface SignupRequest {
  first_name: string;
  last_name: string;
  email: string;
  password: string;
  company_name?: string;
}

const post = <T>(path: string, body: unknown) =>
  apiFetch<T>(path, { method: "POST", body: JSON.stringify(body) });

export const publicApi = {
  site: () => apiFetch<SiteSettings>("/api/v1/public/site"),
  catalog: (currency?: string) =>
    apiFetch<PublicCatalog>("/api/v1/public/catalog", { query: { currency } }),
  /** The public Store: filtered, counted and sorted on the server (`status`
   * and `plan_type` comma-separated; price bounds in major units). */
  store: (query: Record<string, string | undefined>) => apiFetch<PublicStorePage>("/api/v1/public/store", { query }),
  storeProduct: (slug: string, currency?: string) =>
    apiFetch<PublicStoreProductPage>(`/api/v1/public/store/products/${encodeURIComponent(slug)}`, { query: { currency } }),
  /** ADR-058: the product page's coupon "Apply". */
  storeCoupon: (slug: string, query: { code: string; currency: string; amount_minor: number }) =>
    apiFetch<CouponCheck>(`/api/v1/public/store/products/${encodeURIComponent(slug)}/coupon`, {
      query: { code: query.code, currency: query.currency, amount_minor: String(query.amount_minor) },
    }),
  product: (slug: string, currency?: string) =>
    apiFetch<PublicProductPage>(`/api/v1/public/catalog/products/${encodeURIComponent(slug)}`, {
      query: { currency },
    }),
  /** Always 202 "check your email" - whether or not the address is registered. */
  signup: (body: SignupRequest) => post<{ status: string }>("/api/v1/public/signup", body),
  /** The emailed link's token and the password chosen at signup, together;
   * signs the person in. */
  verifyEmail: (token: string, password: string) =>
    post<{ organization_slug: string | null; organization_id: string | null }>("/api/v1/public/signup/verify", {
      token,
      password,
    }),
  resendVerification: (email: string) => post<{ status: string }>("/api/v1/public/signup/resend", { email }),
  forgotPassword: (email: string) => post<{ status: string }>("/api/v1/public/password/forgot", { email }),
  resetPassword: (token: string, password: string) =>
    post<{ status: string }>("/api/v1/public/password/reset", { token, password }),
  /** A partner's referral link: an opaque token for the signup to convert, and
   * (ADR-053) where the link leads - a platform path the server derived. */
  captureReferral: (code: string) =>
    post<{ token: string; destination?: string }>("/api/v1/public/referrals", { code }),
  convertReferral: (token: string, organizationId: string) =>
    post<{ converted: boolean }>("/api/v1/referrals/convert", { token, organization_id: organizationId }),
  /** ADR-054: a published feature or release of a Store product - the page an
   * affiliate's update link opens. */
  productUpdate: (slug: string, updateSlug: string) =>
    apiFetch<PublicProductUpdate>(
      `/api/v1/public/products/${encodeURIComponent(slug)}/updates/${encodeURIComponent(updateSlug)}`,
    ),
  /** ADR-053: a white-label partner's branded Store - presentation only. */
  storefront: (slug: string) => apiFetch<Storefront>(`/api/v1/public/storefront/${encodeURIComponent(slug)}`),
};

/** ADR-054: an approved product announcement. Plain text throughout. */
export interface PublicProductUpdate {
  id: string;
  kind: "feature" | "release";
  slug: string;
  title: string;
  summary: string;
  body: string;
  banner_url: string | null;
  /** An https page on an approved host; shown as a link, never redirected to. */
  landing_url: string;
  page_path: string;
  published_at: string | null;
  product: { code: string; name: string; slug: string; tagline: string };
}

/** A partner's branded Store: its brand (presentation) over the central
 * catalog - only the products it may sell, at its prices. */
export interface Storefront {
  slug: string;
  brand: {
    brand_name: string;
    logo_url: string | null;
    accent_color: string;
    theme: Record<string, string>;
    support_email: string;
    support_url: string;
    portal: {
      short_name: string;
      support_name: string;
      support_phone: string;
      website_url: string;
      terms_url: string;
      privacy_url: string;
      welcome_message: string;
      footer_text: string;
    };
  };
  products: {
    code: string;
    name: string;
    slug: string;
    tagline: string;
    category: string;
    icon: string;
    summary: string;
    logo_url: string | null;
    plans: {
      plan_version_id: string;
      plan_code: string;
      plan_name: string;
      trial_days: number;
      prices: { currency: string; interval: string; amount_minor: number }[];
    }[];
  }[];
}
