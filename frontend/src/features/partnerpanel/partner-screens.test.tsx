import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerShell } from "@/features/controlplane/ControlShell";
import { expectNoViolations, mockApi, ok, SIGNED_IN, type Reply } from "@/features/controlplane/testSupport";
import { Roles } from "./Access";
import { Team, Users } from "./Network";
import { AddOns, Payments, ProductManager } from "./Catalog";
import { Appearance, AuthConfiguration, contrastRatio } from "./Account";
import { ApiKeys, ChannelManager, Translations } from "./Tools";
import { Notifications, Support } from "./Help";

let pathname = "/partner/p1";
const nav = vi.hoisted(() => ({ router: { replace: () => {}, push: () => {}, refresh: () => {} } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

const ID = "11111111-1111-1111-1111-111111111111";
const API = `/api/v1/partner/${ID}`;
// Built from pieces so this file holds no literal script element (the panel's architecture test scans every .tsx).
const EVIL = '<img src=x onerror="alert(1)">' + "<scr" + "ipt>alert(2)</scr" + "ipt>";

const ALL = [
  "dashboard.view", "profile.view", "profile.manage", "member.view", "member.manage", "product.view", "product.manage", "activity.view",
  "customer.view", "customer.provision", "customer.export", "pricing.view", "commission.view", "payout.view", "referral.view", "referral.create",
  "subpartner.view", "subpartner.manage", "branding.view", "branding.manage", "domain.view", "domain.manage", "apikey.view", "apikey.manage",
].map((p) => `platform.partner_${p}`);

function portal(permissions: string[] = ALL, status = "active"): Reply {
  return ok({
    partners: [{ partner_id: ID, code: "acme", display_name: "Acme Partners", status, role: "owner", role_label: "Partner Owner", is_admin: true, capabilities: [], permissions }],
  });
}

function shell(children: React.ReactNode) {
  return render(<PartnerShell partnerId={ID}>{children}</PartnerShell>);
}

const BASE: Record<string, Reply[]> = { "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal()] };

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = `/partner/${ID}`;
});

describe("role & permissions", () => {
  it("shows each preset as a subset of what the partner holds, and no custom roles", async () => {
    mockApi({
      ...BASE,
      [`GET ${API}/roles`]: [
        ok({
          roles: [
            { key: "owner", label: "Partner Owner", description: "Everything.", administrator: true, can_change: true, owner_only: true, members: 1, capabilities: [{ key: "customer.provision", label: "Create customers", access: "manage" }] },
            { key: "viewer", label: "Viewer", description: "Reads.", administrator: false, can_change: false, owner_only: false, members: 2, capabilities: [{ key: "customer.provision", label: "Create customers", access: "view" }] },
            { key: "support", label: "Support", description: "Diagnostics.", administrator: false, can_change: true, owner_only: false, members: 0, capabilities: [] },
          ],
          partner_capabilities: [{ key: "customer.provision", label: "Create customers" }],
          account_access: { everyone: ["See the dashboard"], administrators: ["Manage API keys"] },
          can_manage: true,
          not_implemented: { custom_roles: "Roles are the fixed presets." },
        }),
      ],
    });
    const { container } = shell(<Roles />);
    expect(await screen.findByRole("heading", { name: "Role & Permissions" })).toBeInTheDocument();
    const cards = await screen.findByRole("list", { name: "Roles" });
    expect(within(cards).getByText("Partner Owner")).toBeInTheDocument();
    expect(within(cards).getByText("Only an owner can give or change this role.")).toBeInTheDocument();
    expect(within(cards).getByText("Nothing your account holds applies to this role.")).toBeInTheDocument();
    expect(screen.getByText("Roles are the fixed presets.")).toBeInTheDocument();
    // It explains; it never edits.
    expect(within(screen.getByRole("main")).queryByRole("button")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("channel manager", () => {
  it("says which channel is ready and calls the others not available, never connected", async () => {
    mockApi({
      ...BASE,
      [`GET ${API}/channels`]: [
        ok({
          channels: [
            { key: "email", label: "Email", state: "needs_setup", detail: "Name the sender.", editable: true },
            { key: "whatsapp", label: "WhatsApp", state: "not_available", detail: "No provider exists for this channel on the platform.", editable: false },
          ],
          states: [
            { key: "connected", label: "Connected", meaning: "Messages can be sent." },
            { key: "error", label: "Error", meaning: "The last use failed." },
          ],
          not_implemented: { whatsapp_sms_webhook: "No WhatsApp, SMS or partner webhook channel exists on the platform." },
        }),
      ],
    });
    const { container } = shell(<ChannelManager />);
    const table = await screen.findByRole("table", { name: "Channels" });
    expect(within(table).getByText("needs setup")).toBeInTheDocument();
    expect(within(table).getByText("not available")).toBeInTheDocument();
    expect(within(table).queryByText("connected")).not.toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Set the sender" })).toHaveAttribute("href", `/partner/${ID}/brand-settings`);
    await expectNoViolations(container);
  });
});

describe("API key manager", () => {
  const KEY = {
    id: "k1",
    name: "Reporting",
    key_id: "w360pk_abcdef012345",
    scopes: [{ key: "customers.read", label: "Read customers" }],
    status: "active",
    created_at: "2026-10-01T00:00:00Z",
    created_by: "owner@acme.example.com",
    expires_at: "2027-01-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    rotated_from: false,
  };
  const OVERVIEW = {
    keys: [KEY],
    scopes: [
      { key: "account.read", label: "Read the account", description: "Name and status." },
      { key: "customers.read", label: "Read customers", description: "The customer list." },
    ],
    expiry_days: [30, 90, 180, 365],
    default_expiry_days: 90,
    can_manage: true,
    limit: 20,
    never_issued: ["Cloud or hosting credentials", "Workeasy360 master secrets or signing keys", "Database credentials"],
  };
  const SECRET = "w360pk_0123456789ab_the-one-time-secret-value";

  it("lists a key by its public id only, with name, scope, status, created, last used and expiry", async () => {
    mockApi({ ...BASE, [`GET ${API}/api-keys`]: [ok(OVERVIEW)] });
    const { container } = shell(<ApiKeys />);
    const table = await screen.findByRole("table", { name: "Your API keys" });
    expect(within(table).getByText("Reporting")).toBeInTheDocument();
    expect(within(table).getByText("w360pk_abcdef012345…")).toBeInTheDocument();
    expect(within(table).getByText("Read customers")).toBeInTheDocument();
    expect(within(table).getByText("Never")).toBeInTheDocument();
    expect(screen.getByText("Cloud or hosting credentials")).toBeInTheDocument();
    expect(container.textContent).not.toContain(SECRET);
    await expectNoViolations(container);
  });

  it("creates a key, shows the secret once, and never again after it is dismissed", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/api-keys`]: [ok(OVERVIEW)],
      [`POST ${API}/api-keys`]: [{ status: 201, body: { key: { ...KEY, id: "k2", name: "Sync" }, secret: SECRET } }],
    });
    shell(<ApiKeys />);
    await screen.findByRole("table", { name: "Your API keys" });
    fireEvent.change(screen.getByLabelText("Key name"), { target: { value: "Sync" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Read customers/ }));
    fireEvent.change(screen.getByLabelText("Expires after"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByTestId("new-secret")).toHaveTextContent(SECRET);
    const create = calls.find((c) => c.key === `POST ${API}/api-keys`)!;
    // The browser names a label, scopes it picked from a list and an expiry from a list: nothing else.
    expect(create.body).toEqual({ name: "Sync", scopes: ["customers.read"], expires_in_days: 30 });
    fireEvent.click(screen.getByRole("button", { name: "I have stored it" }));
    expect(screen.queryByTestId("new-secret")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("asks before rotating or revoking, and shows a rotated key's new secret once", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/api-keys`]: [ok(OVERVIEW)],
      [`POST ${API}/api-keys/k1/rotate`]: [ok({ key: { ...KEY, id: "k3" }, secret: SECRET })],
      [`POST ${API}/api-keys/k1/revoke`]: [ok({ key: { ...KEY, status: "revoked" } })],
    });
    shell(<ApiKeys />);
    await screen.findByRole("table", { name: "Your API keys" });
    fireEvent.click(screen.getByRole("button", { name: "Rotate Reporting" }));
    expect(calls.some((c) => c.key.endsWith("/rotate"))).toBe(false); // not yet: it asks first
    expect(screen.getByText("The current secret stops working at once.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm rotate" }));
    expect(await screen.findByTestId("new-secret")).toHaveTextContent(SECRET);
    expect(calls.find((c) => c.key.endsWith("/rotate"))!.body).toEqual({ expires_in_days: 90 });
    fireEvent.click(screen.getByRole("button", { name: "I have stored it" }));
    fireEvent.click(screen.getByRole("button", { name: "Revoke Reporting" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(calls.some((c) => c.key.endsWith("/revoke"))).toBe(true));
  });

  it("offers no create, rotate or revoke to someone who cannot manage keys", async () => {
    mockApi({ ...BASE, [`GET ${API}/api-keys`]: [ok({ ...OVERVIEW, can_manage: false })] });
    shell(<ApiKeys />);
    await screen.findByRole("table", { name: "Your API keys" });
    expect(screen.queryByRole("button", { name: "Create key" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rotate|Revoke/ })).not.toBeInTheDocument();
  });

  it("renders hostile key names as text", async () => {
    mockApi({ ...BASE, [`GET ${API}/api-keys`]: [ok({ ...OVERVIEW, keys: [{ ...KEY, name: EVIL }] })] });
    const { container } = shell(<ApiKeys />);
    await screen.findByRole("table", { name: "Your API keys" });
    expect(container.querySelector("img:not([data-w360-logo])")).toBeNull(); // the shell's own logo aside
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(EVIL)).toBeInTheDocument();
  });
});

describe("translation manager", () => {
  const STRINGS = [
    { key: "portal.welcome_title", group: "Welcome", label: "Welcome heading", max_length: 80, value: "", effective: "Welcome", source: "english", english: "Welcome" },
    { key: "portal.support.heading", group: "Support text", label: "Support heading", max_length: 80, value: "Aide ?", effective: "Aide ?", source: "partner", english: "Need help?" },
  ];
  const overview = (locale: string, strings = STRINGS) => ({
    locale,
    locales: [
      { code: "en", name: "English", translated: 0 },
      { code: "fr", name: "French", translated: 1 },
    ],
    strings,
    can_manage: true,
    fallback: ["Your translation for this language", "Workeasy360's wording for this language", "English"],
    never_translatable: ["Security messages and sign-in errors"],
  });

  it("saves only what changed, by key, and withdraws a string by emptying it", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/translations`]: [ok(overview("en"))],
      [`PUT ${API}/translations`]: [ok(overview("en"))],
    });
    const { container } = shell(<Translations />);
    fireEvent.change(await screen.findByLabelText("Welcome heading"), { target: { value: "Bienvenue" } });
    fireEvent.change(screen.getByLabelText("Support heading"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save translations" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/translations`)).toBe(true));
    expect(calls.find((c) => c.key === `PUT ${API}/translations`)!.body).toEqual({
      locale: "en",
      strings: { "portal.welcome_title": "Bienvenue", "portal.support.heading": "" },
    });
    await expectNoViolations(container);
  });

  it("says where each string comes from and what can never be translated", async () => {
    mockApi({ ...BASE, [`GET ${API}/translations`]: [ok(overview("en"))] });
    shell(<Translations />);
    expect(await screen.findByText(/Shown now: Welcome \(English\)/)).toBeInTheDocument();
    expect(screen.getByText(/Shown now: Aide \? \(your wording\)/)).toBeInTheDocument();
    expect(screen.getByText("Security messages and sign-in errors")).toBeInTheDocument();
  });

  it("limits the length of every field to what the server allows", async () => {
    mockApi({ ...BASE, [`GET ${API}/translations`]: [ok(overview("en"))] });
    shell(<Translations />);
    expect(await screen.findByLabelText("Welcome heading")).toHaveAttribute("maxlength", "80");
  });
});

