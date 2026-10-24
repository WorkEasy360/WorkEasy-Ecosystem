import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { AllPartnersList, PartnersOverview } from "./PartnerConsole";
import { PartnerCenterLayout } from "./PartnerCenter";
import { allPartnersHref } from "./oldPartnerRoutes";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";

/**
 * The Partners area (ADR-048): one "Partners" entry in the Super Admin sidebar,
 * its own navigation of exactly Dashboard, All Partners and Add Partner, the
 * dashboard and the one partner table. What the backend cannot enforce for the
 * browser is checked here: the type badges are only rendered (never derived),
 * every filter goes to the server, every card leads to the filtered list, and
 * nothing invents money.
 */

let pathname = "/admin/partners";
let query = "";
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => pathname,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  replace.mockReset();
  pathname = "/admin/partners";
  query = "";
});

const VIEW = "platform.staff_partner.view";
const MANAGE = "platform.staff_partner.manage";
const MONEY = "platform.staff_commission.view";

const CARDS = [
  { key: "total_partners", label: "Total partners", count: 4 },
  { key: "active_partners", label: "Active partners", count: 3 },
  { key: "resellers", label: "Resellers", count: 2 },
  { key: "white_label", label: "White-label partners", count: 1 },
  { key: "affiliates", label: "Affiliates", count: 1 },
  { key: "suspended_partners", label: "Suspended partners", count: 1 },
  { key: "profiles_incomplete", label: "Profiles incomplete", count: 2 },
];

const OVERVIEW = {
  as_of: "2026-03-01T00:00:00Z",
  cards: CARDS,
  recent_partners: [
    {
      id: "p9",
      code: "newest",
      display_name: "Newest Partner",
      status: "draft",
      created_at: "2026-02-28T00:00:00Z",
      experience: [{ key: "affiliate", label: "Affiliate" }],
      profile_completion: { percent: 40, complete: false, setup_status: "setup_required" },
    },
  ],
};

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    code: "acme",
    display_name: "Acme Partners",
    version: 3,
    payout_currency: "GBP",
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    experience: [{ key: "reseller", label: "Reseller" }],
    primary_experience: "reseller",
    capability_count: 9,
    capabilities: ["customer.provision", "customer.manage"],
    administrator: { email: "owner@acme.example", name: "Ola Owner", role: "owner", last_login_at: "2026-03-02T00:00:00Z" },
    phone: "+44 20 7946 0000",
    country: "GB",
    customers: 3,
    products: [{ product_code: "crm", plan_code: null }],
    active_subscriptions: 2,
    commission: [{ currency: "GBP", balance_minor: 3554, unpaid_minor: 0, entries: 2 }],
    payouts: { open: 0, latest: null, paid: [] },
    referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
    profile_completion: { percent: 100, complete: true, setup_status: "active" },
    last_activity_at: "2026-03-01T00:00:00Z",
    ...overrides,
  };
}

const LIST = "GET /api/v1/admin/partners/resellers";
const listRoutes = (rows: unknown[], extra: Record<string, Reply[]> = {}) => ({
  [LIST]: [ok({ results: rows, next_cursor: null, has_more: false })],
  ...extra,
});

// --- navigation ------------------------------------------------------------------------------

describe("the Super Admin sidebar", () => {
  async function sidebarLinks(permissions: string[]) {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [ok({ is_staff: true, aal: 1, permissions })],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    return within(screen.getByRole("navigation", { name: /Super Admin/ })).getAllByRole("link");
  }

  it("has one Partners entry", async () => {
    const links = await sidebarLinks([VIEW, MONEY]);
    expect(links.map((a) => a.textContent)).toEqual(["Dashboard", "Partners"]);
    expect(links[1]).toHaveAttribute("href", "/admin/partners");
  });

  it("stays current on a partner's own page", async () => {
    pathname = "/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile";
    const links = await sidebarLinks([VIEW]);
    expect(links.find((a) => a.textContent === "Partners")).toHaveAttribute("aria-current", "page");
  });

  it("is absent for someone who may not view partners", async () => {
    for (const permissions of [["platform.staff_customer.view"], [MONEY]]) {
      const links = await sidebarLinks(permissions);
      expect(links.map((a) => a.textContent)).not.toContain("Partners");
    }
  });
});

