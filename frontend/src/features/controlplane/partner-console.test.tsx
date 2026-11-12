import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AdminShell } from "./ControlShell";
import { AllPartnersList, PartnersOverview } from "./PartnerConsole";
import { PartnerCenterLayout } from "./PartnerCenter";
import { allPartnersHref } from "./oldPartnerRoutes";
import { CENTER_ITEMS, centerItems, isCurrent } from "./partnerCenterNav";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";
import { PortalsProvider } from "@/lib/portalContext";

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
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => pathname,
}));

beforeEach(() => {
  vi.restoreAllMocks();
  replace.mockReset();
  push.mockReset();
  pathname = "/admin/partners";
  query = "";
});

const VIEW = "platform.staff_partner.view";
const MANAGE = "platform.staff_partner.manage";
const MONEY = "platform.staff_commission.view";
const GUEST = "platform.staff_partner.guest";
const KYC = "platform.staff_kyc.view";

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

describe("the Admin sidebar", () => {
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
    return within(screen.getByRole("navigation", { name: "Admin Portal" })).getAllByRole("link");
  }

  const SUBMENU = [
    ["Affiliate", "/admin/partners/affiliate"],
    ["Reseller", "/admin/partners/reseller"],
    ["White Label", "/admin/partners/white-label"],
    ["All Partners", "/admin/partners/all"],
  ];
  const submenu = (links: HTMLElement[]) =>
    links.filter((a) => SUBMENU.some(([label]) => a.textContent === label)).map((a) => [a.textContent, a.getAttribute("href")]);

  it("opens Partners into Affiliate, Reseller, White Label and All Partners - its other pages stay tabs inside it", async () => {
    const links = await sidebarLinks([VIEW, MANAGE, MONEY]);
    const nav = screen.getByRole("navigation", { name: "Admin Portal" });
    const partners = within(nav).getByRole("button", { name: "Partners" });
    // On a Partners page (the overview), the section is open and holds it.
    expect(partners).toHaveAttribute("aria-expanded", "true");
    expect(partners).toHaveAttribute("data-contains-current");
    expect(submenu(links)).toEqual(SUBMENU);
    // The module's own tabs are unchanged.
    const tabs = screen.getByRole("navigation", { name: "Approved Partners pages" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["All partners", "Partner overview", "Product requests", "Upgrade fees"]);
  });

  it("marks the type's own page in the menu, and All partners among the tabs", async () => {
    pathname = "/admin/partners/reseller";
    const links = await sidebarLinks([VIEW]);
    const current = links.filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.textContent);
    expect(current).toEqual(["Reseller"]);
    const tabs = screen.getByRole("navigation", { name: "Approved Partners pages" });
    expect(within(tabs).getByRole("link", { name: "All partners" })).toHaveAttribute("aria-current", "page");
  });

  it("lets the staff member reorder the Partners menu with the keyboard, remembered on this browser", async () => {
    window.localStorage.removeItem("w360.admin.menu.partners");
    const links = await sidebarLinks([VIEW]);
    const reseller = links.find((a) => a.textContent === "Reseller")!;
    expect(reseller).toHaveAccessibleDescription(/press Alt with the Up or Down arrow/);
    fireEvent.keyDown(reseller, { key: "ArrowUp", altKey: true });
    const nav = screen.getByRole("navigation", { name: "Admin Portal" });
    await waitFor(() =>
      expect(submenu(within(nav).getAllByRole("link")).map(([label]) => label)).toEqual(["Reseller", "Affiliate", "White Label", "All Partners"]),
    );
    expect(screen.getByText("Reseller moved to position 1 of 4.")).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("w360.admin.menu.partners") ?? "[]")).toEqual([
      "/admin/partners/reseller",
      "/admin/partners/affiliate",
      "/admin/partners/white-label",
      "/admin/partners/all",
    ]);
    // Order only: each entry still opens its own page.
    expect(within(nav).getByRole("link", { name: "Reseller" })).toHaveAttribute("href", "/admin/partners/reseller");
    window.localStorage.removeItem("w360.admin.menu.partners");
  });

  it("reorders the Partners menu by drag and drop", async () => {
    window.localStorage.removeItem("w360.admin.menu.partners");
    const links = await sidebarLinks([VIEW]);
    const item = (label: string) => links.find((a) => a.textContent === label)!.closest("li")!;
    const data = { effectAllowed: "", dropEffect: "", setData: vi.fn() };
    fireEvent.dragStart(item("All Partners"), { dataTransfer: data });
    fireEvent.dragOver(item("Affiliate"), { dataTransfer: data });
    fireEvent.drop(item("Affiliate"), { dataTransfer: data });
    const nav = screen.getByRole("navigation", { name: "Admin Portal" });
    await waitFor(() =>
      expect(submenu(within(nav).getAllByRole("link")).map(([label]) => label)).toEqual(["All Partners", "Affiliate", "Reseller", "White Label"]),
    );
    window.localStorage.removeItem("w360.admin.menu.partners");
  });

  it("stays current on a partner's own page", async () => {
    pathname = "/admin/partners/0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11/profile";
    await sidebarLinks([VIEW]);
    const partners = within(screen.getByRole("navigation", { name: "Admin Portal" })).getByRole("button", { name: "Partners" });
    expect(partners).toHaveAttribute("data-contains-current");
    expect(partners).toHaveAttribute("aria-expanded", "true");
    const tabs = screen.getByRole("navigation", { name: "Approved Partners pages" });
    expect(within(tabs).getByRole("link", { name: "All partners" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Partner overview" })).not.toHaveAttribute("aria-current");
  });

  it("asks for a new password first when the current one is temporary", async () => {
    mockApi({
      "GET /api/v1/auth/session": [ok({ ...(SIGNED_IN.body as object), password_change_required: true })],
      "GET /api/v1/admin/session": [{ status: 403, body: { code: "password_change_required" } }],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    expect(await screen.findByRole("heading", { name: "Choose your own password" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page" })).toBeNull();
  });

  it("is absent for someone who may not view partners or commission rules", async () => {
    const links = await sidebarLinks(["platform.staff_customer.view"]);
    expect(links.map((a) => a.textContent)).not.toContain("Partners");
  });

  it("opens on Commission rules for someone who may only read commissions", async () => {
    const links = await sidebarLinks([MONEY]);
    expect(links.find((a) => a.textContent === "Partners")).toHaveAttribute("href", "/admin/partners/commission-rules");
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
  it("send the type lists to the type's own page, keeping a search and a valid status", () => {
    expect(allPartnersHref({ q: "acme", status: "active" }, "reseller")).toBe("/admin/partners/reseller?q=acme&status=active");
    expect(allPartnersHref({ status: "open" }, "white_label")).toBe("/admin/partners/white-label");
    expect(allPartnersHref({ q: "x" }, "affiliate")).toBe("/admin/partners/affiliate?q=x");
    expect(allPartnersHref({ unpaid: "1" })).toBe("/admin/partners/all");
  });
});

// --- the overview -------------------------------------------------------------------------------

describe("the Partner overview", () => {
  it("shows the counts, each a card linking to the list of what it counts", async () => {
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
      // ADR-062: a type's card opens that type's own page.
      [/^Resellers 2/, "/admin/partners/reseller"],
      [/^White-label partners 1/, "/admin/partners/white-label"],
      [/^Affiliates 1/, "/admin/partners/affiliate"],
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

function renderList(permissions = [VIEW, MANAGE, GUEST, KYC], category?: "affiliate" | "reseller" | "white_label") {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AllPartnersList category={category} />
    </AdminContextForTest>,
  );
}

/** Open a row's "⋯" menu and return it. */
/** ADR-063: Login as Guest asks for the reason first; choose one and start. */
async function confirmGuest(name: string, opts: { reason?: string; details?: string } = {}) {
  const dialog = await screen.findByRole("dialog", { name: `Login as Guest · ${name}` });
  if (opts.reason) fireEvent.change(within(dialog).getByLabelText("Reason"), { target: { value: opts.reason } });
  if (opts.details) fireEvent.change(within(dialog).getByLabelText("Details (optional)"), { target: { value: opts.details } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Start guest session" }));
}

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
  it("shows the specification's columns (ADR-063), with actions in a row menu", async () => {
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
      "Partner Name",
      "Type / Permissions",
      "Account Status",
      "View / Actions",
      "Partner Email",
      "KYC Status",
      "Country",
      "Assigned Customers",
      "Created",
    ]);
    const acme = within(table).getByRole("row", { name: /Acme Partners/ });
    expect(within(acme).getByRole("link", { name: "Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(acme).getByRole("button", { name: "Copy partner ID of Acme Partners" })).toBeInTheDocument();
    expect(acme).toHaveTextContent("owner@acme.example");
    expect(acme).toHaveTextContent("GB");
    // Earnings are no longer in this list (2026-11-10): they stay on the partner's page.
    expect(acme).not.toHaveTextContent("35.54");
    // 2026-11-11: View and Login as Guest side by side, right after the status.
    expect(within(acme).getByRole("link", { name: "View Acme Partners" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(acme).getByRole("button", { name: "Login as Guest to Acme Partners" })).toHaveTextContent(/^Login as Guest$/);
    expect(acme).toHaveTextContent("Active");
    // No inline switch: the status is a badge.
    expect(within(table).queryByRole("switch")).not.toBeInTheDocument();
    const combo = within(table).getByRole("row", { name: /Combo Co/ });
    // One type, the partner's current workspace - never all it holds.
    // The type first, then the other permissions it holds (ADR-063: enabled permissions).
    const typeCell = (r: HTMLElement) => within(r).getAllByRole("cell")[0];
    expect(typeCell(combo)).toHaveTextContent(/^White-label\+ Reseller, Affiliate$/);
    expect(typeCell(acme)).toHaveTextContent(/^Reseller$/);
    expect(combo).toHaveTextContent("Setup required");
    expect(combo).toHaveTextContent("No administrator");
    await expectNoViolations(container);

    const menu = openRowMenu("Acme Partners");
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "View partner",
      "Edit partner",
      "Review KYC",
      "Manage Permissions",
      "View Assigned Customers",
      "View Partner Wallet",
      "View Commission & Payout Records",
      "Open partner panel",
      "Copy panel link",
      "Manage Guest Access",
      "Suspend",
    ]);
    expect(within(menu).getByRole("menuitem", { name: "Review KYC" })).toHaveAttribute("href", "/admin/partners/applications/p1");
    expect(within(menu).getByRole("menuitem", { name: "Edit partner" })).toHaveAttribute("href", "/admin/partners/p1/profile");
    expect(within(menu).getByRole("menuitem", { name: "View Partner Wallet" })).toHaveAttribute(
      "href",
      "/admin/wallets?owner_type=partner&q=Acme%20Partners",
    );
    expect(within(menu).getByRole("menuitem", { name: "View Assigned Customers" })).toHaveAttribute("href", "/admin/partners/p1?tab=customers");
    expect(within(menu).getByRole("menuitem", { name: /Open partner panel/ })).toHaveAttribute("href", `${window.location.origin}/partner/p1`);
    fireEvent.keyDown(menu, { key: "Escape" });

    // A draft nobody can act for is not offered activation (the server refuses it).
    const comboMenu = openRowMenu("Combo Co");
    expect(within(comboMenu).getByRole("menuitem", { name: "Complete setup" })).toHaveAttribute("href", "/admin/partners/p2/profile");
    expect(within(comboMenu).getByRole("menuitem", { name: "Activate (add an administrator first)" })).toBeDisabled();
    expect(within(comboMenu).queryByRole("menuitem", { name: "Suspend" })).not.toBeInTheDocument();
    // Login as Guest is never in the menu: it is the button beside View.
    expect(within(comboMenu).queryByRole("menuitem", { name: /Login as Guest/ })).not.toBeInTheDocument();
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

  it("Login as Guest opens the partner's dashboard at once on a single host, recording where it came from", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, origin: "http://localhost:3000", pathname: "/admin/partners/all" });
    const calls = mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/guest-view": [
          ok({ partner_id: "p1", reason: "Support ticket 42", expires_at: "2026-03-01T00:30:00Z", portal_path: "/partner/reseller/dashboard" }),
        ],
      }),
    );
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(screen.getByRole("button", { name: "Login as Guest to Acme Partners" }));
    // ADR-063: the Admin says why; the reason is recorded with the session.
    await confirmGuest("Acme Partners", { details: "ticket 42" });
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/partner/reseller/dashboard"));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/guest-view")!.body).toEqual({
      reason: "Partner support request: ticket 42",
      return_path: "/admin/partners/all",
    });
    vi.unstubAllGlobals();
  });

  it("Login as Guest posts the one-time ticket to the partner portal in a form, never in a URL", async () => {
    const origins = {
      admin: "https://admin.workeasy360.com",
      partner: "https://partner.workeasy360.com",
      app: "https://app.workeasy360.com",
      auth: "https://auth.workeasy360.com",
      store: "",
    };
    const submitted: { action: string; method: string; fields: Record<string, string> }[] = [];
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(function (this: HTMLFormElement) {
      submitted.push({
        action: this.action,
        method: this.method,
        fields: Object.fromEntries([...this.querySelectorAll("input")].map((i) => [i.name, i.value])),
      });
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, pathname: "/admin/partners/reseller" });
    mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/guest-view": [
          ok({
            partner_id: "p1",
            reason: "x",
            expires_at: "2026-03-01T00:30:00Z",
            portal_path: "/partner/reseller/dashboard",
            entry: { action: "https://partner.workeasy360.com/api/v1/partner/guest-view/enter", ticket: "T".repeat(43) },
          }),
        ],
      }),
    );
    render(
      <PortalsProvider origins={origins}>
        <AdminContextForTest permissions={[VIEW, GUEST]}>
          <AllPartnersList category="reseller" />
        </AdminContextForTest>
      </PortalsProvider>,
    );
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(screen.getByRole("button", { name: "Login as Guest to Acme Partners" }));
    await confirmGuest("Acme Partners");
    await waitFor(() => expect(submitted).toHaveLength(1));
    expect(submitted[0]).toEqual({
      action: "https://partner.workeasy360.com/api/v1/partner/guest-view/enter",
      method: "post",
      fields: { ticket: "T".repeat(43) },
    });
    expect(assign).not.toHaveBeenCalled();
    submit.mockRestore();
    vi.unstubAllGlobals();
  });

  it("refuses to post a ticket anywhere but this deployment's partner portal", async () => {
    const origins = {
      admin: "https://admin.workeasy360.com",
      partner: "https://partner.workeasy360.com",
      app: "https://app.workeasy360.com",
      auth: "https://auth.workeasy360.com",
      store: "",
    };
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
    mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/guest-view": [
          ok({
            partner_id: "p1",
            reason: "x",
            expires_at: "2026-03-01T00:30:00Z",
            portal_path: "/partner/reseller/dashboard",
            entry: { action: "https://evil.example.com/collect", ticket: "T".repeat(43) },
          }),
        ],
      }),
    );
    render(
      <PortalsProvider origins={origins}>
        <AdminContextForTest permissions={[VIEW, GUEST]}>
          <AllPartnersList />
        </AdminContextForTest>
      </PortalsProvider>,
    );
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(screen.getByRole("button", { name: "Login as Guest to Acme Partners" }));
    await confirmGuest("Acme Partners");
    expect(await screen.findByText("Something went wrong. Please try again.")).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    submit.mockRestore();
  });

  it("remembers this list's address for Back to Partners, and restores its filters after a guest session", async () => {
    pathname = "/admin/partners/reseller";
    query = "q=acme&status=active";
    mockApi(listRoutes([row()]));
    const first = renderList([VIEW, MANAGE, GUEST], "reseller");
    await screen.findByRole("table", { name: "Partners" });
    expect(JSON.parse(window.sessionStorage.getItem("w360.admin.partners.list") ?? "null")).toEqual({
      path: "/admin/partners/reseller",
      search: "?q=acme&status=active",
    });
    first.unmount();
    // Back from Return to Super Admin: the server keeps only the path, so the
    // marked address brings the search and filters back from this tab.
    query = "guest=ended";
    const calls = mockApi(listRoutes([row()]));
    renderList([VIEW, MANAGE, GUEST], "reseller");
    await screen.findByRole("table", { name: "Partners" });
    expect(replace).toHaveBeenCalledWith("/admin/partners/reseller?q=acme&status=active", { scroll: false });
    const url = new URL(calls[0].url, "http://x");
    expect(url.searchParams.get("q")).toBe("acme");
    expect(url.searchParams.get("status")).toBe("active");
    expect(screen.getByLabelText("Search by partner name or email")).toHaveValue("acme");
    window.sessionStorage.clear();
  });

  it("a guest return to a list this tab never showed just drops the marker", async () => {
    window.sessionStorage.clear();
    pathname = "/admin/partners/affiliate";
    query = "guest=ended";
    mockApi(listRoutes([row()]));
    renderList([VIEW, MANAGE, GUEST], "affiliate");
    await screen.findByRole("table", { name: "Partners" });
    expect(replace).toHaveBeenCalledWith("/admin/partners/affiliate", { scroll: false });
  });

  it("says when a guest login came back unused or refused", async () => {
    query = "guest=expired";
    mockApi(listRoutes([row()]));
    renderList();
    expect(await screen.findByText(/That guest login link was used or expired/)).toBeInTheDocument();
  });

  it("offers Login as Guest only with its own permission, not with partner management", async () => {
    mockApi(listRoutes([row()]));
    renderList([VIEW, MANAGE]);
    await screen.findByRole("table", { name: "Partners" });
    expect(screen.queryByRole("button", { name: /Login as Guest/ })).not.toBeInTheDocument();
    const menu = openRowMenu("Acme Partners");
    expect(within(menu).queryByRole("menuitem", { name: /Login as Guest/ })).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Manage Guest Access" })).toBeInTheDocument();
  });

  it("Manage Guest Access lists who looked and revokes an active view", async () => {
    const access = {
      minutes: 30,
      may_start: true,
      sessions: [
        {
          id: "g2",
          staff: { id: "u2", name: "Sam Staff", email: "sam@workeasy360.com" },
          reason: "Login as Guest - All Partners",
          started_at: "2026-03-01T00:00:00Z",
          expires_at: "2999-03-01T00:30:00Z",
          entered_at: "2026-03-01T00:00:05Z",
          ended_at: null,
          ended_by: null,
          status: "active",
          mine: false,
        },
        {
          id: "g1",
          staff: { id: "u1", name: "", email: "me@workeasy360.com" },
          reason: "Checking the catalogue",
          started_at: "2026-02-01T00:00:00Z",
          expires_at: "2026-02-01T00:30:00Z",
          entered_at: null,
          ended_at: "2026-02-01T00:10:00Z",
          ended_by: { id: "u1", name: "", email: "me@workeasy360.com" },
          status: "exited",
          mine: true,
        },
      ],
    };
    const revoked = { ...access, sessions: [{ ...access.sessions[0], status: "revoked", ended_at: "2026-03-01T00:05:00Z", ended_by: access.sessions[1].staff }, access.sessions[1]] };
    const calls = mockApi(
      listRoutes([row()], {
        "GET /api/v1/admin/partners/p1/guest-sessions": [ok(access), ok(revoked)],
        "POST /api/v1/admin/partners/p1/guest-sessions/g2/revoke": [ok(revoked)],
      }),
    );
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(within(openRowMenu("Acme Partners")).getByRole("menuitem", { name: "Manage Guest Access" }));
    const dialog = await screen.findByRole("dialog", { name: "Guest access · Acme Partners" });
    const table = await within(dialog).findByRole("table");
    expect(within(table).getByRole("row", { name: /Sam Staff/ })).toHaveTextContent("Active");
    expect(within(table).getByRole("row", { name: /me@workeasy360.com/ })).toHaveTextContent("Exited");
    fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/partners/p1/guest-sessions/g2/revoke")).toBe(true));
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p1/guest-sessions/g2/revoke")!.body).toEqual({
      reason: "Revoke guest access - Manage Guest Access",
    });
    expect(await within(dialog).findByText(/revoked\. It ends on their next request/)).toBeInTheDocument();
    await expectNoViolations(dialog);
  });

  it("puts View and Login as Guest side by side in every row, right after the status", async () => {
    mockApi(
      listRoutes([
        row(),
        row({ id: "p2", display_name: "Second Co", primary_experience: "affiliate", experience: [{ key: "affiliate", label: "Affiliate" }] }),
        row({ id: "p3", display_name: "Draft Co", status: "draft", profile_completion: { percent: 20, complete: false, setup_status: "setup_required" } }),
      ]),
    );
    const { container } = renderList();
    const table = await screen.findByRole("table", { name: "Partners" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).not.toContain("Earnings");
    expect(headers).not.toContain("Guest Login");
    // Fourth, after Name, Type and Status - never at the far end of the row.
    expect(headers.indexOf("View / Actions")).toBe(3);
    expect(headers.indexOf("View / Actions")).toBe(headers.indexOf("Account Status") + 1);
    for (const name of ["Acme Partners", "Second Co", "Draft Co"]) {
      const r = within(table).getByRole("row", { name: new RegExp(name) });
      const cell = within(r).getAllByRole("cell")[2];
      // View, then Login as Guest immediately after it, then the row's other actions.
      const controls = [...cell.querySelectorAll("a, button")].map((el) => el.getAttribute("aria-label") ?? el.textContent);
      expect(controls).toEqual([`View ${name}`, `Login as Guest to ${name}`, `Actions for ${name}`]);
      expect(within(cell).getByRole("link", { name: `View ${name}` })).toHaveTextContent(/^View$/);
    }
    for (const name of ["Acme Partners", "Second Co"]) {
      const button = within(within(table).getByRole("row", { name: new RegExp(name) })).getByRole("button", { name: `Login as Guest to ${name}` });
      expect(button).toHaveTextContent(/^Login as Guest$/);
      expect(button).toBeEnabled();
      expect(button).not.toHaveAttribute("aria-disabled");
    }
    // A draft has no dashboard yet: the button is there, says why, and does nothing.
    const draft = within(within(table).getByRole("row", { name: /Draft Co/ })).getByRole("button", { name: "Login as Guest to Draft Co" });
    expect(draft).toHaveAttribute("aria-disabled", "true");
    expect(draft).toHaveAccessibleDescription("Opens once the partner is active");
    fireEvent.click(draft);
    expect(screen.queryByRole("dialog", { name: /^Login as Guest · / })).not.toBeInTheDocument();
    // No money in the list at all.
    expect(table).not.toHaveTextContent("₹");
    expect(table).not.toHaveTextContent("£");
    await expectNoViolations(container);
  });

  it("shows the same View and Login as Guest pair on each partner type's own list", async () => {
    for (const category of ["affiliate", "reseller", "white_label"] as const) {
      pathname = `/admin/partners/${category === "white_label" ? "white-label" : category}`;
      mockApi(listRoutes([row()]));
      const view = renderList([VIEW, MANAGE, GUEST], category);
      const table = await screen.findByRole("table", { name: "Partners" });
      const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
      expect(headers.slice(0, 4)).toEqual(["Partner Name", "Type / Permissions", "Account Status", "View / Actions"]);
      const cell = within(within(table).getByRole("row", { name: /Acme Partners/ })).getAllByRole("cell")[2];
      expect(within(cell).getByRole("link", { name: "View Acme Partners" })).toBeInTheDocument();
      expect(within(cell).getByRole("button", { name: "Login as Guest to Acme Partners" })).toBeEnabled();
      view.unmount();
    }
  });

  it("the Login button asks why, then opens that partner's guest view through the real endpoint", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, pathname: "/admin/partners/all" });
    const calls = mockApi(
      listRoutes([row(), row({ id: "p2", display_name: "Second Co" })], {
        "POST /api/v1/admin/partners/p2/guest-view": [
          ok({ partner_id: "p2", reason: "x", expires_at: "2026-03-01T00:30:00Z", portal_path: "/partner/reseller/dashboard" }),
        ],
      }),
    );
    renderList();
    const button = await screen.findByRole("button", { name: "Login as Guest to Second Co" });
    fireEvent.click(button);
    // The other rows' buttons wait while the dialog is open.
    expect(screen.getByRole("button", { name: "Login as Guest to Acme Partners" })).toBeDisabled();
    await confirmGuest("Second Co", { reason: "Investigating a reported problem" });
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/partner/reseller/dashboard"));
    expect(calls.filter((c) => c.key.endsWith("/guest-view")).map((c) => c.key)).toEqual(["POST /api/v1/admin/partners/p2/guest-view"]);
    expect(calls.find((c) => c.key === "POST /api/v1/admin/partners/p2/guest-view")!.body).toEqual({
      reason: "Investigating a reported problem",
      return_path: "/admin/partners/all",
    });
    vi.unstubAllGlobals();
  });

  it("says why when the server refuses, and the button can be used again", async () => {
    mockApi(
      listRoutes([row()], {
        "POST /api/v1/admin/partners/p1/guest-view": [
          { status: 403, body: { code: "permission_denied", detail: "Insufficient permission: platform.staff_partner.guest" } },
        ],
      }),
    );
    renderList();
    const button = await screen.findByRole("button", { name: "Login as Guest to Acme Partners" });
    fireEvent.click(button);
    await confirmGuest("Acme Partners");
    expect(await screen.findByText("Insufficient permission: platform.staff_partner.guest")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Login as Guest · Acme Partners" })).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("shows View but no Login as Guest to staff who may not start a guest view", async () => {
    mockApi(listRoutes([row()]));
    renderList([VIEW, MANAGE]);
    const table = await screen.findByRole("table", { name: "Partners" });
    const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).not.toContain("Guest Login");
    expect(headers).not.toContain("Earnings");
    expect(headers).toContain("View / Actions");
    expect(within(table).getByRole("link", { name: "View Acme Partners" })).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /Login as Guest/ })).not.toBeInTheDocument();
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
      status: "suspended",
      profile: "incomplete",
      q: "owner@acme",
    });
    expect(replace).toHaveBeenLastCalledWith("/admin/partners?q=owner%40acme&status=suspended&profile=incomplete", { scroll: false });
    // Partner type is a page of its own (ADR-062), keeping the other filters.
    choose("Partner type", "Affiliate");
    expect(push).toHaveBeenLastCalledWith("/admin/partners/affiliate?q=owner%40acme&status=suspended&profile=incomplete");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(replace).toHaveBeenLastCalledWith("/admin/partners", { scroll: false });
  });

  it("is the same table on a type's own page, listing that type only - the server filters", async () => {
    pathname = "/admin/partners/reseller";
    const calls = mockApi(listRoutes([row()]));
    const { container } = renderList([VIEW, MANAGE, GUEST], "reseller");
    expect(screen.getByRole("heading", { level: 1, name: "Reseller partners" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Partners" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Partner Name",
      "Type / Permissions",
      "Account Status",
      "View / Actions",
      "Partner Email",
      "KYC Status",
      "Country",
      "Assigned Customers",
      "Created",
    ]);
    const url = new URL(calls[0].url, "http://x");
    expect(url.searchParams.get("category")).toBe("reseller");
    expect(url.searchParams.get("type")).toBeNull();
    expect(screen.getByRole("combobox", { name: "Partner type" })).toHaveTextContent("Reseller");
    expect(screen.getByRole("link", { name: "New partner" })).toHaveAttribute("href", "/admin/partners/new?type=reseller");
    // Its other filters work as on All partners and stay on this page.
    choose("Status", "Active");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("status=active"));
    expect(calls.at(-1)!.url).toContain("category=reseller");
    expect(replace).toHaveBeenLastCalledWith("/admin/partners/reseller?status=active", { scroll: false });
    // Another type, or all of them, is another page.
    choose("Partner type", "White-label");
    expect(push).toHaveBeenLastCalledWith("/admin/partners/white-label?status=active");
    choose("Partner type", "All types");
    expect(push).toHaveBeenLastCalledWith("/admin/partners/all?status=active");
    await expectNoViolations(container);
  });

  it("names each type's page and its empty state", async () => {
    for (const [category, title, empty] of [
      ["affiliate", "Affiliate partners", "No affiliate partners yet."],
      ["white_label", "White Label partners", "No white-label partners yet."],
    ] as const) {
      mockApi(listRoutes([]));
      const view = renderList([VIEW], category);
      expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
      expect(await screen.findByText(empty)).toBeInTheDocument();
      view.unmount();
    }
  });

  it("starts from the filters in the address and ignores values it does not know", async () => {
    query = "status=bogus&profile=complete&q=brand";
    const calls = mockApi(listRoutes([]));
    renderList();
    await screen.findByText("No partners match these filters.");
    const url = new URL(calls[0].url, "http://x");
    expect(url.searchParams.get("status")).toBeNull();
    expect(url.searchParams.get("profile")).toBe("complete");
    expect(url.searchParams.get("q")).toBe("brand");
  });

  it("stays on New partner, whose ?type= chooses the form's type (found by the real-browser check)", async () => {
    pathname = "/admin/partners/new";
    query = "type=reseller";
    mockApi(listRoutes([row()]));
    renderList();
    await screen.findByRole("table", { name: "Partners" });
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("opens a type's own page for an old ?type= address, keeping the filters it knows", async () => {
    pathname = "/admin/partners/all";
    query = "type=white_label&status=bogus&profile=complete&q=brand";
    mockApi(listRoutes([]));
    renderList();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/admin/partners/white-label?q=brand&profile=complete"));
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
    expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "View partner",
      "Edit partner",
      "Manage Permissions",
      "View Assigned Customers",
      "View Partner Wallet",
      "View Commission & Payout Records",
      "Open partner panel",
      "Copy panel link",
      // Reading the guest-view history is a view; starting or revoking one is not.
      "Manage Guest Access",
    ]);
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
    expect(screen.getAllByRole("link", { name: /partners|Resellers|Affiliates|Profiles/ }).filter((a) => a.getAttribute("href")?.startsWith("/admin/partners/")).length).toBeGreaterThanOrEqual(CARDS.length);
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
