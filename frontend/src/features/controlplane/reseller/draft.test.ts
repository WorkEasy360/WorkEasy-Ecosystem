import { describe, it, expect } from "vitest";
import type { CapabilityCatalog } from "@/lib/api/partnerConsole";
import {
  adaptServerErrors,
  chosenCapabilities,
  effectiveFromIso,
  emptyDraft,
  identityValid,
  messagesOf,
  stepOfField,
  toBody,
  typedPrices,
  validateAll,
  validateStep,
  type Draft,
} from "./draft";
import { trialSummary } from "./ProductsStep";

/**
 * The wizard's form model, without the screen: what a draft sends to the review,
 * and which of it is wrong or missing at each step. The model holds strings and
 * selections; the only conversions are text to minor units and to basis points.
 */

const CATALOG: CapabilityCatalog = {
  results: [],
  groups: [],
  presets: [{ key: "standard_reseller", label: "Standard Reseller", description: "", capabilities: ["customer.provision", "subscription.sell"] }],
};

function filled(overrides: Partial<Draft> = {}): Draft {
  const d = emptyDraft();
  d.profile.displayName = "Acme Resell";
  d.code = "acme-resell";
  d.profile.businessEmail = "hello@acme.example";
  d.profile.country = "GB";
  d.profile.contacts.primary = { name: "Priya Shah", jobTitle: "", email: "priya@acme.example", phone: "" };
  return { ...d, ...overrides };
}

describe("toBody", () => {
  it("sends the minimum for an empty draft: no administrator, products, prices or scope", () => {
    const body = toBody(emptyDraft(), CATALOG, "standard_reseller");
    expect(body.administrator).toBeUndefined();
    expect(body.products).toEqual([]);
    expect(body.partner_prices).toEqual([]);
    expect(body.product_options).toEqual({});
    expect(body.commission).toEqual({ mode: "default" });
    expect(body.pricing_mode).toBe("standard");
    expect(body.white_label).toBe(false);
    // The preset is not chosen until the capabilities step has been seen.
    expect(body.capabilities).toEqual([]);
  });

  it("copies the primary contact onto the partner and the profile, and the name onto both", () => {
    const body = toBody(filled(), CATALOG, null);
    expect(body.partner).toEqual({
      code: "acme-resell",
      display_name: "Acme Resell",
      legal_name: "",
      contact_name: "Priya Shah",
      contact_email: "priya@acme.example",
      payout_currency: "GBP",
    });
    expect(body.profile?.business).toMatchObject({ display_name: "Acme Resell", business_email: "hello@acme.example", country: "GB" });
    expect(body.profile?.contacts?.primary).toMatchObject({ name: "Priya Shah", email: "priya@acme.example" });
  });

  it("sends partner prices only for custom pricing, as integer minor units", () => {
    const d = filled({ products: ["crm"], prices: { "pv-1|monthly": "45.00", "pv-2|monthly": "", "pv-3|monthly": "x" } });
    expect(toBody(d, null, null).partner_prices).toEqual([]);
    const custom = { ...d, pricingMode: "custom" as const };
    expect(typedPrices(custom)).toEqual([{ plan_version_id: "pv-1", interval: "monthly", amount_minor: 4500 }]);
    expect(toBody(custom, null, null).pricing_mode).toBe("custom");
    expect(typedPrices({ ...custom, currency: "JPY", prices: { "pv-1|monthly": "1500" } })[0].amount_minor).toBe(1500);
  });

  it("sends a custom commission as basis points, limited to chosen products, with a timestamp", () => {
    const d = filled({
      products: ["crm", "books"],
      commissionMode: "custom",
      ratePercent: "12.5",
      commissionScope: "products",
      commissionProducts: ["books", "ghost"],
      effectiveFrom: "2099-01-01T09:30",
    });
    expect(toBody(d, null, null).commission).toEqual({
      mode: "custom",
      rate_bp: 1250,
      product_codes: ["books"],
      effective_from: new Date("2099-01-01T09:30").toISOString(),
    });
    expect(toBody({ ...d, ratePercent: "oops" }, null, null).commission).not.toHaveProperty("rate_bp");
    expect(toBody({ ...d, commissionMode: "default" }, null, null).commission).toEqual({ mode: "default" });
  });

  it("sends trial choices per product, defaulting to allowed, and the administrator only once started", () => {
    const d = filled({ products: ["crm", "books"], trialAllowed: { crm: false } });
    expect(toBody(d, null, null).product_options).toEqual({
      crm: { trial_allowed: false, portal_visible: true },
      books: { trial_allowed: true, portal_visible: true },
    });
    const withAdmin = { ...d, admin: { ...d.admin, email: " boss@acme.example ", firstName: "Bo" } };
    expect(toBody(withAdmin, null, null).administrator).toEqual({
      email: "boss@acme.example",
      role: "admin",
      first_name: "Bo",
      last_name: "",
      send_invitation: "on_activation",
    });
  });

  it("never carries a computed figure", () => {
    const text = JSON.stringify(toBody(filled({ products: ["crm"], commissionMode: "custom", ratePercent: "15" }), null, null));
    expect(text).not.toMatch(/discount|platform_net|commission_minor|list_price|margin|purchase_minor|hold_days/);
  });
});

