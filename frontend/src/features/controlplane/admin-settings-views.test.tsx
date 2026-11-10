import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PortalsProvider } from "@/lib/portalContext";
import type { PortalOrigins } from "@/lib/portals";
import { PlatformSettingsScreen } from "./AdminCommerce";
import { DomainsScreen, LoginClientsScreen, SignInAuditScreen } from "./AdminDomains";
import { AdminNotifications } from "./AdminNotifications";
import { AuditScreen, SecurityScreen } from "./AdminScreens";
import { AdminTools } from "./AdminTopbar";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";

/**
 * Admin > Settings and the top bar (2026-11-09): the platform settings' new sections,
 * Domains & Apps, Login clients, the Sign-in audit, New Admin, the audit log's server
 * filters, and what the top bar offers.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/settings",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const T = "2026-11-08T10:00:00Z";
const asStaff = (permissions: string[], ui: React.ReactNode) => render(<AdminContextForTest permissions={permissions}>{ui}</AdminContextForTest>);
const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;
const urlsOf = (calls: Call[], key: string) => calls.filter((c) => c.key === key).map((c) => c.url);

// --- platform settings -----------------------------------------------------------------

const SETTINGS_VIEW = "platform.staff_settings.view";
const SETTINGS_MANAGE = "platform.staff_settings.manage";
const SETTINGS = "/api/v1/admin/settings";
const CURRENT = {
  signup_enabled: true,
  partner_applications_enabled: true,
  support_email: "help@example.com",
  sales_email: "sales@example.com",
  catalog_currency: "INR",
  public_catalog_currency: "USD",
  default_currency: "INR",
  currencies: ["INR", "USD"],
  company_name: "WorkEasy",
  company_legal_name: "",
  company_address: "1 Main Road",
  company_tax_id: "GST123",
  tax_rate_bp: null,
  default_tax_rate_bp: 1800,
  notifications_email: "",
  version: 7,
};
/** What the server is sent when nothing else is changed: every field, always. */
const UNCHANGED = {
  signup_enabled: true,
  partner_applications_enabled: true,
  support_email: "help@example.com",
  sales_email: "sales@example.com",
  public_catalog_currency: "USD",
  company_name: "WorkEasy",
  company_legal_name: "",
  company_address: "1 Main Road",
  company_tax_id: "GST123",
  tax_rate_bp: null,
  notifications_email: "",
  version: 7,
};

