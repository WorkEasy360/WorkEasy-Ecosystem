import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ResellerDraftBody } from "@/lib/api/partnerConsole";

type Fn = (...args: unknown[]) => Promise<unknown>;

const calls: { name: string; args: unknown[] }[] = [];
const record =
  (name: string, result: unknown = {}): Fn =>
  async (...args) => {
    calls.push({ name, args });
    return result;
  };

const mocks = vi.hoisted(() => {
  const none: Fn = async () => ({});
  return {
    createPartner: none,
    grantCapability: none,
    grantProduct: none,
    setPartnerStatus: none,
    saveProfile: none,
    setPartnerPrice: none,
    assignCommission: none,
    inviteAdministrator: none,
  };
});

vi.mock("@/lib/api/controlPlane", () => ({
  adminApi: {
    createPartner: (...a: unknown[]) => mocks.createPartner(...a),
    grantCapability: (...a: unknown[]) => mocks.grantCapability(...a),
    grantProduct: (...a: unknown[]) => mocks.grantProduct(...a),
    setPartnerStatus: (...a: unknown[]) => mocks.setPartnerStatus(...a),
  },
}));
vi.mock("@/lib/api/partnerConsole", () => ({
  partnerConsoleApi: {
    saveProfile: (...a: unknown[]) => mocks.saveProfile(...a),
    setPartnerPrice: (...a: unknown[]) => mocks.setPartnerPrice(...a),
    assignCommission: (...a: unknown[]) => mocks.assignCommission(...a),
    inviteAdministrator: (...a: unknown[]) => mocks.inviteAdministrator(...a),
  },
}));

import { buildCommands, grantedCapabilities, type Created } from "./commands";

/**
 * The commands the wizard runs, one at a time, in the order the design fixes -
 * and that the version one command returns is the one the next quotes.
 */

beforeEach(() => {
  calls.length = 0;
  mocks.createPartner = record("create", { id: "p1", version: 3 });
  mocks.saveProfile = record("profile", { version: 9 });
  mocks.grantCapability = record("capability");
  mocks.grantProduct = record("product");
  mocks.setPartnerPrice = record("price");
  mocks.assignCommission = record("commission");
  mocks.inviteAdministrator = record("administrator");
  mocks.setPartnerStatus = record("activate");
});

function body(overrides: Partial<ResellerDraftBody> = {}): ResellerDraftBody {
  return {
    partner: { code: "acme", display_name: "Acme", legal_name: "", contact_name: "Priya", contact_email: "priya@acme.example", payout_currency: "GBP" },
    profile: { business: { display_name: "Acme" } },
    capabilities: ["customer.provision", "subscription.sell"],
    white_label: false,
    products: [],
    product_options: {},
    pricing_mode: "standard",
    partner_prices: [],
    commission: { mode: "default" },
    ...overrides,
  };
}

async function run(b: ResellerDraftBody, activate = false) {
  const created: { current: Created } = { current: {} };
  const defs = buildCommands({ body: b, catalog: null, productNames: { crm: "CRM" }, planNames: { pv: "CRM — Pro" }, created, reason: "Why", activate });
  for (const def of defs) await def.run();
  return { defs, created };
}