describe("chosenCapabilities", () => {
  it("counts the preset only once the step has been seen, and a ticked set always", () => {
    const d = emptyDraft();
    expect(chosenCapabilities(d, CATALOG, "standard_reseller")).toEqual([]);
    expect(chosenCapabilities({ ...d, capabilitiesSeen: true }, CATALOG, "standard_reseller")).toEqual(["customer.provision", "subscription.sell"]);
    expect(chosenCapabilities({ ...d, capabilitiesSeen: true, capabilities: ["x"] }, CATALOG, "standard_reseller")).toEqual(["x"]);
    expect(chosenCapabilities({ ...d, capabilitiesSeen: true }, CATALOG, null)).toEqual([]);
  });
});

describe("validateStep", () => {
  it("asks for the identity and the required business fields on Save & continue", () => {
    const errors = validateStep("business", emptyDraft(), "continue");
    expect(Object.keys(errors).sort()).toEqual(["business.business_email", "business.country", "code", "display_name"]);
    expect(validateStep("business", filled(), "continue")).toEqual({});
  });

  it("asks only for the identity, and for nothing wrong, when saving a draft", () => {
    const errors = validateStep("business", emptyDraft(), "draft");
    expect(Object.keys(errors).sort()).toEqual(["code", "display_name"]);
    expect(validateStep("business", filled({ profile: { ...filled().profile, businessEmail: "", country: "" } }), "draft")).toEqual({});
    const wrong = filled();
    wrong.profile.website = "http://x.example";
    expect(validateStep("business", wrong, "draft")).toHaveProperty(["business.website"]);
  });

  it("rejects a code that is not 3-48 lower-case letters, digits and hyphens starting with a letter", () => {
    for (const code of ["", "ab", "Acme", "1acme", "acme_resell", "a".repeat(49)]) {
      expect(validateStep("business", filled({ code }), "continue")).toHaveProperty("code");
    }
    expect(validateStep("business", filled({ code: "acme-2" }), "continue")).not.toHaveProperty("code");
  });

  it("needs a primary contact to continue, but not to save a draft", () => {
    const d = emptyDraft();
    expect(Object.keys(validateStep("contacts", d, "continue")).sort()).toEqual(["contacts.primary.email", "contacts.primary.name"]);
    expect(validateStep("contacts", d, "draft")).toEqual({});
    d.profile.contacts.primary.email = "nope";
    expect(validateStep("contacts", d, "draft")).toHaveProperty(["contacts.primary.email"]);
  });

  it("needs a capability to continue", () => {
    const d = emptyDraft();
    expect(validateStep("capabilities", { ...d, capabilitiesSeen: true }, "continue", { catalog: CATALOG, preset: "standard_reseller" })).toEqual({});
    expect(validateStep("capabilities", { ...d, capabilitiesSeen: true }, "continue", { catalog: CATALOG, preset: null })).toHaveProperty("capabilities");
    expect(validateStep("capabilities", d, "draft")).toEqual({});
  });

  it("flags an amount that is not money on its row, only for custom pricing", () => {
    const d = filled({ prices: { "pv|monthly": "45,00" } });
    expect(validateStep("pricing", d, "continue")).toEqual({});
    expect(validateStep("pricing", { ...d, pricingMode: "custom" }, "continue")).toHaveProperty(["price.pv|monthly"]);
  });

  it("checks a custom commission: the rate, the scope and an effective time that is not in the past", () => {
    const base = filled({ products: ["crm"], commissionMode: "custom", ratePercent: "15" });
    expect(validateStep("commission", base, "continue")).toEqual({});
    expect(validateStep("commission", { ...base, ratePercent: "101" }, "continue")).toHaveProperty(["commission.rate"]);
    expect(validateStep("commission", { ...base, ratePercent: "" }, "draft")).toHaveProperty(["commission.rate"]);
    expect(validateStep("commission", { ...base, commissionScope: "products", commissionProducts: [] }, "continue")).toHaveProperty(["commission.scope"]);
    expect(validateStep("commission", { ...base, commissionScope: "products", commissionProducts: ["crm"] }, "continue")).toEqual({});
    expect(validateStep("commission", { ...base, effectiveFrom: "2020-01-01T00:00" }, "continue")).toHaveProperty(["commission.effective_from"]);
    expect(validateStep("commission", { ...base, effectiveFrom: "2099-01-01T00:00" }, "continue")).toEqual({});
    // The default programme rate has nothing to check.
    expect(validateStep("commission", { ...base, commissionMode: "default", ratePercent: "oops" }, "continue")).toEqual({});
  });

  it("checks payout and support addresses, and an administrator only once started", () => {
    const d = filled();
    expect(validateStep("administrator", d, "continue")).toEqual({});
    d.admin.firstName = "Bo";
    expect(validateStep("administrator", d, "continue")).toHaveProperty(["administrator.email"]);
    d.admin.email = "boss@acme.example";
    expect(validateStep("administrator", d, "continue")).toEqual({});
    d.profile.supportEmail = "bad";
    expect(validateStep("administrator", d, "continue")).toHaveProperty(["support.email"]);
    d.profile.payoutContactEmail = "bad";
    expect(validateStep("commission", d, "continue")).toHaveProperty(["payout.contact_email"]);
  });
});

