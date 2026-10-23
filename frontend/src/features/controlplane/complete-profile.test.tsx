import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CompleteProfile } from "./profile/CompleteProfile";
import { stepsFor } from "./profile/steps";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";

/**
 * A partner's Complete Your Profile (ADR-048). What these tests hold on to:
 *
 * - the steps follow the labels the server derived from the grants, so a partner
 *   holding several gets the union, in one order;
 * - every step saves through a command that already existed (capability grants,
 *   referral codes, the brand, the partner status) - the wizard adds no new write;
 * - completion is the server's answer, shown beside the steps; activation is a
 *   separate, reasoned act on the Review step, offered only when complete.
 */

let query = "";
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => `/admin/partners/${ID}/profile`,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  replace.mockReset();
  query = "";
});

const ID = "0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11";
const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage", "platform.staff_commission.manage", "platform.staff_pricing.manage"];

const RESELLER = ["customer.provision", "customer.manage", "subscription.sell", "subscription.manage", "pricing.view", "commission.earn", "subpartner.manage"];
const AFFILIATE = ["referral.create", "referral.view", "commission.earn", "commission.view", "payout.view"];
const LABEL: Record<string, string> = { reseller: "Reseller", white_label: "White-label", affiliate: "Affiliate" };

function partner(over: Record<string, unknown> = {}) {
  return {
    id: ID,
    code: "abc",
    display_name: "ABC Technologies",
    legal_name: "",
    contact_name: "",
    contact_email: "",
    payout_currency: "INR",
    status: "draft",
    default_experience: "reseller",
    version: 2,
    created_at: "2026-10-01T00:00:00Z",
    activated_at: null,
    status_reason: "",
    capabilities: [],
    members: [
      {
        id: "m1",
        user_id: "u9",
        email: "admin@abc.example",
        role: "owner",
        added_at: "2026-10-01T00:00:00Z",
        password_set: true,
        full_name: "",
        last_login_at: null,
        invitation_sent_at: null,
        invitation_deferred: false,
        mfa_enabled: false,
      },
    ],
    product_grants: [],
    live_customers: 0,
    experience: [{ key: "reseller", label: "Reseller" }],
    ...over,
  };
}

function summary(labels: string[], held: string[], completion: Record<string, unknown> = {}) {
  const sections = [
    { key: "business", label: "Business information", state: "incomplete", required: true, detail: "Add the business email." },
    { key: "administrator", label: "Partner administrator", state: "complete", required: true, detail: "" },
  ];
  return {
    partner: { ...partner(), experience: undefined },
    experience: labels.map((key) => ({ key, label: LABEL[key] })),
    capabilities: held.map((key) => ({ key, label: key, group: "", granted_at: "2026-10-01T00:00:00Z", expires_at: null })),
    customers: { live: 0, active: 0, suspended: 0, ended: 0, owner_activation_pending: 0 },
    products: [],
    active_subscriptions: 0,
    commission: [],
    commission_rule: null,
    payouts: { open: 0, by_status: {}, latest: null },
    white_label: { enabled: false, brand_configured: false, domain_status: "none", domains: { active: 0, verified: 0, awaiting: 0 } },
    referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
    setup_progress: [],
    profile_completion: {
      percent: 50,
      complete: false,
      required: 2,
      done: 1,
      sections,
      missing: [{ key: "business", label: "Business information", detail: "Add the business email." }],
      setup_status: "setup_required",
      ...completion,
    },
    activation: { can_activate: false, missing: [{ key: "business", label: "Business information", detail: "" }] },
  };
}

const PROFILE = {
  partner_id: ID,
  version: 2,
  business: {
    display_name: "ABC Technologies",
    legal_name: "",
    business_email: "",
    website: "",
    phone: "",
    alternative_email: "",
    registration_number: "",
    tax_number: "",
    country: "IN",
    timezone: "",
    address: { line1: "", line2: "", city: "", region: "", postal_code: "" },
  },
  contacts: {
    primary: { name: "", job_title: "", email: "", phone: "" },
    billing: { same_as_primary: true },
    technical: { same_as_primary: true },
    support: { same_as_primary: true },
  },
  support: { email: "", phone: "", url: "" },
  payout: { currency: "INR", frequency: "manual", contact_email: "" },
  internal_notes: "",
  updated_at: "2026-10-01T00:00:00Z",
};

