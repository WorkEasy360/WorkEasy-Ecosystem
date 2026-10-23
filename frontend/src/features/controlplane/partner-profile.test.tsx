import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerDetailScreen } from "./AdminPartners";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * The partner detail page, "complete setup" (simplified by ADR-048): the Profile
 * tab (business details and contacts together), the Overview's profile
 * completion and the header's activation guidance, the per-product trial and
 * portal choices, the people table and the Activity columns. What the
 * backend cannot enforce for the browser: each tab sends only its own parts, a
 * save carries the version it was read at, a problem lands on the field it
 * names, guidance never replaces the server's decision, and nothing is drawn
 * from the response except the fields the screen is meant to show.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/partners/p1",
}));

const ID = "p1";
const BASE = `/api/v1/admin/partners/${ID}`;
const ALL = [
  "platform.staff_partner.view",
  "platform.staff_partner.manage",
  "platform.staff_commission.view",
  "platform.staff_commission.manage",
  "platform.staff_pricing.manage",
  "platform.staff_audit.view",
];
const VIEW_ONLY = ["platform.staff_partner.view"];

const MEMBERS = [
  { id: "m1", user_id: "u1", email: "priya@acme.example", role: "admin", added_at: "2026-01-02T00:00:00Z", password_set: true, full_name: "Priya Shah", last_login_at: "2026-02-01T09:00:00Z", invitation_sent_at: "2026-01-02T00:00:00Z", invitation_deferred: false, mfa_enabled: true },
  { id: "m2", user_id: "u2", email: "ops@acme.example", role: "viewer", added_at: "2026-01-03T00:00:00Z", password_set: false, full_name: "", last_login_at: null, invitation_sent_at: "2026-02-10T10:00:00Z", invitation_deferred: false, mfa_enabled: false },
  { id: "m3", user_id: "u3", email: "later@acme.example", role: "viewer", added_at: "2026-01-04T00:00:00Z", password_set: false, full_name: "Lena Later", last_login_at: null, invitation_sent_at: null, invitation_deferred: true, mfa_enabled: false },
  { id: "m4", user_id: "u4", email: "old@acme.example", role: "viewer", added_at: "2025-12-01T00:00:00Z", password_set: false, full_name: "", last_login_at: null, invitation_sent_at: null, invitation_deferred: false, mfa_enabled: false },
];

const DETAIL = {
  id: ID,
  code: "acme",
  display_name: "Acme Partners",
  legal_name: "Acme Partners Ltd",
  contact_name: "Priya Shah",
  contact_email: "priya@acme.example",
  payout_currency: "GBP",
  status: "active",
  version: 3,
  created_at: "2026-01-01T00:00:00Z",
  activated_at: "2026-01-02T00:00:00Z",
  status_reason: "",
  capabilities: [{ capability: "customer.provision", granted_at: "2026-01-02T00:00:00Z", expires_at: null, is_effective: true }],
  members: MEMBERS,
  product_grants: [
    { id: "g1", product_code: "crm", plan_code: null, trial_allowed: true, portal_visible: true },
    { id: "g2", product_code: "books", plan_code: "books_basic", trial_allowed: false, portal_visible: true },
  ],
  live_customers: 2,
  experience: [
    { key: "reseller", label: "Reseller" },
    { key: "white_label", label: "White-label" },
  ],
};

/** The profile completion the server computed: an active partner still waiting
 * for its domain. */
const COMPLETION = {
  percent: 86,
  complete: false,
  required: 7,
  done: 6,
  sections: [
    { key: "business", label: "Business details", state: "complete", required: true, detail: "" },
    { key: "capabilities", label: "Capabilities", state: "complete", required: true, detail: "" },
    { key: "products", label: "Products", state: "complete", required: true, detail: "" },
    { key: "pricing", label: "Pricing", state: "optional", required: false, detail: "Customers pay the list price." },
    { key: "commission", label: "Commission", state: "complete", required: true, detail: "programme default" },
    { key: "administrator", label: "Administrator", state: "complete", required: true, detail: "" },
    { key: "brand", label: "Brand", state: "complete", required: true, detail: "" },
    { key: "domain", label: "Domain", state: "pending", required: true, detail: "requested" },
  ],
  missing: [{ key: "domain", label: "Domain", detail: "requested" }],
  setup_status: "active",
};

const SUMMARY = {
  partner: { ...DETAIL },
  experience: [
    { key: "reseller", label: "Reseller" },
    { key: "white_label", label: "White-label" },
  ],
  capabilities: [{ key: "customer.provision", label: "Create customer organizations", group: "customer_management", granted_at: "2026-01-02T00:00:00Z", expires_at: null }],
  customers: { live: 2, active: 2, suspended: 0, ended: 1, owner_activation_pending: 1 },
  products: [
    { grant_id: "g1", product_code: "crm", product_name: "CRM", plan_code: null, trial_allowed: true, portal_visible: true },
    { grant_id: "g2", product_code: "books", product_name: "Books", plan_code: "books_basic", trial_allowed: false, portal_visible: true },
  ],
  active_subscriptions: 1,
  commission: [{ currency: "GBP", balance_minor: 5000, unpaid_minor: 2000, entries: 3 }],
  commission_rule: { code: "default", version: 1, rate_bp: 1000, product_code: "", scope: "programme_default", effective_from: "2026-01-01T00:00:00Z" },
  payouts: { open: 1, by_status: { draft: 1 }, latest: null },
  white_label: { enabled: true, brand_configured: false, domain_status: "requested", domains: { active: 0, verified: 0, awaiting: 1 } },
  referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
  setup_progress: [],
  profile_completion: COMPLETION,
  activation: { can_activate: false, missing: COMPLETION.missing },
};

/** A draft whose profile lacks its business email and an administrator. */
const DRAFT_MISSING = [
  { key: "business", label: "Business details", detail: "Add the company's business email." },
  { key: "capabilities", label: "Capabilities", detail: "Grant at least one capability." },
  { key: "administrator", label: "Partner administrator", detail: "Name the person who will run the account." },
];
const DRAFT_INCOMPLETE = {
  ...SUMMARY,
  partner: { ...SUMMARY.partner, status: "draft" },
  profile_completion: {
    percent: 43,
    complete: false,
    required: 7,
    done: 3,
    sections: [
      { key: "business", label: "Business details", state: "incomplete", required: true, detail: "Add the company's business email." },
      { key: "capabilities", label: "Capabilities", state: "incomplete", required: true, detail: "Grant at least one capability." },
      { key: "products", label: "Products", state: "complete", required: true, detail: "" },
      { key: "pricing", label: "Pricing", state: "optional", required: false, detail: "Customers pay the list price." },
      { key: "commission", label: "Commission", state: "complete", required: true, detail: "" },
      { key: "administrator", label: "Partner administrator", state: "incomplete", required: true, detail: "Name the person who will run the account." },
      { key: "brand", label: "Brand", state: "complete", required: true, detail: "" },
    ],
    missing: DRAFT_MISSING,
    setup_status: "setup_required",
  },
  activation: { can_activate: false, missing: DRAFT_MISSING },
};

/** A draft whose profile is complete: ready for staff to activate. */
const DRAFT_READY = {
  ...SUMMARY,
  partner: { ...SUMMARY.partner, status: "draft" },
  profile_completion: {
    ...COMPLETION,
    percent: 100,
    complete: true,
    done: 7,
    sections: COMPLETION.sections.map((x) => (x.required ? { ...x, state: "complete", detail: "" } : x)),
    missing: [],
    setup_status: "ready",
  },
  activation: { can_activate: true, missing: [] },
};