describe("validateAll and identityValid", () => {
  it("reports only the steps with something wrong", () => {
    expect(validateAll(filled())).toEqual({});
    expect(Object.keys(validateAll(emptyDraft()))).toEqual(["business"]);
    const d = filled({ commissionMode: "custom", ratePercent: "" });
    d.profile.website = "http://x.example";
    expect(Object.keys(validateAll(d))).toEqual(["business", "commission"]);
  });

  it("is valid with a name, a code and a supported currency", () => {
    expect(identityValid(emptyDraft())).toBe(false);
    expect(identityValid(filled())).toBe(true);
    expect(identityValid(filled({ currency: "XXX" }))).toBe(false);
  });

  it("lists each distinct message once", () => {
    expect(messagesOf({ a: "one", b: "one", c: "two" })).toEqual(["one", "two"]);
  });
});

describe("the server's problems", () => {
  it("are placed on the step that shows the input they name", () => {
    expect(stepOfField("code")).toBe("business");
    expect(stepOfField("display_name")).toBe("business");
    expect(stepOfField("payout_currency")).toBe("business");
    expect(stepOfField("business.website")).toBe("business");
    expect(stepOfField("contact_email")).toBe("contacts");
    expect(stepOfField("contacts.billing.phone")).toBe("contacts");
    expect(stepOfField("capabilities")).toBe("capabilities");
    expect(stepOfField("products")).toBe("products");
    expect(stepOfField("product_options")).toBe("products");
    expect(stepOfField("partner_prices")).toBe("pricing");
    expect(stepOfField("pricing_mode")).toBe("pricing");
    expect(stepOfField("commission")).toBe("commission");
    expect(stepOfField("payout.frequency")).toBe("commission");
    expect(stepOfField("administrator.first_name")).toBe("administrator");
    expect(stepOfField("support.url")).toBe("administrator");
    expect(stepOfField("internal_notes")).toBe("administrator");
    expect(stepOfField("white_label")).toBe("whitelabel");
    expect(stepOfField("something_else")).toBe("review");
  });

  it("keep the first message per field, and reach the form's own field names", () => {
    const errors = adaptServerErrors([
      { field: "display_name", message: "Enter a name." },
      { field: "display_name", message: "Second." },
      { field: "contact_email", message: "Enter a valid email address." },
      { field: "legal_name", message: "Too long." },
    ]);
    expect(errors.display_name).toBe("Enter a name.");
    expect(errors["business.display_name"]).toBe("Enter a name.");
    expect(errors["contacts.primary.email"]).toBe("Enter a valid email address.");
    expect(errors["business.legal_name"]).toBe("Too long.");
  });
});

describe("small helpers", () => {
  it("reads a datetime-local value as a timestamp, or nothing", () => {
    expect(effectiveFromIso("")).toBeNull();
    expect(effectiveFromIso("not a date")).toBeNull();
    expect(effectiveFromIso("2099-01-01T09:30")).toBe(new Date("2099-01-01T09:30").toISOString());
  });

  it("words the platform trial, or says there is none", () => {
    const plans = [{ plan_version_id: "pv", plan_code: "pro", plan_name: "Professional", included_seats: 10, trial_days: 14 }];
    const product = { code: "crm", name: "CRM", status: "available", plans, trial: null };
    expect(trialSummary(product)).toBe("No trial configured");
    const trial = { enabled: true, duration_days: 14, seat_limit: 5, payment_method_required: false, eligible_plan_codes: ["pro"] };
    expect(trialSummary({ ...product, trial })).toBe("14-day trial on Professional, up to 5 seats");
    expect(trialSummary({ ...product, trial: { ...trial, seat_limit: 1 } })).toBe("14-day trial on Professional, up to 1 seat");
    expect(trialSummary({ ...product, trial: { ...trial, seat_limit: null } })).toBe("14-day trial on Professional, with the plan's included seats");
    expect(trialSummary({ ...product, trial: { ...trial, enabled: false } })).toBe("No trial configured");
  });
});