describe("support manager", () => {
  const SUPPORT = {
    contact: { email: "partners@workeasy360.example", available: true },
    help_desk: { name: "Easy Help Desk", available: true, url: "https://help.workeasy360.com/", suspended: false },
    partner: { name: "Acme Partners" },
    priorities: ["low", "normal", "high", "urgent"],
    requests: null,
    not_implemented: { partner_tickets: "Workeasy360 has no ticket system of its own." },
  };

  it("points at the contact address and Easy Help Desk, and does not list requests it does not have", async () => {
    mockApi({ ...BASE, [`GET ${API}/support`]: [ok(SUPPORT)] });
    const { container } = shell(<Support />);
    const desk = await screen.findByRole("link", { name: "Open Easy Help Desk" });
    expect(desk).toHaveAttribute("href", "https://help.workeasy360.com/");
    expect(desk).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "partners@workeasy360.example" })).toHaveAttribute("href", "mailto:partners@workeasy360.example");
    expect(screen.getByText("Workeasy360 has no ticket system of its own.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("prepares an email in the person's own mail app and sends nothing itself", async () => {
    const calls = mockApi({ ...BASE, [`GET ${API}/support`]: [ok(SUPPORT)] });
    shell(<Support />);
    fireEvent.change(await screen.findByLabelText("Subject"), { target: { value: "Payout question" } });
    fireEvent.change(screen.getByLabelText("What do you need help with?"), { target: { value: "Where is my payout?" } });
    expect(screen.getByRole("button", { name: "Prepare email" })).toBeEnabled();
    // Nothing but the page's own reads went to the server.
    expect(calls.every((c) => c.key.startsWith("GET"))).toBe(true);
  });

  it("refuses to link an address that is not https", async () => {
    mockApi({ ...BASE, [`GET ${API}/support`]: [ok({ ...SUPPORT, help_desk: { ...SUPPORT.help_desk, url: "javascript:alert(1)" } })] });
    shell(<Support />);
    await screen.findByRole("heading", { name: "Support Manager" });
    await screen.findByRole("heading", { name: "Easy Help Desk" });
    expect(screen.queryByRole("link", { name: "Open Easy Help Desk" })).not.toBeInTheDocument();
  });
});