describe("the Partners navigation", () => {
  function renderCenter(permissions: string[]) {
    return render(
      <AdminContextForTest permissions={permissions}>
        <PartnerCenterLayout>
          <h1>Body</h1>
        </PartnerCenterLayout>
      </AdminContextForTest>,
    );
  }
  const labels = () =>
    within(screen.getByRole("navigation", { name: "Partner Control Center" }))
      .getAllByRole("link")
      .map((a) => a.textContent);

  it("is exactly Dashboard, All Partners and Add Partner", async () => {
    pathname = "/admin/partners/all";
    const { container } = renderCenter([VIEW, MANAGE, MONEY]);
    expect(labels()).toEqual(["Dashboard", "All Partners", "Add Partner"]);
    for (const gone of ["Resellers", "White-label", "Affiliates", "Customers", "Products & Pricing", "Commissions", "Payouts", "Domains", "Branding"]) {
      expect(labels()).not.toContain(gone);
    }
    expect(screen.getByRole("link", { name: "All Partners" })).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Body"]);
    await expectNoViolations(container);
  });

  it("keeps a partner's page and its Complete Your Profile under All Partners", () => {
    pathname = "/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile";
    renderCenter([VIEW, MANAGE]);
    expect(screen.getByRole("link", { name: "All Partners" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
  });

  it("marks Add Partner on its page and offers it only to staff who may manage partners", () => {
    pathname = "/admin/partners/new";
    renderCenter([VIEW, MANAGE]);
    expect(screen.getByRole("link", { name: "Add Partner" })).toHaveAttribute("aria-current", "page");
  });

  it("hides Add Partner from someone who may only view", () => {
    renderCenter([VIEW]);
    expect(labels()).toEqual(["Dashboard", "All Partners"]);
  });
});

describe("old addresses", () => {
  it("send the lists to All Partners filtered on the type, keeping a search and a valid status", () => {
    expect(allPartnersHref({ q: "acme", status: "active" }, "reseller")).toBe("/admin/partners/all?type=reseller&q=acme&status=active");
    expect(allPartnersHref({ status: "open" }, "white_label")).toBe("/admin/partners/all?type=white_label");
    expect(allPartnersHref({ unpaid: "1" })).toBe("/admin/partners/all");
  });
});

// --- the dashboard -------------------------------------------------------------------------------

describe("the Partner dashboard", () => {
  it("shows the counts, each a link to All Partners filtered to what it counts", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok(OVERVIEW)] });
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MANAGE]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    const total = await screen.findByRole("link", { name: "Total partners: 4. Open the list." });
    expect(total).toHaveAttribute("href", "/admin/partners/all");
    const expected: [string, string][] = [
      ["Active partners: 3. Open the list.", "/admin/partners/all?status=active"],
      ["Resellers: 2. Open the list.", "/admin/partners/all?type=reseller"],
      ["White-label partners: 1. Open the list.", "/admin/partners/all?type=white_label"],
      ["Affiliates: 1. Open the list.", "/admin/partners/all?type=affiliate"],
      ["Suspended partners: 1. Open the list.", "/admin/partners/all?status=suspended"],
      ["Profiles incomplete: 2. Open the list.", "/admin/partners/all?profile=incomplete"],
    ];
    for (const [name, href] of expected) expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    expect(screen.getByRole("link", { name: "+ Add Partner" })).toHaveAttribute("href", "/admin/partners/new");
    await expectNoViolations(container);
  });

  it("lists the recent partners with their type, profile and setup state", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok(OVERVIEW)] });
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Recent partners" });
    const r = within(table).getByRole("row", { name: /Newest Partner/ });
    expect(within(r).getByRole("link", { name: "Newest Partner" })).toHaveAttribute("href", "/admin/partners/p9");
    expect(r).toHaveTextContent("Affiliate");
    expect(r).toHaveTextContent("40%");
    expect(r).toHaveTextContent("Incomplete");
    expect(r).toHaveTextContent("Setup required");
    // Someone who may only view gets no Add Partner button.
    expect(screen.queryByRole("link", { name: "+ Add Partner" })).not.toBeInTheDocument();
  });

  it("invents no revenue, growth or recurring-revenue figure", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok(OVERVIEW)] });
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /Total partners/ });
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\bMRR\b|\bARR\b|growth \d|%\s*growth/i);
    expect(screen.queryByText(/[£$€]\s?\d/)).not.toBeInTheDocument();
  });
});

// --- All Partners --------------------------------------------------------------------------------