const PROFILE = {
  partner_id: ID,
  version: 3,
  business: {
    display_name: "Acme Partners",
    legal_name: "Acme Partners Ltd",
    business_email: "hello@acme.example",
    website: "https://acme.example",
    phone: "+44 20 7946 0000",
    alternative_email: "",
    registration_number: "12345678",
    tax_number: "GB123456789",
    country: "GB",
    timezone: "Europe/London",
    address: { line1: "1 High Street", line2: "", city: "London", region: "", postal_code: "N1 1AA" },
  },
  contacts: {
    primary: { name: "Priya Shah", job_title: "Director", email: "priya@acme.example", phone: "" },
    billing: { same_as_primary: true },
    technical: { same_as_primary: false, name: "Tom Tech", job_title: "", email: "tom@acme.example", phone: "+44 20 7946 0001" },
    support: { same_as_primary: true },
  },
  support: { email: "help@acme.example", phone: "", url: "" },
  payout: { currency: "GBP", frequency: "monthly", contact_email: "finance@acme.example" },
  internal_notes: "Prefers email.",
  updated_at: "2026-02-01T00:00:00Z",
};

const CATALOG = {
  results: [
    { code: "crm", name: "CRM", status: "available", plans: [], trial: { enabled: true, duration_days: 14, seat_limit: null, payment_method_required: false, eligible_plan_codes: [] } },
    { code: "books", name: "Books", status: "available", plans: [], trial: null },
  ],
};

const CAPABILITIES = {
  results: [{ key: "customer.provision", label: "Create customer organizations", short_label: "Create customers", group: "customer_management" }],
  groups: [],
  presets: [],
};

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    [`GET ${BASE}`]: [ok(DETAIL)],
    [`GET ${BASE}/summary`]: [ok(SUMMARY)],
    [`GET ${BASE}/profile`]: [ok(PROFILE)],
    [`GET ${BASE}/pricing`]: [ok({ currency: "GBP", results: [] })],
    [`GET ${BASE}/commission`]: [ok({ partner_earns: true, rules: [], live_versions: {}, in_force: null })],
    "GET /api/v1/admin/partners/products": [ok(CATALOG)],
    "GET /api/v1/admin/partners/capabilities": [ok(CAPABILITIES)],
    ...extra,
  };
}

const STEP_UP: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };
const writes = (calls: Call[]) => calls.filter((c) => !c.key.startsWith("GET "));
const putCalls = (calls: Call[]) => calls.filter((c) => c.key === `PUT ${BASE}/profile`);
type ProfileBody = {
  expected_version: number;
  business?: Record<string, unknown> & { address?: Record<string, string> };
  contacts?: Record<string, unknown>;
  support?: Record<string, string>;
  payout?: Record<string, string>;
  internal_notes?: string;
};

function mount(tab = "overview", permissions = ALL) {
  query = tab === "overview" ? "" : `tab=${tab}`;
  return render(
    <AdminContextForTest permissions={permissions}>
      <PartnerDetailScreen partnerId={ID} />
    </AdminContextForTest>,
  );
}

const change = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

// --- the tabs ---------------------------------------------------------------------------------

describe("the tabs", () => {
  it("lists Profile after Overview, and keeps the relevance rules", async () => {
    mockApi(routes());
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    const tabs = within(screen.getByRole("tablist", { name: "Partner sections" })).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Overview",
      "Profile",
      "Products",
      "Pricing",
      "Commission",
      "Customers",
      "Branding",
      "Domains",
      "Payouts",
      "Users & Permissions",
      "Activity",
    ]);
  });

  it("shows Referrals only with Affiliate, Branding and Domains only with White-label", async () => {
    mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, experience: [{ key: "affiliate", label: "Affiliate" }] })],
        [`GET ${BASE}/summary`]: [ok({ ...SUMMARY, experience: [{ key: "affiliate", label: "Affiliate" }] })],
      }),
    );
    mount();
    await screen.findByRole("heading", { level: 1, name: "Acme Partners" });
    await waitFor(() => expect(screen.getAllByRole("tab").map((t) => t.textContent)).toContain("Referrals"));
    const labels = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(labels).toContain("Profile");
    expect(labels).not.toContain("Branding");
    expect(labels).not.toContain("Domains");
  });

  it("opens Profile - business details and contacts together - from either old deep link, and a view-only staff member still sees it", async () => {
    mockApi(routes());
    const { unmount } = mount("business", VIEW_ONLY);
    expect(await screen.findByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("hello@acme.example")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Billing contact" })).toBeInTheDocument();
    unmount();
    mockApi(routes());
    mount("contacts", VIEW_ONLY);
    expect(await screen.findByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("hello@acme.example")).toBeInTheDocument();
    const labels = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(labels).not.toContain("Payouts");
    expect(labels).not.toContain("Activity");
  });

  it("shows the business details before the contacts on the Profile tab", async () => {
    mockApi(routes());
    mount("profile");
    const business = await screen.findByRole("form", { name: "Business details" });
    const contacts = await screen.findByRole("form", { name: "Contacts" });
    expect(business.compareDocumentPosition(contacts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("moves between the tabs with the arrow keys", async () => {
    mockApi(routes());
    mount();
    const overview = await screen.findByRole("tab", { name: "Overview" });
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Profile" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Products" })).toHaveAttribute("aria-selected", "true");
  });
});

// --- Business Details -------------------------------------------------------------------------

