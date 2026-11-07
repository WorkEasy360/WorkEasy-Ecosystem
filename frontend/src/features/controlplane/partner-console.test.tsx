import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { AllPartnersList, PartnersOverview } from "./PartnerConsole";
import { PartnerCenterLayout } from "./PartnerCenter";
import { allPartnersHref } from "./oldPartnerRoutes";
import { CENTER_ITEMS, centerItems, isCurrent } from "./partnerCenterNav";
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
      "GET /api/v1/admin/session": [ok({ is_staff: true, permissions })],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    return within(screen.getByRole("navigation", { name: /Super Admin/ })).getAllByRole("link");
  }

  it("has one Partners section holding the Partners area's pages - New partner is an action, not a page", async () => {
    await sidebarLinks([VIEW, MANAGE, MONEY]);
    const nav = screen.getByRole("navigation", { name: /Super Admin/ });
    const section = within(nav).getByRole("button", { name: /^Partners/ });
    if (section.getAttribute("aria-expanded") === "false") fireEvent.click(section);
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["Dashboard", "Partner overview", "All partners", "Product requests", "Upgrade fees", "Partner tickets"]);
    expect(links[1]).toHaveAttribute("href", "/admin/partners");
  });

  it("stays current on a partner's own page", async () => {
    pathname = "/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile";
    const links = await sidebarLinks([VIEW]);
    expect(links.find((a) => a.textContent === "All partners")).toHaveAttribute("aria-current", "page");
    expect(links.find((a) => a.textContent === "Partner overview")).not.toHaveAttribute("aria-current");
  });

  it("is absent for someone who may not view partners", async () => {
    for (const permissions of [["platform.staff_customer.view"], [MONEY]]) {
      const links = await sidebarLinks(permissions);
      expect(links.map((a) => a.textContent)).not.toContain("All partners");
      expect(within(screen.getByRole("navigation", { name: /Super Admin/ })).queryByRole("button", { name: /^Partners/ })).toBeNull();
    }
  });
});

describe("the Partners area", () => {
  it("adds no second navigation or banner above a page: the sidebar is the one menu", async () => {
    pathname = "/admin/partners/all";
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MANAGE, MONEY]}>
        <PartnerCenterLayout>
          <h1>Body</h1>
        </PartnerCenterLayout>
      </AdminContextForTest>,
    );
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText("Partner Control Center")).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Body"]);
    await expectNoViolations(container);
  });

  it("keeps a partner's page and its profile under All partners, and New partner too", () => {
    const page = "/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile";
    const all = CENTER_ITEMS.find((i) => i.href === "/admin/partners/all")!;
    expect(isCurrent(page, all)).toBe(true);
    expect(isCurrent(page, CENTER_ITEMS[0])).toBe(false);
    expect(centerItems([VIEW]).map((i) => i.label)).not.toContain("New partner");
  });
});

describe("old addresses", () => {
  it("send the lists to All Partners filtered on the type, keeping a search and a valid status", () => {
    expect(allPartnersHref({ q: "acme", status: "active" }, "reseller")).toBe("/admin/partners/all?type=reseller&q=acme&status=active");
    expect(allPartnersHref({ status: "open" }, "white_label")).toBe("/admin/partners/all?type=white_label");
    expect(allPartnersHref({ unpaid: "1" })).toBe("/admin/partners/all");
  });
});

// --- the overview -------------------------------------------------------------------------------

describe("the Partner overview", () => {
  it("shows the counts, each a card linking to All partners filtered to what it counts", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok(OVERVIEW)] });
    const { container } = render(
      <AdminContextForTest permissions={[VIEW, MANAGE]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Partner overview" })).toBeInTheDocument();
    const total = await screen.findByRole("link", { name: /^Total partners 4/ });
    expect(total).toHaveAttribute("href", "/admin/partners/all");
    const expected: [RegExp, string][] = [
      [/^Active partners 3/, "/admin/partners/all?status=active"],
      [/^Resellers 2/, "/admin/partners/all?type=reseller"],
      [/^White-label partners 1/, "/admin/partners/all?type=white_label"],
      [/^Affiliates 1/, "/admin/partners/all?type=affiliate"],
      [/^Suspended partners 1/, "/admin/partners/all?status=suspended"],
      [/^Profiles incomplete 2/, "/admin/partners/all?profile=incomplete"],
    ];
    for (const [name, href] of expected) expect(screen.getByRole("link", { name })).toHaveAttribute("href", href);
    expect(screen.getByRole("link", { name: "New partner" })).toHaveAttribute("href", "/admin/partners/new");
    // The paragraph about currency conversion is gone.
    expect(screen.queryByText(/currency conversion/)).not.toBeInTheDocument();
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
    // Someone who may only view gets no New partner button.
    expect(screen.queryByRole("link", { name: "New partner" })).not.toBeInTheDocument();
  });

  it("invents no revenue, growth or recurring-revenue figure", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok(OVERVIEW)] });
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    await screen.findByRole("link", { name: /^Total partners/ });
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\bMRR\b|\bARR\b|growth \d|%\s*growth/i);
    expect(screen.queryByText(/[£$€]\s?\d/)).not.toBeInTheDocument();
  });
});

