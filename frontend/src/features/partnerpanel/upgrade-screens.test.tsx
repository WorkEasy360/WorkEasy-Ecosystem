import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import type { OwnProfile, UpgradeRequest, UpgradeState } from "@/lib/api/partnerPanel";
import { UpgradeGrow } from "./UpgradeGrow";
import { UpgradePage } from "./UpgradeWizard";

/**
 * "Grow Your Partnership" and the upgrade wizard (ADR-049): what a person sees,
 * and exactly what each click asks the server to do. The API is mocked, so these
 * are about the screens - the rules that decide anything are the server's.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/affiliate/dashboard",
}));

const ID = "44444444-4444-4444-4444-444444444444";
const API = `/api/v1/partner/${ID}`;
const BASE = "/partner/affiliate";
const perm = (...keys: string[]) => keys.map((k) => `platform.partner_${k}`);

function partner(): PortalPartner {
  return {
    partner_id: ID,
    code: "af",
    display_name: "Aff Co",
    status: "active",
    role: "owner",
    is_admin: true,
    capabilities: [],
    permissions: perm("dashboard.view", "profile.view", "profile.manage", "referral.view"),
  };
}

function mount(ui: React.ReactNode) {
  return render(<PartnerContext.Provider value={{ partner: partner(), base: BASE }}>{ui}</PartnerContext.Provider>);
}

const STEPS = {
  reseller: [
    { key: "business", label: "Business" },
    { key: "products", label: "Products" },
    { key: "pricing", label: "Pricing" },
    { key: "commercial", label: "Commercial" },
    { key: "review", label: "Review" },
  ],
  white_label: [
    { key: "business", label: "Business" },
    { key: "brand", label: "Brand" },
    { key: "products", label: "Products" },
    { key: "pricing", label: "Pricing" },
    { key: "domain", label: "Domain" },
    { key: "review", label: "Review" },
  ],
};

const REQUEST = (over: Partial<UpgradeRequest> = {}): UpgradeRequest => ({
  id: "req-1",
  partner_id: ID,
  from_experience: "affiliate",
  to_experience: "reseller",
  status: "requested",
  proposal: {},
  requested_at: "2026-10-24T10:00:00Z",
  submitted_at: null,
  decided_at: null,
  info_request_note: "",
  decision_note: "",
  capabilities_added: [],
  capabilities_removed: [],
  products_granted: [],
  version: 0,
  editable: true,
  ...over,
});

const STATE = (over: Partial<UpgradeState> = {}): UpgradeState => ({
  current: "affiliate",
  eligible: ["reseller", "white_label"],
  can_request: true,
  open: null,
  recent: [],
  steps: STEPS as UpgradeState["steps"],
  products: [],
  checklist: [],
  ...over,
});

const PROFILE: OwnProfile = {
  partner_id: ID,
  version: 3,
  business: {
    display_name: "Aff Co",
    legal_name: "",
    business_email: "hello@aff.example.com",
    website: "",
    phone: "+44 20 7946 0958",
    alternative_email: "",
    registration_number: "",
    tax_number: "",
    country: "GB",
    timezone: "",
    address: {},
  },
  contacts: { primary: { name: "Pat Owner", email: "pat@aff.example.com" } },
  support: { email: "", phone: "", url: "" },
  payout: { currency: "GBP" },
  updated_at: "2026-10-24T10:00:00Z",
};

const PRODUCTS = [
  { code: "crm", name: "Easy CRM" },
  { code: "books", name: "Easy Books" },
];

const upgrade = (...replies: Reply[]) => ({ [`GET ${API}/upgrade`]: replies });

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Grow Your Partnership", () => {
  it("offers an Affiliate both upgrades, as links to the upgrade pages", async () => {
    mockApi(upgrade(ok(STATE())));
    const { container } = mount(<UpgradeGrow workspace="affiliate" />);
    const section = await screen.findByRole("region", { name: "Grow Your Partnership" });
    const reseller = within(section).getByRole("link", { name: "Upgrade to Reseller" });
    const whiteLabel = within(section).getByRole("link", { name: "Upgrade to White-label" });
    expect(reseller.getAttribute("href")).toBe("/partner/affiliate/upgrade/reseller");
    expect(whiteLabel.getAttribute("href")).toBe("/partner/affiliate/upgrade/white-label");
    expect(within(section).getByText("Manage customers, subscriptions and direct sales.")).toBeTruthy();
    expect(within(section).getByText("Sell under your own brand and domain.")).toBeTruthy();
    await expectNoViolations(container);
  });

  it("offers a Reseller White-label only", async () => {
    mockApi(upgrade(ok(STATE({ current: "reseller", eligible: ["white_label"] }))));
    mount(<UpgradeGrow workspace="reseller" />);
    await screen.findByRole("link", { name: "Upgrade to White-label" });
    expect(screen.queryByRole("link", { name: "Upgrade to Reseller" })).toBeNull();
  });

  it("draws nothing at all for White-label", async () => {
    const calls = mockApi(upgrade(ok(STATE({ current: "white_label", eligible: [] }))));
    const { container } = mount(<UpgradeGrow workspace="white_label" />);
    await waitFor(() => expect(calls.length).toBe(1));
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
    expect(screen.queryByText("Grow Your Partnership")).toBeNull();
  });

  it("draws nothing when the read fails: an offer never gets in the dashboard's way", async () => {
    const calls = mockApi(upgrade({ status: 500, body: { detail: "boom" } }));
    const { container } = mount(<UpgradeGrow workspace="affiliate" />);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.textContent).toBe("");
  });

  it("gives a member who cannot act no button", async () => {
    mockApi(upgrade(ok(STATE({ can_request: false }))));
    mount(<UpgradeGrow workspace="affiliate" />);
    await screen.findByRole("region", { name: "Grow Your Partnership" });
    expect(screen.queryByRole("link", { name: /^Upgrade to/ })).toBeNull();
    expect(screen.getAllByText("A partner administrator can request this upgrade.")).toHaveLength(2);
  });

  it("shows an open request's status and the way back into it, and no other offer", async () => {
    mockApi(upgrade(ok(STATE({ open: REQUEST({ status: "pending_approval", editable: false }) }))));
    mount(<UpgradeGrow workspace="affiliate" />);
    await screen.findByText("Pending approval");
    expect(screen.getByRole("link", { name: "View your request" }).getAttribute("href")).toBe("/partner/affiliate/upgrade/reseller");
    expect(screen.queryByRole("link", { name: /^Upgrade to/ })).toBeNull();
  });

  it("repeats what staff asked for when a request needs more information", async () => {
    mockApi(upgrade(ok(STATE({ open: REQUEST({ status: "profile_required", info_request_note: "Please confirm your VAT number" }) }))));
    mount(<UpgradeGrow workspace="affiliate" />);
    await screen.findByText(/Please confirm your VAT number/);
    expect(screen.getByRole("link", { name: "Continue your request" })).toBeTruthy();
  });

  it("tells an approved partner, still on its old workspace, where its new one is", async () => {
    const approved = REQUEST({ status: "approved", editable: false });
    mockApi(upgrade(ok(STATE({ current: "reseller", eligible: ["white_label"], recent: [approved] }))));
    mount(<UpgradeGrow workspace="affiliate" />);
    const link = await screen.findByRole("link", { name: "Open your Reseller workspace" });
    expect(link.getAttribute("href")).toBe("/partner/reseller/dashboard");
  });
});

describe("the upgrade page", () => {
  it("explains what happens before anything is created, and asks nothing about a login", async () => {
    mockApi(upgrade(ok(STATE())));
    const { container } = mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Upgrade to Reseller" });
    expect(screen.getByText(/same sign-in and the same partner account/)).toBeTruthy();
    expect(screen.queryByLabelText(/password/i)).toBeNull();
    expect(screen.queryByLabelText(/^email/i)).toBeNull();
    await expectNoViolations(container);
  });

  it("starts a request only when asked, then opens the first step", async () => {
    const calls = mockApi({
      ...upgrade(ok(STATE()), ok(STATE({ open: REQUEST(), products: PRODUCTS }))),
      [`POST ${API}/upgrade/requests`]: [{ status: 201, body: REQUEST() }],
      [`GET ${API}/profile`]: [ok(PROFILE)],
    });
    mount(<UpgradePage target="reseller" />);
    const start = await screen.findByRole("button", { name: "Start your Reseller request" });
    expect(calls.filter((c) => c.key.startsWith("POST"))).toHaveLength(0);
    fireEvent.click(start);
    await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
    const post = calls.find((c) => c.key === `POST ${API}/upgrade/requests`)!;
    expect(post.body).toEqual({ experience: "reseller" });
  });

  it("shows the Reseller wizard as five steps and the White-label wizard as six", async () => {
    mockApi({ ...upgrade(ok(STATE({ open: REQUEST(), products: PRODUCTS }))), [`GET ${API}/profile`]: [ok(PROFILE)] });
    const first = mount(<UpgradePage target="reseller" />);
    const list = await screen.findByRole("list", { name: "Upgrade steps" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "1. Business",
      "2. Products",
      "3. Pricing",
      "4. Commercial",
      "5. Review",
    ]);
    first.unmount();

    mockApi({
      ...upgrade(ok(STATE({ open: REQUEST({ to_experience: "white_label" }), products: PRODUCTS }))),
      [`GET ${API}/profile`]: [ok(PROFILE)],
    });
    mount(<UpgradePage />);
    const wl = await screen.findByRole("list", { name: "Upgrade steps" });
    expect(within(wl).getAllByRole("listitem").map((li) => li.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "1. Business",
      "2. Brand",
      "3. Products",
      "4. Pricing",
      "5. Domain",
      "6. Review",
    ]);
    expect(within(wl).getByText("Domain")).toBeTruthy();
  });

  it("saves the business step to the profile, then each step to the request, carrying the version", async () => {
    const calls = mockApi({
      ...upgrade(ok(STATE({ open: REQUEST(), products: PRODUCTS }))),
      [`GET ${API}/profile`]: [ok(PROFILE)],
      [`PUT ${API}/profile`]: [ok({ ...PROFILE, version: 4 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/products`]: [ok(REQUEST({ version: 1, proposal: { products: ["crm"] } }))],
      [`PUT ${API}/upgrade/requests/req-1/sections/pricing`]: [ok(REQUEST({ version: 2 }))],
    });
    const { container } = mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
    await screen.findByLabelText(/^Phone/);
    await expectNoViolations(container);
    fireEvent.change(await screen.findByLabelText(/^Tax/), { target: { value: "GB123456789" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

    await screen.findByRole("heading", { name: "Step 2 of 5: Products" });
    const profilePut = calls.find((c) => c.key === `PUT ${API}/profile`)!;
    expect(profilePut.body).toMatchObject({
      expected_version: 3,
      business: { phone: "+44 20 7946 0958", country: "GB", tax_number: "GB123456789" },
      contacts: { billing: { same_as_primary: true } },
    });

    // Nothing can be sent until a product is chosen.
    const next = screen.getByRole("button", { name: "Save and continue" }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Easy CRM"));
    expect(next.disabled).toBe(false);
    fireEvent.click(next);
    await screen.findByRole("heading", { name: "Step 3 of 5: Pricing" });
    const products = calls.find((c) => c.key === `PUT ${API}/upgrade/requests/req-1/sections/products`)!;
    expect(products.body).toEqual({ data: { products: ["crm"] }, expected_version: 0 });

    fireEvent.click(screen.getByLabelText("I would like to discuss my pricing"));
    fireEvent.change(screen.getByLabelText(/Notes for the review/), { target: { value: "Volume discount" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 4 of 5: Commercial" });
    // The second save carries the version the first one returned.
    const pricing = calls.find((c) => c.key === `PUT ${API}/upgrade/requests/req-1/sections/pricing`)!;
    expect(pricing.body).toEqual({ data: { preference: "negotiated", notes: "Volume discount" }, expected_version: 1 });
  }, 60_000);

  it("shows the server's refusal and stays on the step", async () => {
    mockApi({
      ...upgrade(ok(STATE({ open: REQUEST(), products: PRODUCTS }))),
      [`GET ${API}/profile`]: [ok(PROFILE)],
      [`PUT ${API}/profile`]: [{ status: 400, body: { code: "partner_validation_error", detail: "phone is not a valid phone number." } }],
    });
    mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
    fireEvent.click(await screen.findByRole("button", { name: "Save and continue" }));
    await screen.findByText("phone is not a valid phone number.");
    expect(screen.getByRole("heading", { name: "Step 1 of 5: Business" })).toBeTruthy();
  });

  it("sends the request with the version it last saw, and then describes it as pending", async () => {
    const checklist = [{ key: "business", label: "Business details", required: true, done: true, detail: "" }];
    const open = REQUEST({
      version: 4,
      proposal: { products: ["crm"], pricing: { preference: "standard", notes: "" }, commercial: { expected_customers: 40, team_size: null, customer_management: "", notes: "" } },
    });
    const pending = REQUEST({ version: 5, status: "pending_approval", editable: false, proposal: open.proposal });
    const calls = mockApi({
      ...upgrade(ok(STATE({ open, products: PRODUCTS, checklist })), ok(STATE({ open, products: PRODUCTS, checklist })), ok(STATE({ open: pending, products: PRODUCTS }))),
      [`GET ${API}/profile`]: [ok(PROFILE)],
      [`PUT ${API}/profile`]: [ok({ ...PROFILE, version: 4 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/products`]: [ok({ ...open, version: 5 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/pricing`]: [ok({ ...open, version: 6 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/commercial`]: [ok({ ...open, version: 7 })],
      [`POST ${API}/upgrade/requests/req-1/submit`]: [ok({ ...pending, version: 8 })],
    });
    mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
    fireEvent.click(await screen.findByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 2 of 5: Products" });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 3 of 5: Pricing" });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 4 of 5: Commercial" });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 5 of 5: Review" });

    expect(screen.getByText(/Done: Business details/)).toBeTruthy();
    expect(screen.getByText("Easy CRM")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send for review" }));
    await screen.findByText("Pending approval");
    const submit = calls.find((c) => c.key === `POST ${API}/upgrade/requests/req-1/submit`)!;
    // The version the last save returned, not the page's older copy.
    expect(submit.body).toEqual({ expected_version: 7 });
    expect(screen.getByText(/Nothing about your account has changed/)).toBeTruthy();
  });

  it("disables Send for review while a required item is missing", async () => {
    const checklist = [{ key: "products", label: "Products to sell", required: true, done: false, detail: "Choose at least one product." }];
    const open = REQUEST({ version: 1 });
    mockApi({
      ...upgrade(ok(STATE({ open, products: PRODUCTS, checklist }))),
      [`GET ${API}/profile`]: [ok(PROFILE)],
      [`PUT ${API}/profile`]: [ok({ ...PROFILE, version: 4 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/products`]: [ok({ ...open, version: 2 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/pricing`]: [ok({ ...open, version: 3 })],
      [`PUT ${API}/upgrade/requests/req-1/sections/commercial`]: [ok({ ...open, version: 4 })],
    });
    mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
    fireEvent.click(await screen.findByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 2 of 5: Products" });
    fireEvent.click(screen.getByLabelText("Easy CRM"));
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 3 of 5: Pricing" });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 4 of 5: Commercial" });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByRole("heading", { name: "Step 5 of 5: Review" });
    expect(screen.getByText(/Still needed: Products to sell/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send for review" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("withdraws a pending request when asked, and says nothing changed", async () => {
    const pending = REQUEST({ status: "pending_approval", editable: false });
    const calls = mockApi({
      ...upgrade(ok(STATE({ open: pending, products: PRODUCTS })), ok(STATE())),
      [`POST ${API}/upgrade/requests/req-1/cancel`]: [ok(REQUEST({ status: "cancelled", editable: false }))],
    });
    mount(<UpgradePage target="reseller" />);
    await screen.findByText("Pending approval");
    fireEvent.click(screen.getByRole("button", { name: "Withdraw request" }));
    await screen.findByRole("button", { name: "Start your Reseller request" });
    expect(calls.some((c) => c.key === `POST ${API}/upgrade/requests/req-1/cancel`)).toBe(true);
  });

  it("tells a member who cannot act to ask an administrator", async () => {
    mockApi(upgrade(ok(STATE({ can_request: false }))));
    mount(<UpgradePage target="reseller" />);
    await screen.findByText(/Only a partner administrator can request an upgrade/);
    expect(screen.queryByRole("button", { name: /Start your/ })).toBeNull();
  });

  it("says there is nothing to upgrade to when the server offers nothing", async () => {
    mockApi(upgrade(ok(STATE({ current: "white_label", eligible: [] }))));
    mount(<UpgradePage target="reseller" />);
    await screen.findByText("There is no upgrade available for your account right now.");
    expect(screen.queryByRole("button", { name: /Start your/ })).toBeNull();
  });

  it("congratulates an approved partner and points to the new workspace", async () => {
    const approved = REQUEST({ status: "approved", editable: false });
    mockApi(upgrade(ok(STATE({ current: "reseller", eligible: ["white_label"], recent: [approved] }))));
    mount(<UpgradePage target="reseller" />);
    await screen.findByRole("heading", { name: "Your Reseller upgrade is approved" });
    expect(screen.getByRole("link", { name: "Open your Reseller workspace" }).getAttribute("href")).toBe("/partner/reseller/dashboard");
  });
});