describe("notification center", () => {
  const ITEMS = [
    { id: "e1", occurred_at: "2026-10-16T09:00:00Z", title: "Commission generated", category: "Commission", important: false, detail: "", read: false },
    { id: "e2", occurred_at: "2026-10-15T09:00:00Z", title: "Domain disabled", category: "Domains", important: true, detail: "shop.acme.example", read: false },
    { id: "e3", occurred_at: "2026-10-14T09:00:00Z", title: "Payout paid", category: "Payouts", important: true, detail: "", read: true },
  ];
  const response = (view: string, items = ITEMS) => ({
    view,
    items,
    counts: { all: 3, unread: 2, important: 2, important_unread: 1 },
    window: 100,
    truncated: false,
    not_implemented: { customer_trial_ending: "A customer's trial ending is not recorded against the partner." },
  });

  it("shows unread first, marks one read and all read, and explains what is not recorded", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/notifications`]: [ok(response("unread", ITEMS.filter((i) => !i.read)))],
      [`POST ${API}/notifications/read`]: [ok(response("unread", []))],
    });
    const { container } = shell(<Notifications />);
    const list = await screen.findByRole("list", { name: "Notifications" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Unread (2)" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Mark read: Commission generated" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/notifications/read`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${API}/notifications/read`)!.body).toEqual({ ids: ["e1"] });
    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(calls.filter((c) => c.key === `POST ${API}/notifications/read`)).toHaveLength(2));
    expect(calls.filter((c) => c.key === `POST ${API}/notifications/read`)[1]!.body).toEqual({ all: true });
    expect(await screen.findByText("A customer's trial ending is not recorded against the partner.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("can show everything and only the important ones", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/notifications`]: [ok(response("unread", ITEMS.filter((i) => !i.read))), ok(response("important", ITEMS.filter((i) => i.important)))],
    });
    shell(<Notifications />);
    await screen.findByRole("list", { name: "Notifications" });
    fireEvent.click(screen.getByRole("button", { name: /^Important/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("view=important"))).toBe(true));
  });

  it("renders hostile event details as text", async () => {
    mockApi({ ...BASE, [`GET ${API}/notifications`]: [ok(response("unread", [{ ...ITEMS[0]!, detail: EVIL }]))] });
    const { container } = shell(<Notifications />);
    await screen.findByRole("list", { name: "Notifications" });
    expect(container.querySelector("img:not([data-w360-logo])")).toBeNull(); // the shell's own logo aside
    expect(container.querySelector("script")).toBeNull();
  });

  it("is an honest empty state when nothing is waiting", async () => {
    mockApi({ ...BASE, [`GET ${API}/notifications`]: [ok({ ...response("unread", []), counts: { all: 0, unread: 0, important: 0, important_unread: 0 } })] });
    shell(<Notifications />);
    expect(await screen.findByText("You are all caught up")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
  });
});

