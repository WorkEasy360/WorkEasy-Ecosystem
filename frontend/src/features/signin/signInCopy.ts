import type { EffectiveSignIn, ProductSignInMode, ProductSignInRow, SignInPreferenceValue, SsoState } from "@/lib/api/signIn";

/**
 * The words for universal sign-in (ADR-044). The server stores and decides
 * with enum values only; every sentence a person reads is here, so a value
 * the server adds later shows as itself rather than as a wrong sentence.
 */

/** The default for every product - the brief's three global choices. */
export const DEFAULT_LABELS: Record<EffectiveSignIn, { label: string; hint: string }> = {
  auto_sso: {
    label: "Automatically sign me in to eligible Workeasy360 products",
    hint: "Products open straight away with your Workeasy360 account. No second password.",
  },
  ask: {
    label: "Ask me each time",
    hint: "Choose between your Workeasy360 account and the product's own sign-in whenever you open a product.",
  },
  product_login: {
    label: "Prefer each product's own sign-in",
    hint: "Go to the product's own sign-in page where it has one. Products that only use Workeasy360 will ask you.",
  },
};

/** A per-product override, as an option in that product's list. */
export function overrideLabel(value: SignInPreferenceValue, productName: string, defaultValue: EffectiveSignIn): string {
  switch (value) {
    case "inherit":
      return `Use my default (${shortLabel(defaultValue)})`;
    case "auto_sso":
      return "Automatically use Workeasy360";
    case "ask":
      return "Ask each time";
    case "product_login":
      return `Prefer ${productName} sign-in`;
    default:
      return String(value).replace(/_/g, " ");
  }
}

export function shortLabel(value: EffectiveSignIn): string {
  switch (value) {
    case "auto_sso":
      return "automatic";
    case "ask":
      return "ask each time";
    case "product_login":
      return "product sign-in";
    default:
      return String(value).replace(/_/g, " ");
  }
}

export const MODE_LABELS: Record<ProductSignInMode, string> = {
  sso_only: "Workeasy360 sign-in only",
  sso_optional: "Workeasy360 or its own sign-in",
  local_only: "Its own sign-in only",
};

export const SSO_STATE_LABELS: Record<SsoState, string> = {
  enabled: "Enabled",
  disabled: "Disabled",
  integration_required: "Integration required",
};

/** The one line a product card or a settings row says about what happens. */
export function whatHappens(row: Pick<ProductSignInRow, "sso" | "effective" | "constraint">, productName: string): string {
  if (row.sso !== "enabled") return "Separate product sign-in required";
  if (row.effective === "auto_sso") return "Opens with your Workeasy360 account";
  if (row.effective === "product_login") return `Opens ${productName}'s own sign-in page`;
  if (row.constraint === "product_requires_sso") {
    return `${productName} only uses Workeasy360 sign-in, so Workeasy360 will ask you each time`;
  }
  return "Workeasy360 asks how you want to sign in";
}

/** An account-trail entry for a preference change: the server records
 * `<product code or "default">=<value>`, or `reset`. */
export function describePreferenceChange(reason: string): string {
  if (reason === "reset") return "Product sign-in preferences reset to default";
  const [scope, value] = reason.split("=", 2);
  const what =
    value === "auto_sso" || value === "ask" || value === "product_login" ? shortLabel(value) : (value ?? "").replace(/_/g, " ");
  if (!scope || !value) return "Product sign-in preference changed";
  return scope === "default"
    ? `Default product sign-in changed to ${what}`
    : `Product sign-in for ${scope.replace(/_/g, " ")} changed to ${value === "inherit" ? "my default" : what}`;
}