describe("buildCommands", () => {
  it("skips everything that is empty: a bare draft is a create and a profile save", async () => {
    const { defs } = await run(body({ capabilities: [] }));
    expect(defs.map((d) => d.key)).toEqual(["partner", "profile"]);
    expect(calls.map((c) => c.name)).toEqual(["create", "profile"]);
  });

  it("runs create, profile, capabilities, products, prices, commission, administrator and activation in that order", async () => {
    const { defs } = await run(
      body({
        products: ["crm"],
        product_options: { crm: { trial_allowed: false, portal_visible: true } },
        pricing_mode: "custom",
        partner_prices: [{ plan_version_id: "pv", interval: "monthly", amount_minor: 4500 }],
        commission: { mode: "custom", rate_bp: 1500, effective_from: "2099-01-01T09:30:00.000Z" },
        administrator: { email: "boss@acme.example", role: "admin", first_name: "Bo", last_name: "Boss", send_invitation: "now" },
      }),
      true,
    );
    expect(defs.map((d) => d.key)).toEqual([
      "partner",
      "profile",
      "capability:customer.provision",
      "capability:subscription.sell",
      "product:crm",
      "price:pv:monthly",
      "commission:all",
      "administrator",
      "activate",
    ]);
    expect(calls.map((c) => c.name)).toEqual(["create", "profile", "capability", "capability", "product", "price", "commission", "administrator", "activate"]);
  });

  it("quotes the version create returned for the profile save, and the profile's for activation", async () => {
    await run(body({ administrator: { email: "b@x.example", role: "admin", first_name: "", last_name: "", send_invitation: "on_activation" } }), true);
    expect(calls.find((c) => c.name === "profile")!.args).toEqual(["p1", 3, { business: { display_name: "Acme" } }]);
    expect(calls.find((c) => c.name === "activate")!.args).toEqual(["p1", "active", "Why", 9]);
  });

  it("stops short of activation unless asked", async () => {
    await run(body());
    expect(calls.map((c) => c.name)).not.toContain("activate");
  });

  it("grants a product with the two per-grant choices, and no plan", async () => {
    await run(body({ products: ["crm"], product_options: { crm: { trial_allowed: false, portal_visible: true } } }));
    expect(calls.find((c) => c.name === "product")!.args).toEqual(["p1", "crm", undefined, { trial_allowed: false, portal_visible: true }]);
  });

  it("assigns one commission rule for all products, or one per product in scope, never backdated", async () => {
    await run(body({ commission: { mode: "custom", rate_bp: 1250 } }));
    expect(calls.filter((c) => c.name === "commission").map((c) => c.args)).toEqual([["p1", { rate_bp: 1250, reason: "Why", expected_version: 0 }]]);
    calls.length = 0;
    await run(
      body({ products: ["crm", "books"], commission: { mode: "custom", rate_bp: 1250, product_codes: ["crm", "books"], effective_from: "2099-01-01T09:30:00.000Z" } }),
    );
    expect(calls.filter((c) => c.name === "commission").map((c) => c.args[1])).toEqual([
      { rate_bp: 1250, reason: "Why", expected_version: 0, product_code: "crm", effective_from: "2099-01-01T09:30:00.000Z" },
      { rate_bp: 1250, reason: "Why", expected_version: 0, product_code: "books", effective_from: "2099-01-01T09:30:00.000Z" },
    ]);
  });

  it("assigns nothing for the programme default, or for a rate that did not parse", async () => {
    await run(body({ commission: { mode: "default" } }));
    await run(body({ commission: { mode: "custom" } }));
    expect(calls.map((c) => c.name)).not.toContain("commission");
  });

  it("names the administrator with the timing chosen", async () => {
    await run(body({ administrator: { email: "boss@acme.example", role: "viewer", first_name: "Bo", last_name: "Boss", send_invitation: "on_activation" } }));
    expect(calls.find((c) => c.name === "administrator")!.args).toEqual([
      "p1",
      "boss@acme.example",
      "viewer",
      { first_name: "Bo", last_name: "Boss", send_invitation: "on_activation" },
    ]);
  });

  it("gives every command a stable key, so a finished one is never repeated", () => {
    const keys = (b: ResellerDraftBody) =>
      buildCommands({ body: b, catalog: null, productNames: {}, planNames: {}, created: { current: {} }, reason: "Why", activate: true }).map((d) => d.key);
    expect(keys(body())).toEqual(keys(body()));
    expect(new Set(keys(body({ products: ["crm", "books"] }))).size).toBe(keys(body({ products: ["crm", "books"] })).length);
  });
});

describe("grantedCapabilities", () => {
  it("adds the white-label pair once, only when asked", () => {
    expect(grantedCapabilities(body())).toEqual(["customer.provision", "subscription.sell"]);
    expect(grantedCapabilities(body({ white_label: true }))).toEqual(["customer.provision", "subscription.sell", "branding.manage", "domain.manage"]);
    expect(grantedCapabilities(body({ white_label: true, capabilities: ["branding.manage"] }))).toEqual(["branding.manage", "domain.manage"]);
  });
});