describe("users", () => {
  const ROW = { organization_id: "o1", name: "Zen Retail", organization_status: "active", link_status: "active", started_at: "2026-10-01T00:00:00Z", ended_at: null, subscription_status: "active", access_class: "customer", products: ["easy_crm"], owner_activation: { activation: "active" } };

  it("searches by name on the server and says when nothing matches", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/customers`]: [ok({ results: [ROW], next_cursor: null, has_more: false }), ok({ results: [], next_cursor: null, has_more: false })],
    });
    shell(<Users />);
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    await screen.findByRole("table", { name: "Your customers" });
    fireEvent.change(screen.getByLabelText("Search customers"), { target: { value: "zen" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=zen"))).toBe(true));
    expect(await screen.findByText("No customer matches that search.")).toBeInTheDocument();
  });
});

describe("plans & add-ons and payments", () => {
  it("shows add-ons as a statement, with nothing to buy or create", async () => {
    mockApi({ ...BASE, [`GET ${API}/plans`]: [ok({ currency: "INR", plans: [], can_create_plan: false, not_implemented: { add_ons: "The catalog has no purchasable add-on." } })] });
    shell(<AddOns />);
    expect(await screen.findByText("The catalog has no purchasable add-on.")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("tabs Payments by what the person may read", async () => {
    pathname = `/partner/${ID}/payments`;
    mockApi({
      ...BASE,
      [`GET ${API}/orders`]: [ok({ results: [], next_cursor: null, has_more: false })],
      [`GET ${API}/features`]: [ok({ permissions: [], roles: [], hierarchy: { max_depth: 1 }, not_implemented: {} })],
    });
    shell(<Payments />);
    const tabs = await screen.findByRole("navigation", { name: "Sections" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Orders", "Commissions", "Balance"]);
    expect(within(tabs).getByRole("link", { name: "Orders" })).toHaveAttribute("aria-current", "page");
  });
});

describe("the team", () => {
  const MEMBERS = {
    members: [
      { id: "m1", name: "Owner One", email: "owner@acme.example.com", role: "owner", role_label: "Partner Owner", status: "active", setup_pending: false, mfa_enrolled: true, invited_at: null, last_login_at: null, joined_at: "2026-09-01T00:00:00Z", is_you: true },
      { id: "m2", name: "Sam Sales", email: "sam@acme.example.com", role: "sales", role_label: "Sales", status: "active", setup_pending: false, mfa_enrolled: false, invited_at: null, last_login_at: null, joined_at: "2026-10-01T00:00:00Z", is_you: false },
      { id: "m3", name: "Pat Paused", email: "pat@acme.example.com", role: "viewer", role_label: "Viewer", status: "suspended", setup_pending: false, mfa_enrolled: false, invited_at: null, last_login_at: null, joined_at: "2026-10-02T00:00:00Z", is_you: false },
    ],
    roles: [{ key: "sales", label: "Sales", description: "Sells." }, { key: "viewer", label: "Viewer", description: "Reads." }],
    can_manage: true,
  };

  it("suspends and resumes a member without removing them, and never offers it for oneself", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/members`]: [ok(MEMBERS)],
      [`POST ${API}/members/m2/suspend`]: [ok({ id: "m2", status: "suspended" })],
      [`POST ${API}/members/m3/resume`]: [ok({ id: "m3", status: "active" })],
    });
    const { container } = shell(<Team />);
    const table = await screen.findByRole("table", { name: "Your team" });
    const self = within(table).getByText("owner@acme.example.com").closest("tr")!;
    expect(within(self).queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
    const paused = within(table).getByText("pat@acme.example.com").closest("tr")!;
    expect(within(paused).getByText("suspended")).toBeInTheDocument();
    expect(within(paused).queryByRole("combobox")).not.toBeInTheDocument(); // no role change while paused
    fireEvent.click(within(within(table).getByText("sam@acme.example.com").closest("tr")!).getByRole("button", { name: "Suspend" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/members/m2/suspend`)).toBe(true));
    fireEvent.click(within(paused).getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${API}/members/m3/resume`)).toBe(true));
    await expectNoViolations(container);
  });
});

