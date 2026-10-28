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