describe("platform settings' new sections", () => {
  it("shows them, with the rate that applies when none is entered; passes axe", async () => {
    mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)] });
    const { container } = asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    expect((await screen.findByText("Company name")).nextElementSibling).toHaveTextContent("WorkEasy");
    expect(screen.getByText("Legal name").nextElementSibling).toHaveTextContent("Not set");
    expect(screen.getByText("Tax rate").nextElementSibling).toHaveTextContent("Deployment default (18%)");
    expect(screen.getByText("Partner applications").nextElementSibling).toHaveTextContent("Open");
    expect(screen.getByRole("button", { name: "Add a notifications email" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("saves company details with every other field and the version", async () => {
    const calls = mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)], [`PUT ${SETTINGS}`]: [ok({ ...CURRENT, version: 8 })] });
    asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit company information" }));
    fireEvent.change(screen.getByLabelText("Legal name"), { target: { value: " WorkEasy Pvt Ltd " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodyOf(calls, `PUT ${SETTINGS}`)).toEqual({ ...UNCHANGED, company_legal_name: "WorkEasy Pvt Ltd" }));
    await waitFor(() => expect(screen.queryByLabelText("Legal name")).toBeNull());
  });

  it("takes the tax rate as a percentage and sends basis points; refuses a bad one before sending", async () => {
    const calls = mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)], [`PUT ${SETTINGS}`]: [ok(CURRENT)] });
    asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit billing" }));
    const rate = screen.getByLabelText("Tax rate (%)");
    expect(rate).toHaveValue("");
    expect(screen.getByText(/Left empty, 18% applies/)).toBeInTheDocument();
    fireEvent.change(rate, { target: { value: "120" } });
    expect(screen.getByText(/percentage from 0 to 100/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(rate, { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodyOf(calls, `PUT ${SETTINGS}`)).toEqual({ ...UNCHANGED, tax_rate_bp: 1250 }));
  });

  it("starts the rate field from what is stored, and clearing it goes back to the default", async () => {
    const calls = mockApi({ [`GET ${SETTINGS}`]: [ok({ ...CURRENT, tax_rate_bp: 1250 })], [`PUT ${SETTINGS}`]: [ok(CURRENT)] });
    asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    expect((await screen.findByText("Tax rate")).nextElementSibling).toHaveTextContent("12.5%");
    fireEvent.click(screen.getByRole("button", { name: "Edit billing" }));
    expect(screen.getByLabelText("Tax rate (%)")).toHaveValue("12.5");
    fireEvent.change(screen.getByLabelText("Tax rate (%)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodyOf(calls, `PUT ${SETTINGS}`)).toEqual(UNCHANGED));
  });

  it("closes partner applications and sets the notifications address", async () => {
    const calls = mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)], [`PUT ${SETTINGS}`]: [ok({ ...CURRENT, version: 8 })] });
    const view = asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit applications" }));
    fireEvent.click(screen.getByLabelText("Partner applications are open"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodyOf(calls, `PUT ${SETTINGS}`)).toEqual({ ...UNCHANGED, partner_applications_enabled: false }));
    view.unmount();

    const second = mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)], [`PUT ${SETTINGS}`]: [ok({ ...CURRENT, version: 8 })] });
    asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit notifications" }));
    fireEvent.change(screen.getByLabelText("Notifications email"), { target: { value: "ops@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodyOf(second, `PUT ${SETTINGS}`)).toEqual({ ...UNCHANGED, notifications_email: "ops@example.com" }));
  });

  it("keeps the edit open and shows the server's refusal", async () => {
    mockApi({
      [`GET ${SETTINGS}`]: [ok(CURRENT)],
      [`PUT ${SETTINGS}`]: [{ status: 409, body: { detail: "Someone else changed the platform settings. Reload and try again." } }],
    });
    asStaff([SETTINGS_VIEW, SETTINGS_MANAGE], <PlatformSettingsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit company information" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Someone else changed the platform settings.");
    expect(screen.getByLabelText("Company name")).toBeInTheDocument();
  });

  it("offers view-only staff no edit, and says when it cannot load", async () => {
    mockApi({ [`GET ${SETTINGS}`]: [ok(CURRENT)] });
    const view = asStaff([SETTINGS_VIEW], <PlatformSettingsScreen />);
    await screen.findByText("Company name");
    expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Add/ })).toBeNull();
    view.unmount();

    mockApi({ [`GET ${SETTINGS}`]: [{ status: 500, body: { detail: "Settings could not be read." } }] });
    asStaff([SETTINGS_VIEW], <PlatformSettingsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Settings could not be read.");
  });
});

// --- domains & apps -------------------------------------------------------------------------

const DOMAINS_URL = "/api/v1/admin/settings/domains";
const CATALOG_VIEW = "platform.staff_catalog.view";
const DOMAINS = {
  portals: [
    { key: "admin", label: "Admin Portal", origin: "https://admin.example.com" },
    { key: "app", label: "Customer Portal", origin: "https://app.example.com" },
  ],
  apps: [
    {
      code: "easy_crm",
      name: "Easy CRM",
      status: "active",
      application_url: "https://crm.example.com",
      marketing_url: "",
      documentation_url: "",
      support_url: "",
      app_links: { android: "https://play.example.com/crm", odd: "javascript:alert(1)" },
      store_link: "/store/product/easy-crm",
      purchase_routes: { store: true, reseller: false, affiliate: true, white_label: false },
    },
    {
      code: "easybook",
      name: "EasyBook",
      status: "draft",
      application_url: "",
      marketing_url: "",
      documentation_url: "",
      support_url: "",
      app_links: {},
      store_link: "",
      purchase_routes: { store: false, reseller: false, affiliate: false, white_label: false },
    },
  ],
  partner_domains: [{ id: "d1", hostname: "crm.bright.example", status: "verified", partner_id: "p1", partner_name: "Bright Partner", verified_at: T }],
  brands: [{ partner_id: "p1", partner_name: "Bright Partner", brand_name: "Bright", has_logo: true }],
  dns_provider_connected: false,
};
const PRODUCTS = { results: [{ id: "prod-1", code: "easy_crm", name: "Easy CRM" }] };

