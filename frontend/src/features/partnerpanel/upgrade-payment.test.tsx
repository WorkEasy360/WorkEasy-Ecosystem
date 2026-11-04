import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PartnerContext } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import type { PortalPartner } from "@/lib/api/controlPlane";
import type { OwnProfile, UpgradeRequest, UpgradeState } from "@/lib/api/partnerPanel";
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


/**
 * Paying an upgrade's set-up fee (2026-11-03): offline with the transfer's
 * reference, or online on the provider's own page. The browser only ever asks;
 * the server decides what was paid.
 */

const FEE = { amount_minor: 2_500_000, currency: "INR", settled: false, payment_method: null, payment_reference: "", paid_online: false, paid_at: null, online_payment: null };
const PROPOSAL = {
  products: ["crm"],
  pricing: { preference: "standard" as const, notes: "" },
  commercial: { expected_customers: 40, team_size: null, customer_management: "", notes: "" },
};
const CHECKLIST = [{ key: "business", label: "Business details", required: true, done: true, detail: "" }];

async function toReview() {
  await screen.findByRole("heading", { name: "Step 1 of 5: Business" });
  fireEvent.click(await screen.findByRole("button", { name: "Save and continue" }));
  await screen.findByRole("heading", { name: "Step 2 of 5: Products" });
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
  await screen.findByRole("heading", { name: "Step 3 of 5: Pricing" });
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
  await screen.findByRole("heading", { name: "Step 4 of 5: Commercial" });
  fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
  await screen.findByRole("heading", { name: "Step 5 of 5: Review" });
}

function routes(open: UpgradeRequest, state: Partial<UpgradeState>, extra: Record<string, Reply[]> = {}) {
  const s = STATE({ open, products: PRODUCTS, checklist: CHECKLIST, ...state });
  return {
    ...upgrade(ok(s), ok(s), ok(s), ok(s)),
    [`GET ${API}/profile`]: [ok(PROFILE)],
    [`PUT ${API}/profile`]: [ok({ ...PROFILE, version: 4 })],
    [`PUT ${API}/upgrade/requests/req-1/sections/products`]: [ok({ ...open, version: 5 })],
    [`PUT ${API}/upgrade/requests/req-1/sections/pricing`]: [ok({ ...open, version: 6 })],
    [`PUT ${API}/upgrade/requests/req-1/sections/commercial`]: [ok({ ...open, version: 7 })],
    ...extra,
  };
}

describe("paying the set-up fee", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("offline: shows how to pay, needs the transfer's reference, and sends it", async () => {
    const open = REQUEST({ version: 4, setup_fee: FEE, proposal: PROPOSAL });
    const pending = REQUEST({ version: 8, status: "pending_approval", editable: false, setup_fee: { ...FEE, payment_method: "offline", payment_reference: "UTR 4029" } });
    const calls = mockApi(
      routes(open, { payment: { instructions: "Bank: Workeasy360 Ltd\nUPI: pay@w360", online_available: false } }, {
        [`POST ${API}/upgrade/requests/req-1/submit`]: [ok(pending)],
      }),
    );
    const { container } = mount(<UpgradePage target="reseller" />);
    await toReview();
    expect(screen.getByRole("heading", { name: /Set-up fee: ₹25,000.00/ })).toBeTruthy();
    expect(screen.getByText(/UPI: pay@w360/)).toBeTruthy();
    // No provider takes INR here: no online choice.
    expect(screen.queryByRole("radio", { name: "Pay online now" })).toBeNull();
    const send = screen.getByRole("button", { name: "Send for review" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Transaction reference/), { target: { value: " UTR 4029 " } });
    expect(send.disabled).toBe(false);
    await expectNoViolations(container);
    fireEvent.click(send);
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${API}/upgrade/requests/req-1/submit`)?.body).toEqual({ expected_version: 7, payment_reference: "UTR 4029" }));
  });

  it("online: sends the partner to the provider's page, and pays nothing itself", async () => {
    const open = REQUEST({ version: 4, setup_fee: FEE, proposal: PROPOSAL });
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, assign, search: "" } as unknown as Location);
    const calls = mockApi(
      routes(open, { payment: { instructions: "", online_available: true } }, {
        [`POST ${API}/upgrade/requests/req-1/pay`]: [
          ok({
            request: { ...open, version: 7 },
            payment: { id: "pay-1", status: "awaiting_payment", amount_minor: 2_500_000, currency: "INR", checkout_url: "https://checkout.example/pay-1", test_mode: true, completed_at: null, failure_code: "" },
          }),
        ],
      }),
    );
    mount(<UpgradePage target="reseller" />);
    await toReview();
    expect((screen.getByRole("radio", { name: "Pay online now" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Pay ₹25,000.00 and send for review" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://checkout.example/pay-1"));
    expect(calls.find((c) => c.key === `POST ${API}/upgrade/requests/req-1/pay`)?.body).toEqual({ expected_version: 7 });
    expect(calls.some((c) => c.key === `POST ${API}/upgrade/requests/req-1/submit`)).toBe(false);
  });

  it("says how a sent request's fee was paid", async () => {
    const pending = REQUEST({ status: "pending_approval", editable: false, setup_fee: { ...FEE, payment_method: "online", paid_online: true } });
    mockApi(upgrade(ok(STATE({ open: pending }))));
    mount(<UpgradePage target="reseller" />);
    expect(await screen.findByText(/Set-up fee ₹25,000.00: paid online/)).toBeTruthy();
  });
});