describe("appearance", () => {
  const BRAND = {
    brand_name: "Acme Cloud",
    logo_file_id: null,
    accent_color: "#0a8043",
    theme: { primary: "#0a8043", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#0f1b2d", accent: "#16c060", border: "#d9e6df" },
    support_email: "",
    support_url: "",
    portal: {},
    version: 3,
  };
  const FEATURES = {
    permissions: [],
    roles: [],
    hierarchy: { max_depth: 1 },
    not_implemented: {
      custom_css: "Custom CSS and JavaScript are deliberately not offered.",
      brand_assets: "One logo is supported.",
      appearance_layout: "A custom sign-in background, landing cards, a preloader and a menu layout have no storage.",
    },
  };

  it("replaces custom CSS with six colour tokens, saves only those, and never offers a stylesheet", async () => {
    const calls = mockApi({
      ...BASE,
      [`GET ${API}/branding`]: [ok({ brand: BRAND })],
      [`GET ${API}/features`]: [ok(FEATURES)],
      [`PUT ${API}/branding`]: [ok({ ...BRAND, version: 4 })],
    });
    const { container } = shell(<Appearance />);
    expect(await screen.findByRole("heading", { name: "Advanced theme tokens" })).toBeInTheDocument();
    expect(screen.getByText("Custom CSS and JavaScript are deliberately not offered.")).toBeInTheDocument();
    expect(screen.getByText(/no storage/)).toBeInTheDocument();
    // No free-text styling control of any kind.
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.queryByLabelText(/css|javascript|script|html/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Primary colour"), { target: { value: "#1a56db" } });
    fireEvent.click(screen.getByRole("button", { name: "Save theme tokens" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${API}/branding`)).toBe(true));
    const body = calls.find((c) => c.key === `PUT ${API}/branding`)!.body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["accent_color", "brand_name", "expected_version", "support_email", "support_url", "theme"]);
    expect(Object.keys(body.theme as object).sort()).toEqual(["accent", "border", "on_primary", "on_surface", "primary", "surface"]);
    expect(Object.values(body.theme as Record<string, string>).every((v) => /^#[0-9a-f]{6}$/.test(v))).toBe(true);
    expect(body.accent_color).toBe("#1a56db");
    await expectNoViolations(container);
  });

  it("refuses to save colours whose text would be unreadable", async () => {
    mockApi({ ...BASE, [`GET ${API}/branding`]: [ok({ brand: BRAND })], [`GET ${API}/features`]: [ok(FEATURES)] });
    shell(<Appearance />);
    fireEvent.change(await screen.findByLabelText("Text on primary"), { target: { value: "#0b8244" } });
    expect(screen.getByText(/too low/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save theme tokens" })).toBeDisabled();
  });

  it("computes contrast the WCAG way", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 0);
    expect(contrastRatio("#777777", "#ffffff")).toBeLessThan(4.5);
  });
});

describe("product manager", () => {
  const P = (code: string, name: string, extra: object) => ({
    code, name, description: "", tagline: `${name} tagline`, icon: "", category: "Sales", status: "available", grant_scope: "all_plans",
    enabled_for_sale: true, show_in_explore: true, show_in_my_products: true, display_order: 1,
    trial: { offered_by_platform: false, days: 0, allowed_for_partner: false }, portal_visible_by_platform: true, plans: [], customers: 0, active_subscriptions: 0, ...extra,
  });

  it("previews the customer catalog from the switches, in the chosen order", async () => {
    mockApi({
      ...BASE,
      [`GET ${API}/products`]: [
        ok({
          currency: "INR",
          products: [P("easy_crm", "Easy CRM", {}), P("easybook", "EasyBook", { show_in_explore: false }), P("easy_project", "Easy Project", { enabled_for_sale: false })],
          available: [],
          can_manage: true,
          not_implemented: {},
        }),
      ],
    });
    const { container } = shell(<ProductManager />);
    const explore = await screen.findByRole("list", { name: "Explore Products preview" });
    expect(within(explore).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Easy CRMEasy CRM tagline"]);
    const mine = screen.getByRole("list", { name: "My Products preview" });
    expect(within(mine).getAllByRole("listitem")).toHaveLength(3);
    await expectNoViolations(container);
  });
});

describe("auth configuration", () => {
  it("says what a partner cannot change instead of offering a switch", async () => {
    mockApi({
      ...BASE,
      [`GET ${API}/authentication`]: [
        ok({
          mode: "central", custom_domain_sign_in: false, summary: "Sign-in stays on the central page.", sign_in_url: "https://auth.workeasy360.com",
          brand_domains: [], team: { members: 2, with_second_factor: 1 },
          controlled_by_workeasy360: ["The issuer and signing keys other products trust"], partner_choices: ["Your brand shown on that page"],
        }),
      ],
      [`GET ${API}/features`]: [ok({ permissions: [], roles: [], hierarchy: { max_depth: 1 }, not_implemented: { auth_policy: "How your customers are authenticated is Workeasy360's policy." } })],
    });
    const { container } = shell(<AuthConfiguration />);
    expect(await screen.findByRole("heading", { name: "Auth Configuration" })).toBeInTheDocument();
    expect(await screen.findByText("How your customers are authenticated is Workeasy360's policy.")).toBeInTheDocument();
    const main = within(screen.getByRole("main"));
    expect(main.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(main.queryByRole("combobox")).not.toBeInTheDocument();
    expect(main.queryByRole("button")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});
