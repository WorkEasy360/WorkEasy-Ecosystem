import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { AddPartner } from "./AddPartner";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";

/**
 * Partner Control Center → Add partner. The business purpose is a capability
 * preset selector and nothing else: it is never sent to the server, and the
 * partner created is an ordinary partner holding whichever capabilities were
 * ticked.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/partners/new",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage"];
const AFFILIATE = ["referral.create", "referral.view", "commission.earn", "commission.view", "payout.view"];

const CATALOG = {
  results: [
    { key: "customer.provision", label: "Create customer organizations", group: "customer_management" },
    { key: "branding.manage", label: "Configure white-label branding", group: "white_label" },
    { key: "domain.manage", label: "Register and activate custom domains", group: "white_label" },
    { key: "commission.earn", label: "Earn commission on attributed sales", group: "commissions" },
    { key: "commission.view", label: "View earned commission", group: "commissions" },
    { key: "payout.view", label: "View payouts", group: "payouts" },
    { key: "referral.create", label: "Create referral codes", group: "referrals" },
    { key: "referral.view", label: "View referrals and their conversion", group: "referrals" },
  ],
  groups: [
    { key: "customer_management", label: "Customer management", capabilities: ["customer.provision"] },
    { key: "commissions", label: "Commissions", capabilities: ["commission.earn", "commission.view"] },
    { key: "payouts", label: "Payouts", capabilities: ["payout.view"] },
    { key: "white_label", label: "White-label", capabilities: ["branding.manage", "domain.manage"] },
    { key: "referrals", label: "Referrals", capabilities: ["referral.create", "referral.view"] },
  ],
  presets: [
    { key: "standard_reseller", label: "Standard Reseller", description: "Sell.", capabilities: ["customer.provision"] },
    { key: "white_label", label: "White-label", description: "Brand and domains.", capabilities: ["branding.manage", "domain.manage"] },
    { key: "affiliate", label: "Affiliate", description: "Refer customers.", capabilities: AFFILIATE },
  ],
};

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/admin/partners/capabilities": [ok(CATALOG)],
    "GET /api/v1/admin/partners/products": [ok({ results: [{ code: "crm", name: "CRM", status: "available", plans: [] }] })],
    "POST /api/v1/admin/partners/reseller-review": [
      ok({
        ok: true,
        problems: [],
        partner: { code: "refer-co", display_name: "Refer Co", legal_name: "", contact_name: "", contact_email: "" },
        capabilities: AFFILIATE.map((key) => ({ key, label: key })),
        experience: [{ key: "affiliate", label: "Affiliate" }],
        profile: { business: {}, contacts: {}, support: {}, payout: {}, has_internal_notes: false },
        products: [],
        pricing_mode: "standard",
        pricing: [],
        trials: [],
        white_label: { requested: false, capabilities_added: [] },
        readiness: { can_activate: true, missing: [] },
        missing_optional: [],
        commission: {
          mode: "default",
          rate_bp: null,
          programme_default_bp: 1000,
          source: "programme_default",
          product_rates: {},
          method: "percentage",
          scope: "all",
          product_codes: [],
          effective_from: null,
          hold_days: 30,
          example: null,
        },
        administrator: null,
        warnings: [],
      }),
    ],
    ...extra,
  };
}

function mount() {
  return render(
    <AdminContextForTest permissions={MANAGE}>
      <AddPartner />
    </AdminContextForTest>,
  );
}

const next = () => fireEvent.click(screen.getByRole("button", { name: "Save & continue" }));

/** Steps 1 and 2 (business details and the primary contact), ending on the capabilities step. */
async function details(name: string) {
  fireEvent.change(await screen.findByLabelText(/Company or partner name/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/^Business email/), { target: { value: "hello@example.com" } });
  fireEvent.change(screen.getByLabelText(/^Country/), { target: { value: "GB" } });
  next();
  fireEvent.change(await screen.findByLabelText(/Primary contact name/), { target: { value: "Pat Lee" } });
  fireEvent.change(screen.getByLabelText(/Primary contact email/), { target: { value: "pat@example.com" } });
  next();
}