// --- All partners --------------------------------------------------------------------------------

function renderList(permissions = [VIEW, MANAGE]) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AllPartnersList />
    </AdminContextForTest>,
  );
}

/** Open a row's "⋯" menu and return it. */
function openRowMenu(name: string) {
  fireEvent.click(screen.getByRole("button", { name: `Actions for ${name}` }));
  return screen.getByRole("menu", { name: `Actions for ${name}` });
}

/** Choose an option of a styled select. */
function choose(label: string, option: string) {
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: option }));
}

describe("All partners", () => {
  it("shows the partner, type, status, country, currency, wallet, last login and joined columns, with actions in a row menu", async () => {
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
          primary_experience: "white_label",
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
    expect(screen.getByRole("heading", { level: 1, name: "All partners" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Partners" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Partner",
      "Type",
      "Status",
      "Country",
      "Currency",
      "Wallet",
      "Last login",
      "Joined",
      "Actions",
    ]);
    const acme = within(table).getByRole("row", { name: /Acme Partners/ });
    expect(within(acme).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(acme).getByRole("button", { name: "Copy partner ID of Acme Partners" })).toBeInTheDocument();
    expect(acme).toHaveTextContent("owner@acme.example");
    expect(acme).toHaveTextContent("GB");
    expect(acme).toHaveTextContent("35.54");
    expect(acme).toHaveTextContent("Active");
    // No inline switch: the status is a badge.
    expect(within(table).queryByRole("switch")).not.toBeInTheDocument();
    const combo = within(table).getByRole("row", { name: /Combo Co/ });
    // One type, the partner's current workspace - never all it holds.
    const typeCell = (r: HTMLElement) => within(r).getAllByRole("cell")[0];
    expect(typeCell(combo)).toHaveTextContent(/^White-label$/);
    expect(typeCell(acme)).toHaveTextContent(/^Reseller$/);
    expect(combo).not.toHaveTextContent("Affiliate");
    expect(combo).toHaveTextContent("Setup required");
    expect(combo).toHaveTextContent("No administrator");
    await expectNoViolations(container);

    const menu = openRowMenu("Acme Partners");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "View partner",
      "Edit setup",
      "Open partner panel",
      "Copy panel link",
      "Guest login (read-only)",
      "Suspend",
    ]);
    expect(within(menu).getByRole("menuitem", { name: "Edit setup" })).toHaveAttribute("href", "/admin/partners/p1/profile");
    expect(within(menu).getByRole("menuitem", { name: /Open partner panel/ })).toHaveAttribute("href", `${window.location.origin}/partner/p1`);
    fireEvent.keyDown(menu, { key: "Escape" });

    // A draft nobody can act for is not offered activation (the server refuses it).
    const comboMenu = openRowMenu("Combo Co");
    expect(within(comboMenu).getByRole("menuitem", { name: "Complete setup" })).toHaveAttribute("href", "/admin/partners/p2/profile");
    expect(within(comboMenu).getByRole("menuitem", { name: "Activate (add an administrator first)" })).toBeDisabled();
    expect(within(comboMenu).queryByRole("menuitem", { name: "Suspend" })).not.toBeInTheDocument();
  });

  it("copies an ID with the copy button beside it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mockApi(listRoutes([row()]));
    renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(table).getByRole("button", { name: "Copy partner ID of Acme Partners" }));
    expect(writeText).toHaveBeenCalledWith("p1");
    expect(await within(table).findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("guest login opens the partner's portal read-only at once, recording where it came from", async () => {
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
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(openRowMenu("Acme Partners")).getByRole("menuitem", { name: "Guest login (read-only)" }));
    expect(screen.queryByLabelText(/Why are you opening/)).not.toBeInTheDocument();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/partner/p1"));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/guest-view")!.body).toEqual({ reason: "Guest login - All Partners" });
    vi.unstubAllGlobals();
  });

  it("says the partner New partner just created was created and points to its setup", async () => {
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

  it("sends every filter to the server, searches as you type and keeps them in the address", async () => {
    const calls = mockApi(listRoutes([row()]));
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    choose("Partner type", "Affiliate");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("type=affiliate"));
    choose("Status", "Suspended");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("status=suspended"));
    choose("Profile", "Incomplete");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("profile=incomplete"));
    fireEvent.change(screen.getByLabelText("Search by partner name or email"), { target: { value: "owner@acme" } });
    expect(screen.queryByRole("button", { name: "Search" })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(replace).toHaveBeenLastCalledWith("/admin/partners", { scroll: false });
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
    expect(screen.getByRole("combobox", { name: "Partner type" })).toHaveTextContent("White-label");
  });

  it("reactivates from the row menu after a confirmation, recording where it came from and sending the row's version", async () => {
    const calls = mockApi(
      listRoutes([row(), row({ id: "p3", display_name: "Paused Co", status: "suspended", version: 7 })], {
        "POST /api/v1/admin/partners/p3/status": [ok({ id: "p3" })],
      }),
    );
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(openRowMenu("Paused Co")).getByRole("menuitem", { name: "Reactivate" }));
    const dialog = screen.getByRole("dialog", { name: "Reactivate Paused Co?" });
    expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p3/status")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Reactivate partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p3/status")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p3/status")!.body).toEqual({
      status: "active",
      reason: "Reactivated from All Partners",
      expected_version: 7,
    });
    expect(await screen.findByText("Paused Co reactivated.")).toBeInTheDocument();
  });

  it("suspends an active partner after a red confirmation, with the reason written there", async () => {
    const calls = mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/status": [ok({ id: "p1" })],
      }),
    );
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(openRowMenu("Acme Partners")).getByRole("menuitem", { name: "Suspend" }));
    const dialog = screen.getByRole("dialog", { name: "Suspend Acme Partners?" });
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: "Unpaid invoices since March" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Suspend partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p1/status")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/status")!.body as { status: string; reason: string }).toMatchObject({
      status: "suspended",
      reason: "Unpaid invoices since March",
    });
  });

  it("activates a draft with an administrator from its row menu", async () => {
    const calls = mockApi(
      listRoutes([row({ id: "p5", display_name: "Draft Co", status: "draft", version: 2 })], {
        "POST /api/v1/admin/partners/p5/status": [ok({ id: "p5" })],
      }),
    );
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(openRowMenu("Draft Co")).getByRole("menuitem", { name: "Activate" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Activate partner" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p5/status")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p5/status")!.body).toEqual({
      status: "active",
      reason: "Activated from All Partners",
      expected_version: 2,
    });
    expect(await screen.findByText("Draft Co activated.")).toBeInTheDocument();
  });

  it("offers no lifecycle action to staff who may only view", async () => {
    mockApi(listRoutes([row(), row({ id: "p4", display_name: "Draft Co", status: "draft" })]));
    renderList([VIEW]);
    await screen.findByRole("table", { name: "Partners" });
    const menu = openRowMenu("Acme Partners");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["View partner", "Edit setup", "Open partner panel", "Copy panel link"]);
    expect(screen.queryByRole("link", { name: "New partner" })).not.toBeInTheDocument();
  });

  it("says when nothing matches", async () => {
    query = "q=zzz";
    mockApi(listRoutes([]));
    renderList();
    expect(await screen.findByText("No partners match these filters.")).toBeInTheDocument();
  });
});