const base = (labels: string[], held: string[], extra: Record<string, Reply[]> = {}, completion: Record<string, unknown> = {}) => ({
  [`GET /api/v1/admin/partners/${ID}`]: [ok(partner({ experience: labels.map((key) => ({ key, label: LABEL[key] })) }))],
  [`GET /api/v1/admin/partners/${ID}/summary`]: [ok(summary(labels, held, completion))],
  [`GET /api/v1/admin/partners/${ID}/profile`]: [ok(PROFILE)],
  "GET /api/v1/admin/partners/products": [ok({ results: [{ code: "crm", name: "Easy CRM", status: "available", plans: [], trial: null }] })],
  ...extra,
});

function mount(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <CompleteProfile partnerId={ID} />
    </AdminContextForTest>,
  );
}

const stepNames = () =>
  within(screen.getByRole("navigation", { name: "Profile steps" }))
    .getAllByRole("button")
    .map((b) => b.textContent?.replace(/^(✓|\d+)/, "").replace(/ \(.*\)$/, ""));

describe("which steps a partner gets", () => {
  it("follows the labels, never a stored type", () => {
    expect(stepsFor(["reseller"], RESELLER)).toEqual(["business", "products", "pricing", "commission", "sales", "subresellers", "team", "review"]);
    expect(stepsFor(["reseller", "white_label"], ["customer.provision", "commission.earn", "branding.manage", "domain.manage"])).toEqual([
      "business",
      "brand",
      "products",
      "pricing",
      "commission",
      "sales",
      "domain",
      "communication",
      "portal",
      "team",
      "review",
    ]);
    expect(stepsFor(["affiliate"], AFFILIATE)).toEqual(["business", "products", "commission", "referral", "payout", "marketing", "review"]);
  });

  it("gives a combined partner the union, once each", () => {
    const steps = stepsFor(["reseller", "affiliate"], [...RESELLER, ...AFFILIATE]);
    expect(new Set(steps).size).toBe(steps.length);
    for (const s of ["pricing", "sales", "subresellers", "referral", "payout", "marketing", "team"]) expect(steps).toContain(s);
  });
});

describe("the reseller wizard", () => {
  // A large form plus an axe pass: slow in jsdom when the whole suite runs in parallel.
  it("lists the reseller's steps, opens on the first and shows the server's completion", async () => {
    mockApi(base(["reseller"], RESELLER));
    const { container } = mount();
    expect(await screen.findByRole("heading", { level: 2, name: "Step 1 of 8: Business information" })).toBeInTheDocument();
    expect(stepNames()).toEqual([
      "Business information",
      "Products",
      "Pricing",
      "Commission",
      "Customer & sales configuration",
      "Sub-reseller settings",
      "Team",
      "Review",
    ]);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Complete Your Profile: ABC Technologies");
    expect(document.body.textContent).toMatch(/50%/);
    expect(document.body.textContent).toMatch(/Still needed: Business information/);
    await screen.findByLabelText(/^Business email/, { selector: "input" });
    await expectNoViolations(container);
  }, 60_000);

  it("moves with Continue and keeps the step in the address", async () => {
    mockApi(base(["reseller"], RESELLER, { [`GET /api/v1/admin/partners/${ID}/pricing`]: [ok({ currency: "INR", results: [] })] }));
    mount();
    await screen.findByRole("heading", { level: 2, name: /Step 1 of 8/ });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Step 2 of 8: Products" })).toBeInTheDocument();
    expect(replace).toHaveBeenLastCalledWith(`/admin/partners/${ID}/profile?step=products`, { scroll: false });
    expect(screen.getByRole("button", { name: /Products/ })).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { level: 2, name: /Step 1 of 8/ })).toBeInTheDocument();
  });

  it("opens the step named in the address, and the first one for a step it does not have", async () => {
    query = "step=referral";
    mockApi(base(["reseller"], RESELLER));
    mount();
    expect(await screen.findByRole("heading", { level: 2, name: /Step 1 of 8: Business information/ })).toBeInTheDocument();
  });

  it("turns customer and sales permissions on and off with the capability commands", async () => {
    query = "step=sales";
    const held = RESELLER.filter((k) => k !== "pricing.view");
    const calls = mockApi(
      base(["reseller"], held, {
        [`POST /api/v1/admin/partners/${ID}/capabilities`]: [ok({ id: "g", capability: "pricing.view" })],
        [`POST /api/v1/admin/partners/${ID}/capabilities/revoke`]: [ok({})],
      }),
    );
    mount();
    const view = await screen.findByRole("checkbox", { name: "May view pricing" });
    expect(view).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "May create customers" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "May manage subscriptions and renewals" })).toBeChecked();
    fireEvent.click(view);
    await waitFor(() => expect(calls.some((c) => c.key === `POST /api/v1/admin/partners/${ID}/capabilities`)).toBe(true));
    const grant = calls.find((c) => c.key === `POST /api/v1/admin/partners/${ID}/capabilities`)!;
    expect(grant.body).toMatchObject({ capability: "pricing.view" });
    expect((grant.body as { reason: string }).reason.length).toBeGreaterThanOrEqual(10);
    fireEvent.click(screen.getByRole("checkbox", { name: "May create customers" }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/capabilities/revoke"))).toBe(true));
    expect(calls.find((c) => c.key.endsWith("/capabilities/revoke"))!.body).toMatchObject({ capability: "customer.provision" });
  });
});