function renderList(permissions = [VIEW, MANAGE]) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AllPartnersList />
    </AdminContextForTest>,
  );
}

describe("All Partners", () => {
  it("shows the agency-style columns, the type badges the server derived and the setup state", async () => {
    mockApi(
      listRoutes([
        row(),
        row({
          id: "p2",
          display_name: "Combo Co",
          experience: [
            { key: "reseller", label: "Reseller" },
            { key: "white_label", label: "White-label" },
            { key: "affiliate", label: "Affiliate" },
          ],
          status: "draft",
          administrator: null,
          phone: "",
          country: "",
          commission: [],
          profile_completion: { percent: 33, complete: false, setup_status: "setup_required" },
        }),
      ]),
    );
    const { container } = renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Status",
      "Action",
      "Panel",
      "Partner ID",
      "Name",
      "Email",
      "Phone",
      "Country",
      "Currency",
      "Wallet",
      "Last Login",
      "Joining Date",
    ]);
    const acme = within(table).getByRole("row", { name: /Acme Partners/ });
    expect(within(acme).getByRole("switch", { name: "Acme Partners active" })).toHaveAttribute("aria-checked", "true");
    expect(within(acme).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(acme).getByRole("link", { name: "Edit setup of Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1/profile");
    expect(within(acme).getByRole("button", { name: "Guest login to Acme Partners" })).toBeInTheDocument();
    expect(within(acme).getByRole("button", { name: "Copy panel link of Acme Partners" })).toBeInTheDocument();
    expect(acme).toHaveTextContent(`${window.location.origin}/partner/p1`);
    expect(acme).toHaveTextContent("owner@acme.example");
    expect(acme).toHaveTextContent("+44 20 7946 0000");
    expect(acme).toHaveTextContent("GB");
    expect(acme).toHaveTextContent("35.54");
    expect(acme).toHaveTextContent("active");
    const combo = within(table).getByRole("row", { name: /Combo Co/ });
    const badges = within(combo).getByRole("list", { name: /Partner type/ });
    expect(within(badges).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Reseller", "White-label", "Affiliate"]);
    expect(within(combo).getByRole("link", { name: "Complete setup of Combo Co" })).toHaveAttribute("href", "/admin/partners/p2/profile");
    // A draft nobody can act for is not offered activation (the server refuses it).
    const comboSwitch = within(combo).getByRole("switch", { name: "Combo Co active" });
    expect(comboSwitch).toBeDisabled();
    expect(comboSwitch).toHaveAttribute("title", "Add an administrator before activating this partner.");
    expect(combo).toHaveTextContent("Setup required");
    expect(combo).toHaveTextContent("No administrator");
    await expectNoViolations(container);
  });

  it("copies the value a copy button stands next to", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockApi(listRoutes([row()]));
    renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(table).getByRole("button", { name: "Copy email of Acme Partners" }));
    expect(writeText).toHaveBeenCalledWith("owner@acme.example");
    expect(await within(table).findByRole("button", { name: "Copied email of Acme Partners" })).toBeInTheDocument();
  });

  it("guest login asks why, opens the partner's portal read-only and says so", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, origin: "http://localhost:3000" });
    const calls = mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/guest-view": [
          ok({ partner_id: "p1", reason: "Support ticket 42", expires_at: "2026-03-01T00:30:00Z", portal_path: "/partner/p1" }),
        ],
      }),
    );
    renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(table).getByRole("button", { name: "Guest login to Acme Partners" }));
    const why = within(table).getByLabelText(/Why are you opening the portal of Acme Partners/);
    expect(within(table).getByRole("button", { name: "Open guest view" })).toBeDisabled();
    fireEvent.change(why, { target: { value: "Support ticket 42" } });
    fireEvent.click(within(table).getByRole("button", { name: "Open guest view" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/partner/p1"));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/guest-view")!.body).toEqual({ reason: "Support ticket 42" });
    vi.unstubAllGlobals();
  });

  it("marks the partner Add Partner just created and points to its setup", async () => {
    const id = "11111111-2222-4333-8444-555555555555";
    query = `created=${id}`;
    mockApi(listRoutes([row({ id, display_name: "Fresh Co", status: "draft" })]));
    renderList();
    expect(await screen.findByText("Partner created successfully.")).toBeInTheDocument();
    const banner = screen.getByText("Partner created successfully.").closest("[role=status]") as HTMLElement;
    expect(within(banner).getByRole("link", { name: "Complete setup" })).toHaveAttribute("href", `/admin/partners/${id}/profile`);
  });

  it("ignores a created marker that is not a partner id", async () => {
    query = "created=javascript:alert(1)";
    mockApi(listRoutes([row()]));
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    expect(screen.queryByText("Partner created successfully.")).not.toBeInTheDocument();
  });

  it("sends every filter to the server and keeps them in the address", async () => {
    const calls = mockApi(listRoutes([row()]));
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.change(screen.getByLabelText("Partner type"), { target: { value: "affiliate" } });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("type=affiliate"));
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "suspended" } });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("status=suspended"));
    fireEvent.change(screen.getByLabelText("Profile"), { target: { value: "incomplete" } });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("profile=incomplete"));
    fireEvent.change(screen.getByLabelText("Search by partner name or email"), { target: { value: "owner@acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("q=owner%40acme"));
    const last = new URL(calls.at(-1)!.url, "http://x");
    expect(Object.fromEntries(last.searchParams)).toMatchObject({
      scope: "all",
      type: "affiliate",
      status: "suspended",
      profile: "incomplete",
      q: "owner@acme",
    });
    expect(replace).toHaveBeenLastCalledWith("/admin/partners?q=owner%40acme&type=affiliate&status=suspended&profile=incomplete", { scroll: false });
  });

  it("starts from the filters in the address and ignores values it does not know", async () => {
    query = "type=white_label&status=bogus&profile=complete&q=brand";
    const calls = mockApi(listRoutes([]));
    renderList();
    await screen.findByText("No partners match these filters.");
    const url = new URL(calls[0].url, "http://x");
    expect(url.searchParams.get("type")).toBe("white_label");
    expect(url.searchParams.get("status")).toBeNull();
    expect(url.searchParams.get("profile")).toBe("complete");
    expect(url.searchParams.get("q")).toBe("brand");
    expect(screen.getByLabelText("Partner type")).toHaveValue("white_label");
  });

  it("offers Suspend or Reactivate with a recorded reason, sending the row's version", async () => {
    const calls = mockApi(
      listRoutes([row(), row({ id: "p3", display_name: "Paused Co", status: "suspended", version: 7 })], {
        "POST /api/v1/admin/partners/p3/status": [ok({ id: "p3" })],
      }),
    );
    renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    const paused = within(table).getByRole("row", { name: /Paused Co/ });
    const toggle = within(paused).getByRole("switch", { name: "Paused Co active" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    fireEvent.change(within(table).getByLabelText(/Reason to reactivate Paused Co/), { target: { value: "Review finished, back on" } });
    fireEvent.click(within(table).getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p3/status")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p3/status")!.body).toEqual({
      status: "active",
      reason: "Review finished, back on",
      expected_version: 7,
    });
    expect(await screen.findByText("Paused Co reactivated.")).toBeInTheDocument();
  });

  it("activates a draft with an administrator from its switch, with a recorded reason", async () => {
    const calls = mockApi(
      listRoutes([row({ id: "p5", display_name: "Draft Co", status: "draft", version: 2 })], {
        "POST /api/v1/admin/partners/p5/status": [ok({ id: "p5" })],
      }),
    );
    renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    const toggle = within(table).getByRole("switch", { name: "Draft Co active" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    fireEvent.change(within(table).getByLabelText(/Reason to activate Draft Co/), { target: { value: "Agreement signed today" } });
    fireEvent.click(within(table).getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p5/status")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p5/status")!.body).toEqual({
      status: "active",
      reason: "Agreement signed today",
      expected_version: 2,
    });
    expect(await screen.findByText("Draft Co activated.")).toBeInTheDocument();
  });

  it("offers no lifecycle action to staff who may only view", async () => {
    mockApi(listRoutes([row(), row({ id: "p4", display_name: "Draft Co", status: "draft" })]));
    renderList([VIEW]);
    const table = await screen.findByRole("table", { name: "Partners" });
    for (const toggle of within(table).getAllByRole("switch")) expect(toggle).toBeDisabled();
    expect(within(table).queryByRole("button", { name: /Guest login/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "+ Add Partner" })).not.toBeInTheDocument();
  });

  it("says when nothing matches", async () => {
    mockApi(listRoutes([]));
    renderList();
    expect(await screen.findByText("No partners match these filters.")).toBeInTheDocument();
  });
});