// --- upgrade requests (ADR-049) -------------------------------------------------------------------

describe("upgrade requests on the Partners screens", () => {
  it("tells the overview how many requests wait for a decision, and says nothing when none do", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok({ ...OVERVIEW, upgrades_pending: 2 })] });
    const first = render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    const notice = (await screen.findByText("2 upgrade requests are waiting for a decision.")).closest("[role=status]") as HTMLElement;
    expect(within(notice).getByRole("link", { name: "Open all partners" })).toHaveAttribute("href", "/admin/partners/all");
    // It sits beside the cards: the card list is unchanged.
    expect(screen.getAllByRole("link", { name: /partners|Resellers|Affiliates|Profiles/ }).filter((a) => a.getAttribute("href")?.startsWith("/admin/partners/all")).length).toBeGreaterThanOrEqual(CARDS.length);
    await expectNoViolations(first.container);
    first.unmount();

    for (const body of [{ ...OVERVIEW, upgrades_pending: 0 }, OVERVIEW]) {
      mockApi({ "GET /api/v1/admin/partners/overview": [ok(body)] });
      const again = render(
        <AdminContextForTest permissions={[VIEW]}>
          <PartnersOverview />
        </AdminContextForTest>,
      );
      await screen.findByRole("link", { name: /^Total partners/ });
      expect(screen.queryByText(/waiting for a decision/)).not.toBeInTheDocument();
      again.unmount();
    }
  });

  it("says it is one request when it is one", async () => {
    mockApi({ "GET /api/v1/admin/partners/overview": [ok({ ...OVERVIEW, upgrades_pending: 1 })] });
    render(
      <AdminContextForTest permissions={[VIEW]}>
        <PartnersOverview />
      </AdminContextForTest>,
    );
    expect(await screen.findByText("1 upgrade request is waiting for a decision.")).toBeInTheDocument();
  });

  it("draws an Upgrade badge on a partner with an open request, linking to where it is decided", async () => {
    mockApi(
      listRoutes([
        row({ upgrade: { id: "r1", to: "white_label", status: "pending_approval" } }),
        row({ id: "p2", display_name: "Quiet Co", upgrade: null }),
        row({ id: "p3", display_name: "Old Style Co" }),
      ]),
    );
    const { container } = renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    const acme = within(table).getByRole("row", { name: /Acme Partners/ });
    const badge = within(acme).getByRole("link", { name: "Upgrade: Pending approval, to White-label - Acme Partners" });
    expect(badge).toHaveTextContent("Upgrade: Pending approval");
    expect(badge).toHaveAttribute("href", "/admin/partners/p1?tab=users");
    expect(within(table).getByRole("row", { name: /Quiet Co/ })).not.toHaveTextContent("Upgrade:");
    expect(within(table).getByRole("row", { name: /Old Style Co/ })).not.toHaveTextContent("Upgrade:");
    await expectNoViolations(container);
  });
});