describe("domains and apps", () => {
  it("lists all four sections and says what the check is; passes axe", async () => {
    mockApi({ [`GET ${DOMAINS_URL}`]: [ok(DOMAINS)], "GET /api/v1/admin/catalog/products": [ok(PRODUCTS)] });
    const { container } = asStaff([SETTINGS_VIEW, CATALOG_VIEW], <DomainsScreen />);
    const portals = await screen.findByRole("table", { name: "Primary domains" });
    expect(within(portals).getByText("Admin Portal")).toBeInTheDocument();
    expect(screen.getByText("No Cloudflare account is connected, so this is a live check of the address itself, not Cloudflare's own status.")).toBeInTheDocument();

    const apps = screen.getByRole("table", { name: "Product addresses and apps" });
    const crm = within(apps).getByRole("row", { name: /easy_crm/ });
    await waitFor(() => expect(within(crm).getByRole("link", { name: "Easy CRM" })).toHaveAttribute("href", "/admin/products/prod-1"));
    expect(within(crm).getByText("crm.example.com")).toBeInTheDocument();
    expect(within(crm).getByRole("link", { name: /Android/ })).toHaveAttribute("href", "https://play.example.com/crm");
    // Only a web address becomes a link.
    expect(within(crm).queryByRole("link", { name: /Odd/ })).toBeNull();
    expect(within(crm).getByText("Odd: javascript:alert(1)")).toBeInTheDocument();
    expect(within(crm).getByText("app.example.com/store/product/easy-crm")).toBeInTheDocument();
    expect(within(crm).getByText("Store")).toBeInTheDocument();
    expect(within(crm).getByText("Affiliate")).toBeInTheDocument();
    expect(within(crm).queryByText("Reseller")).toBeNull();
    const book = within(apps).getByRole("row", { name: /easybook/ });
    expect(within(book).getByText("Not on sale")).toBeInTheDocument();
    // Not in the catalog list: the products page, rather than a dead address.
    expect(within(book).getByRole("link", { name: "EasyBook" })).toHaveAttribute("href", "/admin/products");

    const partnerDomains = screen.getByRole("table", { name: "Partner domains" });
    expect(within(partnerDomains).getByText("crm.bright.example")).toBeInTheDocument();
    expect(within(partnerDomains).getByRole("link", { name: "Bright Partner" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(within(partnerDomains).getByRole("row", { name: /crm\.bright\.example/ })).getByText("Verified")).toBeInTheDocument();
    const brands = screen.getByRole("table", { name: "Partner branding" });
    expect(within(brands).getByText("Bright")).toBeInTheDocument();
    expect(within(brands).getByText("Has logo")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("checks a portal's address live and shows exactly what came back", async () => {
    const calls = mockApi({
      [`GET ${DOMAINS_URL}`]: [ok(DOMAINS)],
      [`POST ${DOMAINS_URL}/check`]: [
        ok({ host: "admin.example.com", ok: true, detail: "Resolves, and answers over HTTPS with a valid certificate (http_200)." }),
        ok({ host: "app.example.com", ok: false, detail: "Did not answer over HTTPS (timeout)." }),
      ],
    });
    asStaff([SETTINGS_VIEW], <DomainsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Check Admin Portal" }));
    expect(await screen.findByText(/valid certificate \(http_200\)/)).toBeInTheDocument();
    expect(screen.getByText("Answering")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${DOMAINS_URL}/check`)).toEqual({ host: "admin.example.com" });
    fireEvent.click(screen.getByRole("button", { name: "Check Customer Portal" }));
    expect(await screen.findByText(/Did not answer over HTTPS/)).toBeInTheDocument();
    expect(screen.getByText("Not answering")).toBeInTheDocument();
  });

  it("shows a refused check in the server's words", async () => {
    mockApi({
      [`GET ${DOMAINS_URL}`]: [ok(DOMAINS)],
      [`POST ${DOMAINS_URL}/check`]: [{ status: 400, body: { detail: "That address is not one of the platform's." } }],
    });
    asStaff([SETTINGS_VIEW], <DomainsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Check Admin Portal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That address is not one of the platform's.");
  });

  it("links no product page without the catalog grant, and does not ask the catalog", async () => {
    const calls = mockApi({ [`GET ${DOMAINS_URL}`]: [ok(DOMAINS)] });
    asStaff([SETTINGS_VIEW], <DomainsScreen />);
    const apps = await screen.findByRole("table", { name: "Product addresses and apps" });
    expect(within(apps).queryByRole("link", { name: "Easy CRM" })).toBeNull();
    expect(within(apps).getByText("Easy CRM")).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual([`GET ${DOMAINS_URL}`]);
  });

  it("says when each section is empty, and when it cannot load", async () => {
    mockApi({ [`GET ${DOMAINS_URL}`]: [ok({ portals: [], apps: [], partner_domains: [], brands: [], dns_provider_connected: true })] });
    const view = asStaff([SETTINGS_VIEW], <DomainsScreen />);
    expect(await screen.findByText("No portal addresses are configured")).toBeInTheDocument();
    expect(screen.getByText("No products yet")).toBeInTheDocument();
    expect(screen.getByText("No partner domains yet")).toBeInTheDocument();
    expect(screen.getByText("No partner has set up a brand yet")).toBeInTheDocument();
    expect(screen.queryByText(/No Cloudflare account/)).toBeNull();
    view.unmount();

    mockApi({ [`GET ${DOMAINS_URL}`]: [{ status: 500, body: { detail: "Domains could not be read." } }] });
    asStaff([SETTINGS_VIEW], <DomainsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Domains could not be read.");
  });
});

// --- login clients ----------------------------------------------------------------------------

const CLIENTS_URL = "/api/v1/admin/settings/login-clients";
const CLIENT = {
  product_code: "easy_crm",
  client_id: "client-1234567890abcdef",
  status: "active",
  redirect_uris: ["https://crm.example.com/callback", "https://crm.example.com/alt"],
  post_logout_redirect_uris: ["https://crm.example.com/bye"],
  launch_url: "https://crm.example.com/start",
};

describe("login clients", () => {
  it("lists every client's destinations, read-only, with the way to change them; passes axe", async () => {
    mockApi({ [`GET ${CLIENTS_URL}`]: [ok({ results: [CLIENT] })] });
    const { container } = asStaff([SETTINGS_VIEW, CATALOG_VIEW], <LoginClientsScreen />);
    const table = await screen.findByRole("table", { name: "Login clients" });
    expect(within(table).getByText("easy_crm")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: "Copy client ID" })).toBeInTheDocument();
    expect(within(table).getByText("https://crm.example.com/callback")).toBeInTheDocument();
    expect(within(table).getByText("https://crm.example.com/alt")).toBeInTheDocument();
    expect(within(table).getByText("https://crm.example.com/bye")).toBeInTheDocument();
    expect(within(table).getByText("crm.example.com/start")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open products/ })).toHaveAttribute("href", "/admin/products");
    await expectNoViolations(container);
  });

  it("hides the way to change them without the catalog grant", async () => {
    mockApi({ [`GET ${CLIENTS_URL}`]: [ok({ results: [CLIENT] })] });
    asStaff([SETTINGS_VIEW], <LoginClientsScreen />);
    await screen.findByRole("table", { name: "Login clients" });
    expect(screen.queryByRole("link", { name: /Open products/ })).toBeNull();
  });

  it("says when there are none, and when it cannot load", async () => {
    mockApi({ [`GET ${CLIENTS_URL}`]: [ok({ results: [] })] });
    const view = asStaff([SETTINGS_VIEW], <LoginClientsScreen />);
    expect(await screen.findByText("No login clients yet")).toBeInTheDocument();
    view.unmount();

    mockApi({ [`GET ${CLIENTS_URL}`]: [{ status: 500, body: { detail: "Clients could not be read." } }] });
    asStaff([SETTINGS_VIEW], <LoginClientsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Clients could not be read.");
  });
});

// --- the sign-in audit --------------------------------------------------------------------------

const SIGN_INS = "GET /api/v1/admin/security/sign-ins";
const AUDIT_VIEW = "platform.staff_audit.view";
const EVENT = {
  id: "e1",
  user_id: "u1",
  email: "pat@example.com",
  name: "Pat Lee",
  is_staff: true,
  kind: "sign_in_failed",
  kind_label: "Sign-in failed",
  reason: "bad_password",
  ip_prefix: "203.0.113.0/24",
  device_label: "Chrome on Windows",
  occurred_at: T,
};

describe("the sign-in audit", () => {
  it("shows each column, staff only by default; passes axe", async () => {
    const calls = mockApi({ [SIGN_INS]: [ok({ results: [EVENT], total: 40, limit: 25, offset: 0 })] });
    const { container } = asStaff([AUDIT_VIEW], <SignInAuditScreen />);
    const table = await screen.findByRole("table", { name: "Sign-in events" });
    for (const text of ["Pat Lee", "pat@example.com", "Sign-in failed", "bad_password", "203.0.113.0/24", "Chrome on Windows"]) {
      expect(within(table).getByText(text)).toBeInTheDocument();
    }
    expect(within(table).getByText(/8 Nov 2026/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Staff only" })).toHaveAttribute("aria-pressed", "true");
    expect(urlsOf(calls, SIGN_INS)[0]).toBe("/api/v1/admin/security/sign-ins?limit=25&offset=0");
    await expectNoViolations(container);
  });

  it("sends each filter to the server, and pages through it", async () => {
    const calls = mockApi({ [SIGN_INS]: [ok({ results: [EVENT], total: 40, limit: 25, offset: 0 })] });
    asStaff([AUDIT_VIEW], <SignInAuditScreen />);
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toContain("offset=25"));
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.click(screen.getByRole("button", { name: "Everyone" }));
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toBe("/api/v1/admin/security/sign-ins?everyone=1&limit=25&offset=0"));
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.click(screen.getByRole("combobox", { name: "Event" }));
    fireEvent.click(screen.getByRole("option", { name: "Account unlocked" }));
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toContain("kind=account_unlocked"));
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toContain("from=2026-11-01"));
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-08" } });
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toContain("to=2026-11-08"));
    await screen.findByRole("table", { name: "Sign-in events" });
    fireEvent.change(screen.getByLabelText("Search by email"), { target: { value: "pat" } });
    await waitFor(() => expect(urlsOf(calls, SIGN_INS).at(-1)).toContain("q=pat"));
  });

  it("says when there are none, and shows the server's refusal", async () => {
    mockApi({ [SIGN_INS]: [ok({ results: [], total: 0, limit: 25, offset: 0 })] });
    const view = asStaff([AUDIT_VIEW], <SignInAuditScreen />);
    expect(await screen.findByText("No sign-in events")).toBeInTheDocument();
    view.unmount();

    mockApi({ [SIGN_INS]: [{ status: 400, body: { detail: "Use a date like 2026-11-09." } }] });
    const again = asStaff([AUDIT_VIEW], <SignInAuditScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Use a date like 2026-11-09.");
    again.unmount();

    mockApi({ [SIGN_INS]: [{ status: 403, body: { detail: "Forbidden." } }] });
    asStaff([], <SignInAuditScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to the sign-in audit.");
  });
});

// --- New Admin ---------------------------------------------------------------------------------------

const SECURITY_VIEW = "platform.staff_security.view";
const SECURITY_MANAGE = "platform.staff_security.manage";
const ROLES_URL = "GET /api/v1/admin/security/roles";
const ACCOUNTS = "POST /api/v1/admin/security/accounts";

function fillNewAdmin() {
  const popup = screen.getByRole("dialog", { name: "New Admin" });
  fireEvent.change(within(popup).getByLabelText("Full name"), { target: { value: " Asha Rao " } });
  fireEvent.change(within(popup).getByLabelText("Email"), { target: { value: "asha@example.com" } });
  fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Joined the billing team" } });
  return popup;
}

describe("New Admin", () => {
  it("creates the account with the exact fields, says a link is coming, and refreshes who holds what", async () => {
    const calls = mockApi({
      [ROLES_URL]: [ok({ results: [] })],
      [ACCOUNTS]: [{ status: 201, body: { user_id: "u9", new_account: true, role: "BILLING_ADMIN", added: [] } }],
    });
    const { container } = asStaff([SECURITY_VIEW, SECURITY_MANAGE], <SecurityScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "New Admin" }));
    const popup = screen.getByRole("dialog", { name: "New Admin" });
    expect(within(popup).getByText(/They will get a link to set their own password/)).toBeInTheDocument();
    const create = within(popup).getByRole("button", { name: "Create Admin" });
    expect(create).toBeDisabled();
    fillNewAdmin();
    fireEvent.change(within(popup).getByLabelText("Email"), { target: { value: "not-an-email" } });
    expect(within(popup).getByText("Enter a valid email address.")).toBeInTheDocument();
    expect(create).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Email"), { target: { value: "asha@example.com" } });
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "too short" } });
    expect(create).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Reason"), { target: { value: "Joined the billing team" } });
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Role/ }));
    fireEvent.click(screen.getByRole("option", { name: "Billing Admin" }));
    await expectNoViolations(popup);
    fireEvent.click(create);
    expect(await screen.findByText("Admin created. They will get a link to set their own password.")).toBeInTheDocument();
    expect(bodyOf(calls, ACCOUNTS)).toEqual({ full_name: "Asha Rao", email: "asha@example.com", role: "BILLING_ADMIN", reason: "Joined the billing team" });
    await waitFor(() => expect(urlsOf(calls, ROLES_URL).length).toBe(2));
    await expectNoViolations(container);
  });

  it("says so when the person already had an account, because no link is sent", async () => {
    mockApi({
      [ROLES_URL]: [ok({ results: [] })],
      [ACCOUNTS]: [{ status: 201, body: { user_id: "u9", new_account: false, role: "SUPPORT_ADMIN", added: [] } }],
    });
    asStaff([SECURITY_VIEW, SECURITY_MANAGE], <SecurityScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "New Admin" }));
    const popup = fillNewAdmin();
    fireEvent.click(within(popup).getByRole("button", { name: "Create Admin" }));
    expect(await screen.findByText(/already had an account, so they keep their password/)).toBeInTheDocument();
  });

  it("keeps a refusal in the popup, in the server's words", async () => {
    mockApi({
      [ROLES_URL]: [ok({ results: [] })],
      [ACCOUNTS]: [{ status: 400, body: { detail: "That account cannot be an Admin." } }],
    });
    asStaff([SECURITY_VIEW, SECURITY_MANAGE], <SecurityScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "New Admin" }));
    const popup = fillNewAdmin();
    fireEvent.click(within(popup).getByRole("button", { name: "Create Admin" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("That account cannot be an Admin.");
    expect(within(popup).getByLabelText("Email")).toHaveValue("asha@example.com");
  });

  it("is not offered without the security management grant", async () => {
    mockApi({ [ROLES_URL]: [ok({ results: [] })] });
    asStaff([SECURITY_VIEW], <SecurityScreen />);
    await screen.findByRole("tab", { name: "Roles" });
    expect(screen.queryByRole("button", { name: "New Admin" })).toBeNull();
  });
});

// --- the audit log's filters ----------------------------------------------------------------------------

const AUDIT = "GET /api/v1/admin/audit";
const PERSON = "0b8f6a52-6c1e-4f39-9d55-1f0f6f0e2a11";
const auditPage = (results: unknown[]) => ok({ results, next_cursor: null, has_more: false });
const ENTRY = { id: "a1", occurred_at: T, action: "partner.created", actor_kind: "staff", resource_type: "partner", resource_id: "p1", outcome: "success", reason: "" };

describe("the audit log's server filters", () => {
  it("sends the person's ID and the dates, and has no date filter of its own", async () => {
    const calls = mockApi({ [AUDIT]: [auditPage([ENTRY])] });
    const { container } = asStaff([AUDIT_VIEW], <AuditScreen />);
    await screen.findByRole("table", { name: "Audit entries" });
    expect(screen.queryByRole("combobox", { name: /^Date/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(urlsOf(calls, AUDIT).at(-1)).toContain("from=2026-11-01"));
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-08" } });
    await waitFor(() => expect(urlsOf(calls, AUDIT).at(-1)).toContain("to=2026-11-08"));
    await screen.findByRole("table", { name: "Audit entries" });
    fireEvent.change(screen.getByLabelText("Filter by the person's ID"), { target: { value: PERSON } });
    await waitFor(() => expect(urlsOf(calls, AUDIT).at(-1)).toContain(`actor=${PERSON}`));
    await screen.findByRole("table", { name: "Audit entries" });
    await expectNoViolations(container);
  });

  it("says an incomplete ID is incomplete and does not send it", async () => {
    const calls = mockApi({ [AUDIT]: [auditPage([ENTRY])] });
    asStaff([AUDIT_VIEW], <AuditScreen />);
    await screen.findByRole("table", { name: "Audit entries" });
    fireEvent.change(screen.getByLabelText("Filter by the person's ID"), { target: { value: "0b8f" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("whole ID");
    expect(urlsOf(calls, AUDIT).some((url) => url.includes("actor="))).toBe(false);
  });
});

// --- the top bar -------------------------------------------------------------------------------------------

const ATTENTION = "GET /api/v1/admin/attention";
const SUPPORT_VIEW = "platform.staff_support.view";
const WAITING = {
  items: [
    { key: "kyc_reviews", count: 2 },
    { key: "failed_payments", count: 1 },
    { key: "low_balance", count: 3 },
    { key: "support_tickets", count: 4 },
  ],
};

describe("the top bar", () => {
  it("opens the help library for those who may read it, and the help page for everyone else", async () => {
    mockApi({ [ATTENTION]: [ok({ items: [] })] });
    const view = render(<AdminTools permissions={[SUPPORT_VIEW]} />);
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/admin/help-content");
    view.unmount();

    const second = render(<AdminTools permissions={[]} />);
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "/contact");
    second.unmount();

    const origins = { app: "https://app.example.com" } as PortalOrigins;
    render(
      <PortalsProvider origins={origins}>
        <AdminTools permissions={[]} />
      </PortalsProvider>,
    );
    expect(screen.getByRole("link", { name: "Help" })).toHaveAttribute("href", "https://app.example.com/contact");
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  });

  it("says what is waiting behind the new notifications, and where each is dealt with", async () => {
    mockApi({ [ATTENTION]: [ok(WAITING)] });
    render(<AdminTools permissions={[SUPPORT_VIEW]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Notifications: 10 waiting" }));
    expect(await screen.findByRole("link", { name: /^2 partner applications wait for KYC review/ })).toHaveAttribute("href", "/admin/partners/applications");
    expect(screen.getByRole("link", { name: /^1 failed payment or activation needs attention/ })).toHaveAttribute("href", "/admin/payments/failed");
    expect(screen.getByRole("link", { name: /^3 wallets are low on balance/ })).toHaveAttribute("href", "/admin/wallets?low=1");
    expect(screen.getByRole("link", { name: /^4 support tickets are still open/ })).toHaveAttribute("href", "/admin/tickets");
  });

  it("lists the same on the notifications page", async () => {
    mockApi({ [ATTENTION]: [ok(WAITING)] });
    const { container } = render(<AdminNotifications />);
    const table = await screen.findByRole("table", { name: "Notifications" });
    expect(within(table).getByRole("link", { name: "Open wallets" })).toHaveAttribute("href", "/admin/wallets?low=1");
    expect(within(table).getByRole("link", { name: "Open tickets" })).toHaveAttribute("href", "/admin/tickets");
    expect(within(table).getByText("Partner applications waiting for KYC review")).toBeInTheDocument();
    expect(within(table).getByText("Failed payments and activations")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("offers every Create item to one who holds every grant, in order", async () => {
    mockApi({ [ATTENTION]: [ok({ items: [] })] });
    render(
      <AdminTools
        permissions={[
          "platform.staff_customer.manage",
          "platform.staff_partner.manage",
          "platform.staff_catalog.manage",
          "platform.staff_pricing.manage",
          "platform.staff_support.manage",
          "platform.staff_security.manage",
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const items = screen.getAllByRole("menuitem");
    expect(items.map((m) => m.textContent)).toEqual(["New customer", "New partner", "New product", "New plan", "New credit pack", "New help article", "New Admin"]);
    expect(items[0]).toHaveAttribute("href", "/admin/customers");
    expect(items[4]).toHaveAttribute("href", "/admin/usage-pricing/credit-packs");
    expect(items[5]).toHaveAttribute("href", "/admin/help-content");
    expect(items[6]).toHaveAttribute("href", "/admin/security");
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  });

  it("offers only what the grant covers", async () => {
    mockApi({ [ATTENTION]: [ok({ items: [] })] });
    render(<AdminTools permissions={["platform.staff_support.manage"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["New help article"]);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  });
});