describe("the affiliate wizard", () => {
  const referrals = {
    summary: { captured: 0, converted: 0, expired: 0 },
    codes: [{ id: "c1", code: "K7Q2M9AB", label: "Spring", status: "active", created_at: "2026-10-01T00:00:00Z", url: "https://auth.workeasy360.example/signup?ref=K7Q2M9AB" }],
    results: [],
    next_cursor: null,
    has_more: false,
    attribution_window_days: 90,
    can_create: true,
  };

  it("calls the first step Profile and has no pricing, customer or team step", async () => {
    mockApi(base(["affiliate"], AFFILIATE));
    mount();
    await screen.findByRole("heading", { level: 2, name: /Step 1 of 7: Profile/ });
    expect(stepNames()).toEqual(["Profile", "Products promoted", "Commission", "Referral program", "Payout settings", "Marketing information", "Review"]);
  });

  it("shows the referral program and creates a code through the staff command", async () => {
    query = "step=referral";
    const calls = mockApi(
      base(["affiliate"], AFFILIATE, {
        [`GET /api/v1/admin/partners/${ID}/referrals`]: [ok(referrals)],
        [`POST /api/v1/admin/partners/${ID}/referral-codes`]: [ok({ id: "c2", code: "ZZZZ2345", label: "Autumn", status: "active", created_at: "2026-10-02T00:00:00Z" })],
      }),
    );
    const { container } = mount();
    expect(await screen.findByText(/90 days/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Referral codes" });
    expect(within(table).getByText("https://auth.workeasy360.example/signup?ref=K7Q2M9AB")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Copy link for K7Q2M9AB" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Campaign name/), { target: { value: "Autumn" } });
    fireEvent.click(screen.getByRole("button", { name: "Create referral code" }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/referral-codes"))).toBe(true));
    expect(calls.find((c) => c.key.endsWith("/referral-codes"))!.body).toEqual({ label: "Autumn" });
    expect(await screen.findByText("Referral code created.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("explains that an affiliate is granted no product", async () => {
    query = "step=products";
    mockApi(
      base(["affiliate"], AFFILIATE, {
        [`GET /api/v1/admin/partners/${ID}/commission`]: [ok({ rules: [], live_versions: {}, in_force: null, partner_earns: true })],
      }),
    );
    mount();
    expect(await screen.findByText(/It is granted no product/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Allow product" })).not.toBeInTheDocument();
  });
});

describe("the review step", () => {
  it("offers Activate with a recorded reason once the profile is complete", async () => {
    query = "step=review";
    const calls = mockApi(
      base(
        ["reseller"],
        RESELLER,
        { [`POST /api/v1/admin/partners/${ID}/status`]: [ok({ id: ID })] },
        {
          percent: 100,
          complete: true,
          done: 2,
          missing: [],
          setup_status: "ready",
          sections: [{ key: "business", label: "Business information", state: "complete", required: true, detail: "" }],
        },
      ),
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Activate partner" }));
    fireEvent.change(screen.getByLabelText(/Reason for/), { target: { value: "Profile complete and checked" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/status"))).toBe(true));
    expect(calls.find((c) => c.key.endsWith("/status"))!.body).toEqual({ status: "active", reason: "Profile complete and checked", expected_version: 2 });
  });

  it("does not offer activation while something required is missing", async () => {
    query = "step=review";
    mockApi(base(["reseller"], RESELLER));
    mount();
    expect(await screen.findByText(/can be activated once every required section is complete/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate partner" })).not.toBeInTheDocument();
  });

  it("moves to the step that finishes a missing section, inside the wizard", async () => {
    query = "step=review";
    mockApi(base(["reseller"], RESELLER));
    mount();
    // jsdom drops the space before the visually hidden part of the name.
    fireEvent.click(await screen.findByRole("button", { name: /^Complete ?Business information$/ }));
    expect(await screen.findByRole("heading", { level: 2, name: /Step 1 of 8: Business information/ })).toBeInTheDocument();
    expect(replace).toHaveBeenLastCalledWith(`/admin/partners/${ID}/profile?step=business`, { scroll: false });
  });

  it("sends an affiliate with no administrator to Users & Permissions, as it has no Team step", async () => {
    query = "step=review";
    mockApi(
      base(["affiliate"], AFFILIATE, {}, {
        sections: [{ key: "administrator", label: "Partner administrator", state: "incomplete", required: true, detail: "Nobody can sign in." }],
      }),
    );
    mount();
    const link = await screen.findByRole("link", { name: /^Open Users & Permissions ?to deal with Partner administrator$/ });
    expect(link).toHaveAttribute("href", `/admin/partners/${ID}?tab=users`);
  });
});

describe("the white-label brand step", () => {
  const setup = {
    partner: { id: ID, code: "abc", display_name: "ABC Technologies", status: "draft", version: 2 },
    experience: [
      { key: "reseller", label: "Reseller" },
      { key: "white_label", label: "White-label" },
    ],
    capabilities: ["branding.manage", "domain.manage"],
    enabled: true,
    missing_capabilities: [],
    customers: 0,
    brand: null,
    products: [],
    domain: null,
    branding_on_custom_domain: "none",
    authentication: { mode: "workeasy360_secure_sign_in", custom_domain_sign_in: false, summary: "Customers sign in with Workeasy360." },
    checks: { brand_saved: false, logo_saved: false, domain_requested: false },
  };

  it("runs the white-label brand step inside the wizard and saves through the brand command", async () => {
    query = "step=brand";
    const labels = ["reseller", "white_label"];
    const held = ["customer.provision", "commission.earn", "branding.manage", "domain.manage"];
    const calls = mockApi(
      base(labels, held, {
        [`GET /api/v1/admin/partners/${ID}/white-label`]: [ok(setup)],
        [`PUT /api/v1/admin/partners/${ID}/brand`]: [
          ok({ brand_name: "ABC Cloud", logo_file_id: null, accent_color: "", theme: {}, support_email: "", support_url: "", portal: {}, version: 1, updated_at: "2026-10-02T00:00:00Z" }),
        ],
      }),
    );
    mount();
    expect(await screen.findByRole("heading", { level: 2, name: /Step 2 of 11: Brand identity/ })).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText(/^Brand name/, { selector: "input" }), { target: { value: "ABC Cloud" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & continue" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT /api/v1/admin/partners/${ID}/brand`)).toBe(true));
    expect(calls.find((c) => c.key === `PUT /api/v1/admin/partners/${ID}/brand`)!.body).toMatchObject({ brand_name: "ABC Cloud" });
    // The completion beside the steps is read again after the save.
    await waitFor(() => expect(calls.filter((c) => c.key === `GET /api/v1/admin/partners/${ID}/summary`).length).toBeGreaterThan(1));
  });
});

describe("who may use it", () => {
  it("is refused to staff who may not manage partners", () => {
    mockApi({});
    mount(["platform.staff_partner.view"]);
    expect(screen.getByRole("alert")).toHaveTextContent(/permission to manage partners/);
  });
});