describe("choosing a business purpose", () => {
  it("offers four purposes, each a link that only pre-selects capabilities", async () => {
    const { container } = mount();
    expect(screen.getByRole("heading", { level: 1, name: "Add partner" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Business purpose" });
    expect(
      within(list)
        .getAllByRole("link")
        .map((a) => [a.firstElementChild?.textContent, a.getAttribute("href")]),
    ).toEqual([
      ["Reseller", "/admin/partners/new?purpose=reseller"],
      ["White-label", "/admin/partners/new?purpose=white_label"],
      ["Affiliate", "/admin/partners/new?purpose=affiliate"],
      ["Custom", "/admin/partners/new?purpose=custom"],
    ]);
    expect(container.textContent).toMatch(/only pre-selects capabilities/);
    await expectNoViolations(container);
  });

  it("treats an unknown purpose as no purpose", () => {
    query = "purpose=partner_type";
    mount();
    expect(screen.getByRole("list", { name: "Business purpose" })).toBeInTheDocument();
  });
});

describe("the flow for a purpose", () => {
  it("Affiliate starts from the affiliate preset and never sends the purpose", async () => {
    query = "purpose=affiliate";
    const calls = mockApi(routes());
    mount();
    expect(screen.getByRole("heading", { level: 1, name: "Add affiliate" })).toBeInTheDocument();
    await details("Refer Co");

    await screen.findByRole("heading", { name: /Capabilities/ });
    for (const key of AFFILIATE) {
      const label = CATALOG.results.find((c) => c.key === key)!.label;
      expect(screen.getByRole("checkbox", { name: new RegExp(label) })).toBeChecked();
    }
    expect(screen.getByRole("checkbox", { name: /Create customer organizations/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Affiliate" })).toBeChecked();
    next();
    await screen.findByRole("heading", { name: /Step 4 of 9: Products/ });
    next();
    await screen.findByRole("heading", { name: /Step 5 of 9: Pricing/ });

    const review = calls.find((c) => c.key === "POST /api/v1/admin/partners/reseller-review")!;
    expect((review.body as { capabilities: string[] }).capabilities).toEqual(AFFILIATE);
    for (const call of calls) {
      expect(JSON.stringify(call.body ?? {})).not.toMatch(/purpose|affiliate_partner|partner_type/);
      expect(call.url).not.toMatch(/purpose/);
    }
  });

  it("White-label starts from branding and domains", async () => {
    query = "purpose=white_label";
    mockApi(routes());
    mount();
    expect(screen.getByRole("heading", { level: 1, name: "Add white-label partner" })).toBeInTheDocument();
    await details("Brand Co");
    await screen.findByRole("heading", { name: /Capabilities/ });
    expect(screen.getByRole("checkbox", { name: /Configure white-label branding/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Register and activate custom domains/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Create referral codes/ })).not.toBeChecked();
  });

  it("White-label also starts with white-label switched on, on the white-label step", async () => {
    query = "purpose=white_label";
    mockApi(routes());
    mount();
    await details("Brand Co");
    await screen.findByRole("heading", { name: /Step 3 of 9: Capabilities/ });
    for (let n = 4; n <= 8; n++) {
      next();
      await screen.findByRole("heading", { name: new RegExp(`^Step ${n} of 9`) });
    }
    expect(screen.getByRole("radio", { name: "Yes, configure now" })).toBeChecked();
  });

  it("Affiliate starts with white-label off", async () => {
    query = "purpose=affiliate";
    mockApi(routes());
    mount();
    await details("Refer Co");
    await screen.findByRole("heading", { name: /Step 3 of 9: Capabilities/ });
    for (let n = 4; n <= 8; n++) {
      next();
      await screen.findByRole("heading", { name: new RegExp(`^Step ${n} of 9`) });
    }
    expect(screen.getByRole("radio", { name: "Not now" })).toBeChecked();
  });

  it("Custom starts with nothing ticked and asks for at least one capability", async () => {
    query = "purpose=custom";
    mockApi(routes());
    mount();
    expect(screen.getByRole("heading", { level: 1, name: "Add partner" })).toBeInTheDocument();
    await details("Custom Co");
    await screen.findByRole("heading", { name: /Capabilities/ });
    // No preset to start from, and every capability unticked.
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    for (const box of screen.getAllByRole("checkbox").filter((c) => c.closest("fieldset"))) {
      expect(box).not.toBeChecked();
    }
    next();
    // The message is in the summary of what to fix and on the step itself.
    expect((await screen.findAllByText("Choose at least one capability.")).length).toBeGreaterThan(0);
    expect(screen.getByRole("alert")).toHaveTextContent("Choose at least one capability.");
  });

  it("offers a way back to the purposes", async () => {
    query = "purpose=affiliate";
    mockApi(routes());
    mount();
    expect(screen.getByRole("link", { name: "Choose a different purpose" })).toHaveAttribute("href", "/admin/partners/new");
  });
});