describe("Business Details", () => {
  // A large form plus an axe pass: slow in jsdom when the whole suite runs in parallel.
  it("shows what is saved, marks business email and country as required, and shows the currency as a fact", async () => {
    mockApi(routes());
    const { container } = mount("business");
    expect(await screen.findByLabelText(/^Business email/)).toHaveValue("hello@acme.example");
    expect(screen.getByLabelText(/^Company or partner name/)).toHaveValue("Acme Partners");
    expect(screen.getByLabelText(/^Website/)).toHaveValue("https://acme.example");
    expect(screen.getByLabelText(/^Registration or company number/)).toHaveValue("12345678");
    expect(screen.getByLabelText("Address line 1")).toHaveValue("1 High Street");
    expect(screen.getByLabelText(/^Country/)).toHaveValue("GB");
    expect(screen.getByLabelText("Support email")).toHaveValue("help@acme.example");
    expect(screen.getByLabelText(/^Internal notes/)).toHaveValue("Prefers email.");
    expect(screen.getByLabelText(/^Payout frequency/)).toHaveValue("monthly");
    expect(screen.getByLabelText("Time zone (optional)")).toHaveValue("Europe/London");
    expect(screen.getByLabelText("Time zone (optional)")).not.toBeRequired();

    for (const required of [/^Business email/, /^Country/]) {
      const input = screen.getByLabelText(required);
      expect(input).toBeRequired();
      expect((input as HTMLInputElement).labels?.[0].textContent).toContain("*");
    }
    for (const optional of [/^Website/, /^Business phone/, /^Legal business name/, /^Alternative email/, "Support email"]) {
      expect(screen.getByLabelText(optional)).not.toBeRequired();
    }
    // Once the partner is active the currency is a fact, not a field.
    expect(screen.getByText("GBP").closest("p")).toHaveTextContent(/^Paid in GBP\./);
    expect(screen.queryByLabelText(/currency/i)).not.toBeInTheDocument();
    // The promise about what editing does not touch.
    expect(screen.getByText(/never rewrites sales, subscriptions, commissions or payouts/)).toBeInTheDocument();
    await expectNoViolations(container);
  }, 60_000);

  it("sends only the business, support, payout and notes parts, at the version it read, and shows the saved values", async () => {
    const saved = { ...PROFILE, version: 4, business: { ...PROFILE.business, website: "https://www.acme.example" } };
    const calls = mockApi(
      routes({
        [`GET ${BASE}/profile`]: [ok(PROFILE), ok(saved)],
        [`PUT ${BASE}/profile`]: [ok(saved)],
      }),
    );
    mount("business");
    expect(screen.queryByRole("button", { name: "Save business details" })).not.toBeInTheDocument();
    await screen.findByLabelText(/^Website/);
    expect(screen.getByRole("button", { name: "Save business details" })).toBeDisabled();
    expect(within(screen.getByRole("form", { name: "Business details" })).getByText("No changes to save.")).toBeInTheDocument();

    change(/^Website/, "  https://www.acme.example ");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    const saved_message = await screen.findByText("Business details saved.");
    // The form is replaced by the re-read, so the announced outcome takes keyboard focus.
    await waitFor(() => expect(saved_message).toHaveFocus());

    const [put] = putCalls(calls);
    const body = put.body as ProfileBody;
    expect(Object.keys(body).sort()).toEqual(["business", "expected_version", "internal_notes", "payout", "support"]);
    expect(body.contacts).toBeUndefined();
    expect(body.expected_version).toBe(3);
    expect(body.business?.website).toBe("https://www.acme.example");
    expect(body.business?.country).toBe("GB");
    expect(body.business?.address).toEqual({ line1: "1 High Street", line2: "", city: "London", region: "", postal_code: "N1 1AA" });
    expect(body.business?.timezone).toBe("Europe/London");
    // The currency sent is the one already stored - an active partner's is not
    // offered for editing - and the server treats the same value as no change.
    expect(body.payout).toEqual({ frequency: "monthly", contact_email: "finance@acme.example" });
    // Neither the code nor anything commercial is in the request.
    expect(JSON.stringify(body)).not.toMatch(/"code"|capabilit|price|commission/);
    // The tab re-read the profile and shows the new value.
    await waitFor(() => expect(screen.getByLabelText(/^Website/)).toHaveValue("https://www.acme.example"));
    // The page re-read the partner too, so the next status change uses the new version.
    expect(calls.filter((c) => c.key === `GET ${BASE}`).length).toBeGreaterThan(1);
  });

  it("sends a cleared optional field as empty, and a changed note and payout preference", async () => {
    const calls = mockApi(routes({ [`PUT ${BASE}/profile`]: [ok(PROFILE)] }));
    mount("business");
    await screen.findByLabelText(/^Website/);
    change(/^Website/, "");
    change(/^Internal notes/, "Call first.");
    change(/^Payout frequency/, "quarterly");
    change(/^Payout contact email/, "");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    await screen.findByText("Business details saved.");
    const body = putCalls(calls)[0].body as ProfileBody;
    expect(body.business?.website).toBe("");
    expect(body.internal_notes).toBe("Call first.");
    expect(body.payout).toEqual({ frequency: "quarterly", contact_email: "" });
  });

  it("sends a chosen time zone", async () => {
    const calls = mockApi(routes({ [`PUT ${BASE}/profile`]: [ok(PROFILE)] }));
    mount("business");
    await screen.findByLabelText(/^Website/);
    const zone = screen.getByLabelText("Time zone (optional)");
    expect(within(zone).getByRole("option", { name: "Not set" })).toHaveValue("");
    change("Time zone (optional)", "Asia/Kolkata");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    await screen.findByText("Business details saved.");
    expect((putCalls(calls)[0].body as ProfileBody).business?.timezone).toBe("Asia/Kolkata");
  });

  it("offers the preferred currency while the partner is a draft, and sends the one chosen", async () => {
    const calls = mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })],
        [`GET ${BASE}/summary`]: [ok(DRAFT_READY)],
        [`PUT ${BASE}/profile`]: [ok(PROFILE)],
      }),
    );
    mount("business");
    const currency = await screen.findByLabelText("Preferred currency");
    expect(currency).toHaveValue("GBP");
    expect(screen.queryByText(/^Paid in/)).not.toBeInTheDocument();
    change("Preferred currency", "EUR");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    await screen.findByText("Business details saved.");
    expect((putCalls(calls)[0].body as ProfileBody).payout).toEqual({ frequency: "monthly", contact_email: "finance@acme.example", currency: "EUR" });
  });

  it("will not send a form with the required details missing, and says which", async () => {
    const calls = mockApi(routes());
    mount("business");
    await screen.findByLabelText(/^Website/);
    change(/^Business email/, "");
    change(/^Country/, "");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));

    expect(await screen.findByText("Business email: Enter the business email.")).toBeInTheDocument();
    expect(screen.getByText("Country: Choose a country.")).toBeInTheDocument();
    const summary = screen.getByText(/Fix these 2 things before saving/).closest('[role="alert"]');
    expect(summary).toHaveFocus();
    const email = screen.getByLabelText(/^Business email/);
    expect(email).toBeInvalid();
    expect(email).toHaveAccessibleDescription("Enter the business email.");
    expect(putCalls(calls)).toEqual([]);
  });

  it("refuses an address that is plainly not one before asking the server", async () => {
    const calls = mockApi(routes());
    mount("business");
    await screen.findByLabelText(/^Website/);
    change(/^Business email/, "not-an-email");
    change(/^Website/, "http://insecure.example");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter a plain https:// address.")).toBeInTheDocument();
    expect(putCalls(calls)).toEqual([]);
  });

  it("puts a problem the server names on the field it names, and keeps what was typed", async () => {
    const calls = mockApi(
      routes({
        [`PUT ${BASE}/profile`]: [
          { status: 400, body: { code: "partner_validation_error", detail: "Use only letters, digits, spaces and hyphens.", field: "business.address.postal_code" } },
        ],
      }),
    );
    mount("business");
    await screen.findByLabelText(/^Website/);
    change("Postal or ZIP code", "N1 1AA!");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));

    expect(await screen.findByText("Use only letters, digits, spaces and hyphens.")).toBeInTheDocument();
    const postal = screen.getByLabelText("Postal or ZIP code");
    expect(postal).toBeInvalid();
    expect(postal).toHaveAccessibleDescription("Use only letters, digits, spaces and hyphens.");
    expect(postal).toHaveValue("N1 1AA!");
    expect(screen.getByText("Postal or ZIP code: Use only letters, digits, spaces and hyphens.")).toBeInTheDocument();
    expect(screen.getByText(/Nothing was saved/)).toBeInTheDocument();
    expect(screen.queryByText("Business details saved.")).not.toBeInTheDocument();
    expect(putCalls(calls)).toHaveLength(1);
  });

  it("says plainly that someone else changed the partner, and reloads on request", async () => {
    const newer = { ...PROFILE, version: 4, business: { ...PROFILE.business, phone: "+44 20 7946 9999" } };
    const calls = mockApi(
      routes({
        // The Profile tab's two editors each read the profile; both re-read it on Reload.
        [`GET ${BASE}/profile`]: [ok(PROFILE), ok(PROFILE), ok(newer)],
        [`PUT ${BASE}/profile`]: [{ status: 409, body: { code: "concurrent_modification", detail: "Partner was at version 4, not 3. Re-read it and retry." } }],
      }),
    );
    mount("business");
    await screen.findByLabelText(/^Website/);
    change(/^Website/, "https://www.acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));

    const alert = await screen.findByText(/Someone else changed this partner while you were editing/);
    expect(alert).toHaveTextContent(/nothing was saved/i);
    expect(document.body.textContent).not.toMatch(/version 4/);
    const reload = screen.getByRole("button", { name: "Reload" });

    fireEvent.click(reload);
    await screen.findByText(/Reloaded\. This is what is saved now\./);
    await waitFor(() => expect(screen.getByLabelText(/^Business phone/)).toHaveValue("+44 20 7946 9999"));
    // The unsaved edit was replaced by what is saved, and the Reload button is gone.
    expect(screen.getByLabelText(/^Website/)).toHaveValue("https://acme.example");
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key === `GET ${BASE}/profile`)).toHaveLength(4);
  });

  it("resumes the same save after a second-factor prompt", async () => {
    const calls = mockApi(
      routes({
        [`PUT ${BASE}/profile`]: [STEP_UP, ok(PROFILE)],
        "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
      }),
    );
    mount("business");
    await screen.findByLabelText(/^Website/);
    change(/^Website/, "https://www.acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Save business details" }));
    const code = await screen.findByLabelText(/needs a recent verification/);
    expect(screen.queryByText("Business details saved.")).not.toBeInTheDocument();
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Business details saved.");
    const puts = putCalls(calls);
    expect(puts).toHaveLength(2);
    expect(puts[1].body).toEqual(puts[0].body);
  });

  it("is read-only without the manage permission: the values as text, no inputs", async () => {
    const calls = mockApi(routes());
    const { container } = mount("business", VIEW_ONLY);
    expect(await screen.findByText("hello@acme.example")).toBeInTheDocument();
    expect(screen.getByText("https://acme.example")).toBeInTheDocument();
    expect(screen.getByText("United Kingdom")).toBeInTheDocument();
    expect(screen.getByText("1 High Street, London, N1 1AA")).toBeInTheDocument();
    expect(screen.getByText("Prefers email.")).toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
    // Business details and contacts each say why they cannot be changed.
    expect(screen.getAllByText(/needs the platform\.staff_partner\.manage permission/)).toHaveLength(2);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);
  });

  it("draws hostile text as text, in the form and in the read-only view", async () => {
    const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;
    const hostile = { ...PROFILE, business: { ...PROFILE.business, display_name: EVIL }, internal_notes: EVIL };
    mockApi(routes({ [`GET ${BASE}/profile`]: [ok(hostile)] }));
    const { container, unmount } = mount("business");
    expect(await screen.findByLabelText(/^Company or partner name/)).toHaveValue(EVIL);
    expect(screen.getByLabelText(/^Internal notes/)).toHaveValue(EVIL);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    unmount();

    mockApi(routes({ [`GET ${BASE}/profile`]: [ok(hostile)] }));
    const view = mount("business", VIEW_ONLY);
    await screen.findByText("hello@acme.example");
    expect(view.container.textContent).toContain("<img");
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

// --- Contacts -----------------------------------------------------------------------------------

describe("Contacts", () => {
  it("shows the four roles, with a role that follows the primary shown as such", async () => {
    mockApi(routes());
    const { container } = mount("contacts");
    expect(await screen.findByLabelText(/^Primary contact name/)).toHaveValue("Priya Shah");
    expect(screen.getByLabelText(/^Primary contact email/)).toHaveValue("priya@acme.example");
    expect(screen.getByLabelText(/^Primary contact job title/)).toHaveValue("Director");
    const billing = screen.getByRole("group", { name: "Billing contact (optional)" });
    expect(within(billing).getByRole("checkbox", { name: "Same as primary contact" })).toBeChecked();
    expect(within(billing).queryByRole("textbox")).not.toBeInTheDocument();
    const technical = screen.getByRole("group", { name: "Technical contact (optional)" });
    expect(within(technical).getByRole("checkbox", { name: "Same as primary contact" })).not.toBeChecked();
    expect(within(technical).getByLabelText(/^Technical contact name/)).toHaveValue("Tom Tech");
    expect(within(screen.getByRole("group", { name: "Support contact (optional)" })).getByRole("checkbox", { name: "Same as primary contact" })).toBeChecked();
    // Required marks are the primary contact's name and email only.
    expect(screen.getByLabelText(/^Primary contact name/)).toBeRequired();
    expect(screen.getByLabelText(/^Primary contact email/)).toBeRequired();
    expect(screen.getByLabelText(/^Primary contact phone/)).not.toBeRequired();
    // What a contact is, and is not.
    expect(screen.getAllByText(/not Workeasy360 accounts and cannot sign in|not an account, and cannot sign in/).length).toBeGreaterThan(0);
    expect(screen.getByText(/never shown on a partner portal or to another partner/)).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("sends only the contacts - never the business details - and a role that follows the primary stores nothing", async () => {
    const calls = mockApi(routes({ [`PUT ${BASE}/profile`]: [ok({ ...PROFILE, version: 4 })] }));
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    change(/^Primary contact name/, "Priya S.");
    fireEvent.click(screen.getByRole("button", { name: "Save contacts" }));
    await screen.findByText("Contacts saved.");

    const body = putCalls(calls)[0].body as ProfileBody;
    expect(Object.keys(body).sort()).toEqual(["contacts", "expected_version"]);
    expect(body.expected_version).toBe(3);
    expect(body.contacts).toEqual({
      primary: { name: "Priya S.", job_title: "Director", email: "priya@acme.example", phone: "" },
      billing: { same_as_primary: true },
      technical: { name: "Tom Tech", job_title: "", email: "tom@acme.example", phone: "+44 20 7946 0001" },
      support: { same_as_primary: true },
    });
  });

  it("makes a role the same as the primary by ticking the box, and a role its own by clearing it", async () => {
    const calls = mockApi(routes({ [`PUT ${BASE}/profile`]: [ok(PROFILE)] }));
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    const technical = screen.getByRole("group", { name: "Technical contact (optional)" });
    fireEvent.click(within(technical).getByRole("checkbox", { name: "Same as primary contact" }));
    expect(within(technical).queryByRole("textbox")).not.toBeInTheDocument();
    const billing = screen.getByRole("group", { name: "Billing contact (optional)" });
    fireEvent.click(within(billing).getByRole("checkbox", { name: "Same as primary contact" }));
    fireEvent.change(within(billing).getByLabelText(/^Billing contact email/), { target: { value: "accounts@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Save contacts" }));
    await screen.findByText("Contacts saved.");
    const body = putCalls(calls)[0].body as ProfileBody;
    expect(body.contacts).toMatchObject({
      technical: { same_as_primary: true },
      billing: { name: "", job_title: "", email: "accounts@acme.example", phone: "" },
      support: { same_as_primary: true },
    });
  });

  it("requires the primary contact's name and email, and refuses a plainly wrong address, before asking", async () => {
    const calls = mockApi(routes());
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    change(/^Primary contact name/, "");
    change(/^Primary contact email/, "");
    const billing = screen.getByRole("group", { name: "Billing contact (optional)" });
    fireEvent.click(within(billing).getByRole("checkbox", { name: "Same as primary contact" }));
    fireEvent.change(within(billing).getByLabelText(/^Billing contact email/), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Save contacts" }));
    expect(await screen.findByText("Primary contact name: Enter the primary contact's name.")).toBeInTheDocument();
    expect(screen.getByText("Primary contact email: Enter the primary contact's email.")).toBeInTheDocument();
    expect(screen.getByText("Billing contact email: Enter a valid email address.")).toBeInTheDocument();
    expect(putCalls(calls)).toEqual([]);
  });

  it("puts a server problem on the contact field it names", async () => {
    mockApi(
      routes({
        [`PUT ${BASE}/profile`]: [{ status: 400, body: { code: "partner_validation_error", detail: "Enter a valid email address.", field: "contacts.technical.email" } }],
      }),
    );
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    const technical = screen.getByRole("group", { name: "Technical contact (optional)" });
    fireEvent.change(within(technical).getByLabelText(/^Technical contact name/), { target: { value: "Tim Tech" } });
    fireEvent.click(screen.getByRole("button", { name: "Save contacts" }));
    const field = await within(technical).findByLabelText(/^Technical contact email/);
    await waitFor(() => expect(field).toHaveAccessibleDescription("Enter a valid email address."));
    expect(field).toBeInvalid();
  });

  it("says plainly when someone else changed the partner", async () => {
    mockApi(
      routes({
        [`PUT ${BASE}/profile`]: [{ status: 409, body: { code: "concurrent_modification", detail: "Partner was at version 9, not 3. Re-read it and retry." } }],
      }),
    );
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    change(/^Primary contact name/, "Someone New");
    fireEvent.click(screen.getByRole("button", { name: "Save contacts" }));
    expect(await screen.findByText(/Someone else changed this partner/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("points to Users & Permissions for people who can sign in", async () => {
    mockApi(routes());
    mount("contacts");
    await screen.findByLabelText(/^Primary contact name/);
    const links = screen.getAllByRole("button", { name: "Users & Permissions" });
    fireEvent.click(links[0]);
    expect(screen.getByRole("tab", { name: "Users & Permissions" })).toHaveAttribute("aria-selected", "true");
  });

  it("is read-only without the manage permission", async () => {
    const calls = mockApi(routes());
    const { container } = mount("contacts", VIEW_ONLY);
    expect(await screen.findByRole("heading", { name: "Billing contact" })).toBeInTheDocument();
    expect(screen.getByText("Tom Tech")).toBeInTheDocument();
    expect(screen.getAllByText("Same as the primary contact.")).toHaveLength(2);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save contacts" })).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);
  });
});

// --- Overview ------------------------------------------------------------------------------------

describe("Overview", () => {
  /** The Overview's completion list (the header's banner has no list). */
  async function completionList() {
    return (await screen.findByRole("heading", { name: "Profile completion" })).closest("section")!;
  }
  function rowOf(section: HTMLElement, label: string) {
    const row = within(section)
      .getAllByRole("listitem")
      .find((li) => li.querySelector("strong")?.firstChild?.textContent === label);
    if (!row) throw new Error(`no completion row ${label}`);
    return row;
  }
  /** The header's completion banner. */
  async function banner(title: "Profile incomplete" | "Profile complete") {
    return (await screen.findByText(title)).closest("section")!;
  }

  it("shows the derived figures as cards", async () => {
    mockApi(routes());
    const { container } = mount();
    const list = await screen.findByRole("list", { name: "Key figures" });
    const card = (label: string) => within(list).getByText(label).closest("li")!;
    expect(card("Customers")).toHaveTextContent("2");
    expect(card("Customers")).toHaveTextContent("1 awaiting owner activation");
    expect(card("Products")).toHaveTextContent("2");
    expect(card("Subscriptions")).toHaveTextContent("1");
    expect(card("Commission")).toHaveTextContent("10%");
    expect(card("Pending payout")).toHaveTextContent("1");
    expect(card("Domain status")).toHaveTextContent("requested");
    await expectNoViolations(container);
  });

  it("lists the profile completion in the server's words, each unfinished section leading to where it is filled in", async () => {
    mockApi(routes({ [`GET ${BASE}/summary`]: [ok(DRAFT_INCOMPLETE)], [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })] }));
    mount();
    const section = await completionList();
    expect(section).toHaveTextContent("43% complete");
    expect(section).toHaveTextContent("3 of 7 required sections");

    const business = rowOf(section, "Business details");
    expect(business).toHaveTextContent("Incomplete");
    expect(business).toHaveTextContent("Add the company's business email.");
    // jsdom drops the space before a visually hidden suffix, so the name is matched loosely.
    expect(within(business).getByRole("link", { name: /^Complete ?Business details$/ })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=business`);

    const products = rowOf(section, "Products");
    expect(products).toHaveTextContent("Complete");
    expect(within(products).queryByRole("link")).not.toBeInTheDocument();
    expect(within(products).queryByRole("button")).not.toBeInTheDocument();

    // Optional sections are listed, marked, and never counted.
    const pricing = rowOf(section, "Pricing");
    expect(pricing).toHaveTextContent("Not required");
    expect(pricing).toHaveTextContent("(optional)");

    // Capabilities and the administrator are dealt with on Users & Permissions.
    const admin = rowOf(section, "Partner administrator");
    expect(within(admin).queryByRole("link")).not.toBeInTheDocument();
    expect(within(admin).getByRole("button").textContent).toMatch(/^Open Users & Permissions/);
    fireEvent.click(within(rowOf(section, "Capabilities")).getByRole("button"));
    expect(screen.getByRole("tab", { name: "Users & Permissions" })).toHaveAttribute("aria-selected", "true");
  });

  it("leads a waiting domain to the domain step, and links no tab that is not on the page", async () => {
    mockApi(routes());
    const first = mount();
    const section = await completionList();
    const domain = rowOf(section, "Domain");
    expect(domain).toHaveTextContent("Waiting");
    expect(within(domain).getByRole("link", { name: /^Complete ?Domain$/ })).toHaveAttribute("href", `/admin/partners/${ID}/profile?step=domain`);
    fireEvent.click(screen.getByRole("button", { name: "Open payouts" }));
    expect(screen.getByRole("tab", { name: "Payouts" })).toHaveAttribute("aria-selected", "true");
    first.unmount();

    // Staff who may not read commission have no Payouts tab, so no button to it -
    // and no profile step to complete.
    mockApi(routes());
    mount("overview", VIEW_ONLY);
    const again = await completionList();
    expect(within(rowOf(again, "Domain")).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open payouts" })).not.toBeInTheDocument();
  });

  it("tells a draft partner what stands in the way of activation, and disables Activate with the reason", async () => {
    const calls = mockApi(routes({ [`GET ${BASE}/summary`]: [ok(DRAFT_INCOMPLETE)], [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })] }));
    const { container } = mount();
    const notice = await banner("Profile incomplete");
    expect(notice).toHaveTextContent("43%");
    expect(within(notice).getByText(/Still needed: Business details, Capabilities, Partner administrator\./)).toBeInTheDocument();
    expect(notice).toHaveTextContent(/stays a draft - it can do nothing - until its profile is complete and it is activated/);
    expect(screen.queryByText("Profile complete")).not.toBeInTheDocument();
    expect(screen.getByText("Setup required")).toBeInTheDocument();

    const activate = await screen.findByRole("button", { name: "Activate" });
    await waitFor(() => expect(activate).toBeDisabled());
    // The reason is on the page beside the button - a disabled button cannot be focused.
    expect(screen.getByText(/^The profile is incomplete, so this partner cannot be activated yet. Still needed:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate" })).toHaveAccessibleDescription(/cannot be activated yet/);
    expect(writes(calls)).toEqual([]);

    // Everything missing is supplied through Complete Your Profile.
    expect(within(notice).getByRole("link", { name: "Complete Your Profile" })).toHaveAttribute("href", `/admin/partners/${ID}/profile`);
    await expectNoViolations(container);
  });

  it("names a missing primary contact among what is still needed", async () => {
    const missing = [{ key: "primary_contact", label: "Primary contact", detail: "Add the primary contact's name and email." }];
    const summary = { ...DRAFT_INCOMPLETE, profile_completion: { ...DRAFT_INCOMPLETE.profile_completion, missing }, activation: { can_activate: false, missing } };
    mockApi(routes({ [`GET ${BASE}/summary`]: [ok(summary)], [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })] }));
    mount();
    const notice = await banner("Profile incomplete");
    expect(within(notice).getByText(/Still needed: Primary contact\./)).toBeInTheDocument();
    expect(within(notice).getByRole("link", { name: "Complete Your Profile" })).toBeInTheDocument();
  });

  it("says a ready draft can be activated, and activation still goes to the server with its reason and version", async () => {
    const calls = mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })],
        [`GET ${BASE}/summary`]: [ok(DRAFT_READY)],
        [`POST ${BASE}/status`]: [ok({ id: ID, status: "active" })],
      }),
    );
    mount();
    const notice = await banner("Profile complete");
    expect(notice).toHaveTextContent(/Everything required is in place\. Activate the partner when you are ready/);
    expect(within(notice).getByRole("link", { name: "Edit profile" })).toHaveAttribute("href", `/admin/partners/${ID}/profile`);
    expect(screen.queryByText("Profile incomplete")).not.toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Activate" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Activate" }));
    fireEvent.change(screen.getByLabelText(/Reason for/), { target: { value: "Setup reviewed and complete" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByText("Partner activated.");
    expect(calls.find((c) => c.key === `POST ${BASE}/status`)!.body).toEqual({ status: "active", reason: "Setup reviewed and complete", expected_version: 3 });
  });

  it("does not point staff who may not manage partners at an Activate button or an editor they do not have", async () => {
    mockApi(routes({ [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })], [`GET ${BASE}/summary`]: [ok(DRAFT_READY)] }));
    mount("overview", VIEW_ONLY);
    const notice = await banner("Profile complete");
    expect(within(notice).queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
  });

  it("does not offer Activate when the completion answer cannot be read, and says why", async () => {
    mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })],
        [`GET ${BASE}/summary`]: [{ status: 500, body: { code: "server_error", detail: "boom" } }],
      }),
    );
    mount();
    await waitFor(() => expect(screen.getByRole("button", { name: "Activate" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Activate" })).toHaveAccessibleDescription(/could not be checked/);
  });

  it("shows no activation guidance for a partner that is already active", async () => {
    mockApi(routes());
    mount();
    const notice = await banner("Profile incomplete");
    expect(within(notice).getByText(/Still needed: Domain\./)).toBeInTheDocument();
    expect(notice).not.toHaveTextContent(/stays a draft/);
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    expect(screen.queryByText(/cannot be activated yet/)).not.toBeInTheDocument();
  });

  it("says how white-label is added to a partner that is not white-label", async () => {
    mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, experience: [{ key: "reseller", label: "Reseller" }] })],
        [`GET ${BASE}/summary`]: [
          ok({ ...SUMMARY, experience: [{ key: "reseller", label: "Reseller" }], white_label: { enabled: false, brand_configured: false, domain_status: "none", domains: { active: 0, verified: 0, awaiting: 0 } } }),
        ],
      }),
    );
    mount();
    const section = (await screen.findByRole("heading", { name: "White-label" })).closest("section")!;
    expect(section).toHaveTextContent("Not a white-label partner. Add the White-label experience under Users & Permissions to give it a brand and a domain.");
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /white-label/i })).not.toBeInTheDocument();
  });
});

// --- Products: the two per-grant choices ------------------------------------------------------

describe("Products: product options", () => {
  const productGroup = (name: string) => screen.getByRole("group", { name });

  it("shows each product's two choices and what the platform's trial is", async () => {
    mockApi(routes());
    const { container } = mount("products");
    const crm = await screen.findByRole("group", { name: "CRM" });
    expect(within(crm).getByRole("checkbox", { name: "Customers may start the free trial" })).toBeChecked();
    expect(within(crm).getByRole("checkbox", { name: "Shown on the partner's portal" })).toBeChecked();
    // The catalog (what the platform's trial is) loads after the groups first render.
    expect(await within(crm).findByText("The platform's free trial for this product lasts 14 days.")).toBeInTheDocument();
    const books = productGroup("Books (books_basic only)");
    expect(within(books).getByRole("checkbox", { name: "Customers may start the free trial" })).not.toBeChecked();
    expect(within(books).getByText(/The platform has no free trial for this product/)).toBeInTheDocument();
    // What the choices do and do not do.
    expect(screen.getByText(/can only narrow the platform's free trial/)).toBeInTheDocument();
    expect(screen.getByText(/never grants or removes access/)).toBeInTheDocument();
    // Prices and commission have tabs of their own now.
    expect(screen.queryByRole("heading", { name: "Prices" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Pricing" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Commission" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("changes one choice at a time through the grant command, and shows the result", async () => {
    const after = {
      ...SUMMARY,
      products: [{ ...SUMMARY.products[0], trial_allowed: false }, SUMMARY.products[1]],
    };
    const calls = mockApi(
      routes({
        [`GET ${BASE}/summary`]: [ok(SUMMARY), ok(after)],
        [`PATCH ${BASE}/product-grants/g1`]: [ok({ id: "g1", trial_allowed: false, portal_visible: true })],
      }),
    );
    mount("products");
    const crm = await screen.findByRole("group", { name: "CRM" });
    const trial = within(crm).getByRole("checkbox", { name: "Customers may start the free trial" });
    fireEvent.click(trial);
    await screen.findByText("Trial choice saved.");
    expect(calls.find((c) => c.key === `PATCH ${BASE}/product-grants/g1`)!.body).toEqual({ trial_allowed: false });
    await waitFor(() => expect(within(productGroup("CRM")).getByRole("checkbox", { name: "Customers may start the free trial" })).not.toBeChecked());
    // The same element is still on screen (not re-created by the reload), so a
    // keyboard user's place is not lost.
    expect(within(productGroup("CRM")).getByRole("checkbox", { name: "Customers may start the free trial" })).toBe(trial);
  });

  it("changes portal visibility on its own", async () => {
    const calls = mockApi(routes({ [`PATCH ${BASE}/product-grants/g2`]: [ok({ id: "g2", trial_allowed: false, portal_visible: false })] }));
    mount("products");
    const books = await screen.findByRole("group", { name: "Books (books_basic only)" });
    fireEvent.click(within(books).getByRole("checkbox", { name: "Shown on the partner's portal" }));
    await screen.findByText("Portal visibility saved.");
    expect(calls.find((c) => c.key === `PATCH ${BASE}/product-grants/g2`)!.body).toEqual({ portal_visible: false });
  });

  it("shows the values, and no checkbox, without the manage permission", async () => {
    const calls = mockApi(routes());
    mount("products", VIEW_ONLY);
    await screen.findByText("Product options");
    expect(await screen.findByText("Books (books_basic only)")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getAllByText("Customers may start the free trial")).toHaveLength(2);
    expect(screen.getAllByText("Yes").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No").length).toBeGreaterThan(0);
    expect(writes(calls)).toEqual([]);
  });

  it("cannot be changed for a terminated partner", async () => {
    mockApi(routes({ [`GET ${BASE}`]: [ok({ ...DETAIL, status: "terminated" })] }));
    mount("products");
    expect(await screen.findByText(/terminated, so its product options can no longer be changed/)).toBeInTheDocument();
    await screen.findByText("Books (books_basic only)");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("says so when there is nothing to choose", async () => {
    mockApi(routes({ [`GET ${BASE}/summary`]: [ok({ ...SUMMARY, products: [] })] }));
    mount("products");
    expect(await screen.findByText(/may not sell any product yet, so there is nothing to choose/)).toBeInTheDocument();
  });
});

// --- Users & Permissions ---------------------------------------------------------------------------------

describe("Users & Permissions", () => {
  const rowOf = (members: HTMLElement, email: string) => within(members).getByRole("rowheader", { name: email }).closest("tr")!;

  it("shows name, role, status, MFA and last activity, with capabilities by their friendly names", async () => {
    mockApi(routes());
    const { container } = mount("users");
    const caps = await screen.findByRole("table", { name: "Granted capabilities" });
    // The catalog of friendly names loads after the table first renders.
    expect(await within(caps).findByText("Create customer organizations")).toBeInTheDocument();
    expect(within(caps).getByText("customer.provision")).toBeInTheDocument();

    const members = await screen.findByRole("table", { name: "Members" });
    expect(within(members).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Name",
      "Email",
      "Role",
      "Status",
      "MFA",
      "Last activity",
      "Actions",
    ]);
    const cells = (email: string) => within(rowOf(members, email)).getAllByRole("cell").map((c) => c.textContent);

    // Active, with a second factor, last seen on a date.
    expect(cells("priya@acme.example").slice(0, 5)).toEqual(["Priya Shah", "Administrator", "Active", "On", new Date("2026-02-01T09:00:00Z").toLocaleString()]);
    // Invited: the date it was sent.
    const ops = cells("ops@acme.example");
    expect(ops[0]).toBe("-");
    expect(ops[1]).toBe("Viewer");
    expect(ops[2]).toBe(`Invitation sent ${new Date("2026-02-10T10:00:00Z").toLocaleString()}`);
    expect(ops[3]).toBe("Off");
    expect(ops[4]).toBe("Never signed in");
    // Named, but the invitation waits for activation.
    expect(cells("later@acme.example")[2]).toBe("Invitation not sent yet (sent when the partner is activated)");
    // Attached before invitations were tracked.
    expect(cells("old@acme.example")[2]).toBe("Invitation pending");
    await expectNoViolations(container);
  });

  it("offers the setup link only to someone who has not chosen a password", async () => {
    mockApi(routes());
    mount("users");
    const members = await screen.findByRole("table", { name: "Members" });
    expect(within(rowOf(members, "priya@acme.example")).queryByRole("button", { name: /setup link/ })).not.toBeInTheDocument();
    expect(within(rowOf(members, "ops@acme.example")).getByRole("button", { name: "Resend setup link to ops@acme.example" })).toBeInTheDocument();
  });

  it("resends a setup link, and says what that does", async () => {
    const calls = mockApi(
      routes({
        [`POST ${BASE}/members/m2/resend-setup`]: [ok({ sent: true })],
        [`POST ${BASE}/members/m3/resend-setup`]: [ok({ sent: true })],
      }),
    );
    mount("users");
    await screen.findByRole("table", { name: "Members" });
    fireEvent.click(screen.getByRole("button", { name: "Resend setup link to ops@acme.example" }));
    await screen.findByText("A new setup link was sent. The old one no longer works.");
    expect(calls.some((c) => c.key === `POST ${BASE}/members/m2/resend-setup`)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Resend setup link to later@acme.example" }));
    await screen.findByText("The setup link was sent now, instead of when the partner is activated.");
  });

  it("says so when the person had already chosen a password and nothing was sent", async () => {
    mockApi(routes({ [`POST ${BASE}/members/m2/resend-setup`]: [ok({ sent: false })] }));
    mount("users");
    await screen.findByRole("table", { name: "Members" });
    fireEvent.click(screen.getByRole("button", { name: "Resend setup link to ops@acme.example" }));
    expect(await screen.findByText("No link was sent: this person has already chosen a password.")).toBeInTheDocument();
  });

  it("invites a person on a draft partner with names and a chosen time, and never asks for a password", async () => {
    const calls = mockApi(
      routes({
        [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })],
        [`POST ${BASE}/administrators`]: [{ status: 201, body: { id: "m9", role: "viewer", invited: false } }],
      }),
    );
    const { container } = mount("users");
    const email = await screen.findByLabelText(/^Email address/);
    expect(email).toBeRequired();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();

    const timing = screen.getByRole("group", { name: "Send the invitation" });
    expect(within(timing).getByRole("radio", { name: "Now" })).toBeChecked();

    fireEvent.change(email, { target: { value: " new@acme.example " } });
    change(/^First name/, "Ada");
    change(/^Last name/, "Lovelace");
    change("Role", "viewer");
    fireEvent.click(within(timing).getByRole("radio", { name: "When the partner is activated" }));
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await screen.findByText("Added. The invitation will be emailed when this partner is activated.");
    expect(calls.find((c) => c.key === `POST ${BASE}/administrators`)!.body).toEqual({
      email: "new@acme.example",
      role: "viewer",
      first_name: "Ada",
      last_name: "Lovelace",
      send_invitation: "on_activation",
    });
    await waitFor(() => expect(screen.getByLabelText(/^Email address/)).toHaveValue(""));
    expect(screen.getByLabelText(/^First name/)).toHaveValue("");
    await expectNoViolations(container);
  });

  it("invites now by default, omitting empty names", async () => {
    const calls = mockApi(routes({ [`GET ${BASE}`]: [ok({ ...DETAIL, status: "draft" })], [`POST ${BASE}/administrators`]: [{ status: 201, body: { id: "m9", role: "admin", invited: true } }] }));
    mount("users");
    fireEvent.change(await screen.findByLabelText(/^Email address/), { target: { value: "new@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await screen.findByText("Added. If this person has no password yet, a link to choose one was emailed.");
    expect(calls.find((c) => c.key === `POST ${BASE}/administrators`)!.body).toEqual({ email: "new@acme.example", role: "admin", send_invitation: "now" });
  });

  it("offers no deferral on a partner that is not a draft - nothing would send it later", async () => {
    const calls = mockApi(routes({ [`POST ${BASE}/administrators`]: [{ status: 201, body: { id: "m9", role: "viewer", invited: true } }] }));
    mount("users");
    fireEvent.change(await screen.findByLabelText(/^Email address/), { target: { value: "new@acme.example" } });
    expect(screen.queryByRole("group", { name: "Send the invitation" })).not.toBeInTheDocument();
    change(/^First name/, "Ann");
    change("Role", "viewer");
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await screen.findByText(/a link to choose one was emailed/);
    expect(calls.find((c) => c.key === `POST ${BASE}/administrators`)!.body).toEqual({ email: "new@acme.example", role: "viewer", first_name: "Ann" });
  });

  it("changes a role after confirmation, with focus in the select and back on the button when cancelled", async () => {
    const calls = mockApi(routes({ [`POST ${BASE}/members/m2/role`]: [ok({ id: "m2", role: "admin" })] }));
    const { container } = mount("users");
    await screen.findByRole("table", { name: "Members" });
    const open = screen.getByRole("button", { name: "Change role for ops@acme.example" });
    fireEvent.click(open);

    const group = screen.getByRole("group", { name: "Change role for ops@acme.example" });
    const select = within(group).getByLabelText("New role for ops@acme.example");
    expect(select).toHaveFocus();
    expect(select).toHaveValue("viewer");
    expect(within(group).getByRole("button", { name: "Confirm role change" })).toBeDisabled();
    await expectNoViolations(container);

    fireEvent.click(within(group).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Change role for ops@acme.example" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change role for ops@acme.example" })).toHaveFocus();
    expect(writes(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Change role for ops@acme.example" }));
    fireEvent.change(screen.getByLabelText("New role for ops@acme.example"), { target: { value: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm role change" }));
    await screen.findByText("Role changed. It takes effect on this person's next request.");
    expect(calls.find((c) => c.key === `POST ${BASE}/members/m2/role`)!.body).toEqual({ role: "admin" });
    expect(screen.queryByRole("group", { name: "Change role for ops@acme.example" })).not.toBeInTheDocument();
  });

  it("shows the server's refusal when the change would leave an active partner with no administrator", async () => {
    const refusal = "An active partner needs at least one administrator. Add another first.";
    mockApi(
      routes({
        [`POST ${BASE}/members/m1/role`]: [{ status: 400, body: { code: "partner_validation_error", detail: refusal, field: "role" } }],
      }),
    );
    mount("users");
    await screen.findByRole("table", { name: "Members" });
    fireEvent.click(screen.getByRole("button", { name: "Change role for priya@acme.example" }));
    fireEvent.change(screen.getByLabelText("New role for priya@acme.example"), { target: { value: "viewer" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm role change" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(refusal);
    // The row still says Administrator: nothing changed.
    const members = screen.getByRole("table", { name: "Members" });
    expect(within(rowOf(members, "priya@acme.example")).getByText("Administrator")).toBeInTheDocument();
  });

  it("asks before removing, says what removal does and does not do, and focuses the safe choice", async () => {
    const calls = mockApi(routes({ [`POST ${BASE}/members/m2/remove`]: [ok({ id: "m2", status: "removed" })] }));
    const { container } = mount("users");
    await screen.findByRole("table", { name: "Members" });
    fireEvent.click(screen.getByRole("button", { name: "Remove user ops@acme.example" }));

    const group = screen.getByRole("group", { name: "Remove ops@acme.example?" });
    expect(within(group).getByText(/ends this person's partner access immediately/)).toBeInTheDocument();
    expect(within(group).getByText(/does not delete their Workeasy360 account/)).toBeInTheDocument();
    const cancel = within(group).getByRole("button", { name: "Cancel" });
    expect(cancel).toHaveFocus();
    expect(cancel).toHaveAccessibleDescription(/does not delete their Workeasy360 account/);
    expect(writes(calls)).toEqual([]);
    await expectNoViolations(container);

    fireEvent.click(cancel);
    expect(screen.getByRole("button", { name: "Remove user ops@acme.example" })).toHaveFocus();
    expect(writes(calls)).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Remove user ops@acme.example" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove access" }));
    await screen.findByText("User removed. Their partner access ended immediately; their Workeasy360 account was not deleted.");
    expect(calls.filter((c) => c.key === `POST ${BASE}/members/m2/remove`)).toHaveLength(1);
  });

  it("takes on no new user for a terminated partner", async () => {
    mockApi(routes({ [`GET ${BASE}`]: [ok({ ...DETAIL, status: "terminated" })] }));
    mount("users");
    expect(await screen.findByText(/terminated, so it takes on no new users/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite" })).not.toBeInTheDocument();
  });

  it("shows the people and no action to staff who may not manage partners", async () => {
    const calls = mockApi(routes());
    mount("users", VIEW_ONLY);
    const members = await screen.findByRole("table", { name: "Members" });
    expect(within(members).getAllByRole("columnheader").map((h) => h.textContent)).not.toContain("Actions");
    expect(within(members).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Email address/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invite" })).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });
});

// --- Activity ---------------------------------------------------------------------------------------------------

describe("Activity", () => {
  const entry = (over: Record<string, unknown> = {}) => ({
    id: "e1",
    occurred_at: "2026-02-01T00:00:00Z",
    action: "partner.profile_updated",
    label: "Business details or contacts changed",
    actor: "staff@example.com",
    actor_kind: "staff",
    target: "business, contacts",
    resource_type: "partners.partner",
    resource_id: "x",
    outcome: "success",
    reason: "",
    ...over,
  });

  it("shows when, who, what, to what and why - in words, with the recorded action kept as secondary text", async () => {
    mockApi(
      routes({
        [`GET ${BASE}/activity`]: [
          ok({
            results: [
              entry(),
              entry({ id: "e2", action: "partner.active", label: "Partner activated", actor: "System", actor_kind: "system", target: "", reason: "Setup reviewed" }),
              // Whatever else the server might send is not drawn.
              entry({ id: "e3", changes: { token: "SECRET-TOKEN" }, resource_id: "RESOURCE-ID-VALUE" }),
            ],
            next_cursor: null,
            has_more: false,
          }),
        ],
      }),
    );
    const { container } = mount("activity");
    const table = await screen.findByRole("table", { name: "Partner activity" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Timestamp", "Actor", "Action", "Target", "Reason"]);

    const rows = within(table).getAllByRole("row").slice(1);
    const cells = (i: number) => within(rows[i]).getAllByRole("cell").map((c) => c.textContent);
    expect(within(rows[0]).getByRole("rowheader")).toHaveTextContent("Business details or contacts changed");
    expect(within(rows[0]).getByRole("rowheader")).toHaveTextContent("partner.profile_updated");
    expect(cells(0)).toEqual([new Date("2026-02-01T00:00:00Z").toLocaleString(), "staff@example.com", "business, contacts", "-"]);
    // No target and no reason read as a dash, and a system action says System.
    expect(cells(1)).toEqual([new Date("2026-02-01T00:00:00Z").toLocaleString(), "System", "-", "Setup reviewed"]);
    expect(within(rows[1]).getByRole("rowheader")).toHaveTextContent("Partner activated");
    expect(table.textContent).not.toMatch(/SECRET-TOKEN|RESOURCE-ID-VALUE|\{|\}/);
    await expectNoViolations(container);
  });

  it("marks an outcome that was not a success", async () => {
    mockApi(routes({ [`GET ${BASE}/activity`]: [ok({ results: [entry({ outcome: "denied" })], next_cursor: null, has_more: false })] }));
    mount("activity");
    const table = await screen.findByRole("table", { name: "Partner activity" });
    expect(within(table).getByText("(denied)")).toBeInTheDocument();
  });
});
