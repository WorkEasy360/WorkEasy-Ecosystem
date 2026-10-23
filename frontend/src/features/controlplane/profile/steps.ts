/**
 * The steps of a partner's Complete Your Profile (ADR-048), and which ones a
 * partner gets.
 *
 * The steps follow the labels the *server* derived from the partner's
 * capabilities - Reseller, White-label, Affiliate - never a stored type, so a
 * partner that holds several gets the union, in one fixed order:
 *
 * - Reseller: Business information, Products, Pricing, Commission,
 *   Customer & sales configuration, Sub-reseller settings, Team, Review;
 * - White-label: Business information, Brand identity, Products, Pricing,
 *   Customer configuration, Domain, Communication, Customer portal (optional),
 *   Team, Review;
 * - Affiliate: Profile, Products, Referral program, Commission, Payout settings,
 *   Marketing information, Review.
 *
 * A step is a place to fill something in; whether the profile is complete is the
 * server's answer (`profile_completion`), shown beside the steps.
 */

export type ProfileStepKey =
  | "business"
  | "brand"
  | "products"
  | "pricing"
  | "commission"
  | "sales"
  | "subresellers"
  | "referral"
  | "domain"
  | "communication"
  | "portal"
  | "payout"
  | "marketing"
  | "team"
  | "review";

export const STEP_ORDER: ProfileStepKey[] = [
  "business",
  "brand",
  "products",
  "pricing",
  "commission",
  "sales",
  "subresellers",
  "referral",
  "domain",
  "communication",
  "portal",
  "payout",
  "marketing",
  "team",
  "review",
];

/** The step names. An affiliate-only partner's first step is called "Profile". */
export function stepLabel(step: string, labels: string[]): string {
  const affiliateOnly = labels.includes("affiliate") && !labels.includes("reseller") && !labels.includes("white_label");
  const sells = labels.includes("reseller") || labels.includes("white_label");
  switch (step as ProfileStepKey) {
    case "business":
      return affiliateOnly ? "Profile" : "Business information";
    case "brand":
      return "Brand identity";
    case "products":
      return sells ? "Products" : "Products promoted";
    case "pricing":
      return "Pricing";
    case "commission":
      return "Commission";
    case "sales":
      return labels.includes("reseller") ? "Customer & sales configuration" : "Customer configuration";
    case "subresellers":
      return "Sub-reseller settings";
    case "referral":
      return "Referral program";
    case "domain":
      return "Custom domain";
    case "communication":
      return "Communication";
    case "portal":
      return "Customer portal (optional)";
    case "payout":
      return "Payout settings";
    case "marketing":
      return "Marketing information";
    case "team":
      return "Team";
    case "review":
      return "Review";
    default:
      return step;
  }
}

/** The steps for a partner wearing `labels` and holding `held` capabilities. */
export function stepsFor(labels: string[], held: string[] = []): ProfileStepKey[] {
  const reseller = labels.includes("reseller");
  const whiteLabel = labels.includes("white_label");
  const affiliate = labels.includes("affiliate");
  const sells = reseller || whiteLabel;
  const want = new Set<ProfileStepKey>(["business", "review"]);
  if (sells || affiliate) want.add("products");
  if (sells) {
    want.add("pricing");
    want.add("sales");
    want.add("team");
  }
  if (reseller || affiliate || held.includes("commission.earn")) want.add("commission");
  // The white-label bundle leaves sub-partners out: the step is the reseller's,
  // or any partner's that already holds the capability.
  if ((reseller && !whiteLabel) || held.includes("subpartner.manage")) want.add("subresellers");
  if (whiteLabel) {
    want.add("brand");
    want.add("domain");
    want.add("communication");
    want.add("portal");
  }
  if (affiliate) {
    want.add("referral");
    want.add("payout");
    want.add("marketing");
  }
  return STEP_ORDER.filter((step) => want.has(step));
}

/** The completion section each step fills in, for the state shown beside it. */
export const STEP_SECTION: Partial<Record<ProfileStepKey, string>> = {
  business: "business",
  brand: "brand",
  products: "products",
  pricing: "pricing",
  commission: "commission",
  referral: "referral",
  domain: "domain",
  team: "administrator",
};

/** The steps that change something on the brand (and so need the white-label setup read). */
export const WHITE_LABEL_STEPS: ProfileStepKey[] = ["brand", "domain", "communication", "portal"];
