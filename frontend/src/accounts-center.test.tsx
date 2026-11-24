import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { routePortalRequest } from "./proxy";
import { pageOwner, type PortalOrigins } from "@/lib/portals";
import { accountsPage } from "@/features/auth/accountsSignIn";
import { CENTER_SECTIONS, centerOrigins } from "@/features/account/centerConfig";
import {
  assignmentLabel,
  connectionChips,
  holdingLabel,
  lastSeenLabel,
  productSignOutMessage,
  sessionLabel,
} from "@/features/account/centerCopy";
import { CenterOverview, CenterSettings } from "@/features/account/AccountsCenter";
import { CenterProducts, organizationRows } from "@/features/account/CenterProducts";
import { ConnectedLogins } from "@/features/account/ConnectedLogins";
import { ActiveSessions, LoginHistory } from "@/features/account/CenterSessions";
import { describeAccountEvent } from "@/features/console/MySecurityScreen";
import type { CenterOrganization, ConnectedProduct } from "@/lib/api/console";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { UserDetailScreen } from "@/features/controlplane/AdminUsers";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/users/u2",
}));

/**
 * ADR-072: the Accounts Control Center on WorkEasy360 Accounts. It exists only while
 * Accounts hosts identity (`W360_ACCOUNTS_IDENTITY`, ADR-071); every section reads its
 * own person-level endpoint; and nothing it says about a product session claims more
 * than the platform knows.
 */

const BASE = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://accounts.workeasy360.com",
  store: "",
};
const ON: PortalOrigins = { ...BASE, accountsIdentity: true };
const ENV = {
  W360_ADMIN_ORIGIN: BASE.admin,
  W360_PARTNER_ORIGIN: BASE.partner,
  W360_APP_ORIGIN: BASE.app,
  W360_AUTH_ORIGIN: BASE.auth,
};
const ORG_ID = "11111111-1111-4111-8111-111111111111";
const BETA_ID = "22222222-2222-4222-8222-222222222222";
const USER_ID = "33333333-3333-4333-8333-333333333333";

function app(code: string, name: string, extra: Partial<AppDirectoryEntry> = {}): AppDirectoryEntry {
  return {
    product_code: code,
    display_name: name,
    description: "",
    licensing_mode: "per_seat",
    state: "active",
    can_launch: true,
    entitlement_status: "active",
    slug: code,
    tagline: "",
    category: "",
    icon: "",
    sso: "enabled",
    health: { status: "healthy", checked_at: null },
    trial: null,
    plan: null,
    seats: null,
    renews_at: null,
    cancel_at: null,
    sign_in: null,
    ...extra,
  } as AppDirectoryEntry;
}

const ACME: CenterOrganization = {
  organization_id: ORG_ID,
  slug: "acme",
  name: "Acme Traders",
  status: "active",
  is_owner: true,
  restricted: null,
  roles: ["owner"],
  may_manage_billing: true,
  may_assign_seats: true,
  apps: [app("easy_crm", "Easy CRM"), app("easybook", "EasyBook", { state: "no_entitlement", can_launch: false, entitlement_status: null })],
  explore: [
    { code: "easy_crm", trial_state: "owned", trial_outcome: null, trial_ends_at: null, plans: [] },
    { code: "easybook", trial_state: "used", trial_outcome: "expired", trial_ends_at: "2026-11-01T00:00:00Z", plans: [] },
  ],
  setup: [],
};
const BETA: CenterOrganization = {
  organization_id: BETA_ID,
  slug: "beta",
  name: "Beta Labs",
  status: "active",
  is_owner: false,
  restricted: null,
  roles: ["member"],
  may_manage_billing: false,
  may_assign_seats: false,
  apps: [app("easy_crm", "Easy CRM", { state: "no_seat", can_launch: false, entitlement_status: "trial" })],
  explore: [],
  setup: [{ id: "s1", product_code: "easy_crm", status: "pending" }],
};
const PRODUCTS = { organizations: [ACME, BETA], app_origin: BASE.app, store_origin: "https://store.workeasy360.com" };

function connected(extra: Partial<ConnectedProduct> = {}): ConnectedProduct {
  return {
    product_code: "easy_crm",
    name: "Easy CRM",
    icon: "",
    domain: { host: "crm.workeasy360.com", verified: true },
    subject: USER_ID,
    integration_status: "active",
    sso_connected: true,
    last_sign_in_at: "2026-11-19T09:00:00Z",
    last_activity_at: null,
    session: { platform_sign_in: "open", since: "2026-11-19T08:59:00Z", ended_at: null },
    last_sign_out: null,
    can_sign_out: true,
    organizations: [
      {
        organization_id: ORG_ID,
        name: "Acme Traders",
        slug: "acme",
        member: true,
        access: { state: "active", can_launch: true, entitlement_status: "active", licensing_mode: "per_seat", trial_ends_at: null },
        connection: { first_sign_in_at: "2026-11-01T09:00:00Z", last_sign_in_at: "2026-11-19T09:00:00Z", sign_in_count: 4, last_activity_at: null },
      },
    ],
    ...extra,
  };
}

const SESSION = { authenticated: true, user_id: USER_ID, email: "ravi@example.com", name: "Ravi Kumar" };

let assign: ReturnType<typeof vi.fn>;
beforeEach(() => {
  assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign, replace: vi.fn() });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("routing", () => {
  it("exists only while Accounts hosts identity", () => {
    expect(centerOrigins(ENV)).toBeNull();
    expect(centerOrigins({ ...ENV, W360_ACCOUNTS_IDENTITY: "off" })).toBeNull();
    expect(centerOrigins({ ...ENV, W360_ACCOUNTS_IDENTITY: "on" })).toEqual({ appOrigin: BASE.app });
  });

  it("every section is an Accounts page, signs in on Accounts and comes back", () => {
    expect(CENTER_SECTIONS.map((s) => s.label)).toEqual([
      "Overview",
      "My Products",
      "Connected Logins",
      "Active Sessions",
      "Login History",
      "Organizations",
      "Security",
      "Account Settings",
    ]);
    for (const { href } of CENTER_SECTIONS) {
      expect(pageOwner(href, ON), href).toBe("auth");
      expect(accountsPage(href), href).toBe(href);
      const signedOut = routePortalRequest({ origins: ON, host: "accounts.workeasy360.com", pathname: href, search: "", hasSession: false });
      expect(signedOut).toEqual({ kind: "redirect", location: `${BASE.auth}/login?next=${encodeURIComponent(href)}` });
      // On the customer portal it opens on Accounts.
      const elsewhere = routePortalRequest({ origins: ON, host: "app.workeasy360.com", pathname: href, search: "", hasSession: true });
      expect(elsewhere).toEqual({ kind: "redirect", location: `${BASE.auth}${href}` });
    }
  });
});

describe("the words", () => {
  it("never calls a product session active, whatever is recorded", () => {
    for (const state of ["open", "ended", "untracked", "none"] as const) {
      for (const sso of [true, false]) {
        for (const status of [null, "delivered", "pending", "failed"] as const) {
          const product = connected({
            sso_connected: sso,
            session: { platform_sign_in: state, since: null, ended_at: null },
            last_sign_out: status ? { status, requested_at: "2026-11-19T09:00:00Z", finished_at: null, by_this_product_sign_out: true } : null,
          });
          const said = [sessionLabel(product).label, sessionLabel(product).detail, ...connectionChips(product).map((c) => c.label)];
          for (const text of said) expect(text, text).not.toMatch(/\bactive\b/i);
        }
      }
    }
  });

  it("says last seen or unknown, never invents a time", () => {
    expect(lastSeenLabel(null)).toBe("Last seen: unknown");
    expect(lastSeenLabel("2026-11-19T09:00:00Z", Date.parse("2026-11-19T09:05:00Z"))).toBe("Last seen 5 min ago");
  });

  it("tells purchased, assigned, connected and signed out apart", () => {
    expect(holdingLabel("active")?.label).toBe("Purchased");
    expect(holdingLabel("trial")?.label).toBe("Trial");
    expect(holdingLabel(null)).toBeNull();
    expect(assignmentLabel({ state: "active", licensing_mode: "per_seat" }).label).toBe("Seat assigned to you");
    expect(assignmentLabel({ state: "active", licensing_mode: "per_organization" }).label).toBe("Included for every member");
    expect(assignmentLabel({ state: "no_seat", licensing_mode: "per_seat" }).label).toBe("Not assigned to you");
    expect(connectionChips(connected()).map((c) => c.label)).toEqual([
      "Purchased",
      "Assigned to you",
      "SSO connection established",
      expect.stringMatching(/^Last signed in /),
      "WorkEasy360 sign-in still open",
    ]);
    expect(productSignOutMessage("Easy CRM", "pending")).toMatch(/has not confirmed yet/);
    expect(describeAccountEvent({ kind: "product_sign_in", reason: "easy_crm" })).toBe("Signed in to a product with WorkEasy360");
  });

  it("classifies with My Products' own rows: purchased, expired trial, pending setup", () => {
    const acme = organizationRows(ACME);
    expect(acme.map((r) => [r.app.product_code, r.held, r.expired])).toEqual([
      ["easy_crm", true, false],
      ["easybook", false, true],
    ]);
    const beta = organizationRows(BETA);
    expect(beta[0].trial && beta[0].pendingSetup).toBe(true);
  });
});

describe("the sections", () => {
  it("Overview: the person, their organizations, what they can open and recent activity; passes axe", async () => {
    mockApi({
      "GET /api/v1/auth/account/profile": [
        ok({ user_id: USER_ID, email: "ravi@example.com", name: "Ravi Kumar", email_verified: true, created_at: "2026-01-01T00:00:00Z", last_sign_in_at: null }),
      ],
      "GET /api/v1/auth/account/products": [ok(PRODUCTS)],
      "GET /api/v1/auth/account/history": [
        ok({ results: [{ occurred_at: "2026-11-19T09:00:00Z", kind: "product_sign_in", reason: "easy_crm", ip_prefix: "", device_label: "", product_name: "Easy CRM" }], next_cursor: null, has_more: false }),
      ],
      "POST /api/v1/auth/account/products/11111111-1111-4111-8111-111111111111/easy_crm/launch": [
        ok({ redirect_url: `https://crm.workeasy360.com/launch?organization=${ORG_ID}`, state: "active", sign_in: "workeasy360" }),
      ],
    });
    const { container } = render(<CenterOverview appOrigin={BASE.app} />);
    expect(screen.getByRole("heading", { level: 1, name: "Your WorkEasy360 account" })).toBeInTheDocument();
    expect(await screen.findByText(USER_ID)).toBeInTheDocument();
    expect((await screen.findAllByRole("link", { name: "Acme Traders" }))[0]).toHaveAttribute("href", `${BASE.app}/o/acme`);
    expect(await screen.findByText("Signed in to a product with WorkEasy360: Easy CRM")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Your account" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Switch account" })).toBeInTheDocument();
    // Only products open to the person, and only through the launcher's own route.
    fireEvent.click(screen.getByRole("button", { name: "Open Easy CRM in Acme Traders" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`https://crm.workeasy360.com/launch?organization=${ORG_ID}`));
    expect(screen.queryByRole("button", { name: /Open EasyBook/ })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("My Products: across organizations, filtered; a refused launch says why", async () => {
    mockApi({
      "GET /api/v1/auth/account/products": [ok(PRODUCTS)],
    });
    const { container } = render(<CenterProducts />);
    const acme = await screen.findByRole("region", { name: "Acme Traders" });
    expect(within(acme).getByText("Purchased")).toBeInTheDocument();
    expect(within(acme).getByText("Seat assigned to you")).toBeInTheDocument();
    expect(within(acme).getByText("Expired")).toBeInTheDocument();
    expect(within(acme).getByRole("link", { name: "Billing in User Admin" })).toHaveAttribute("href", `${BASE.app}/o/acme/settings/billing`);
    const beta = screen.getByRole("region", { name: "Beta Labs" });
    expect(within(beta).getByText("Not assigned to you")).toBeInTheDocument();
    expect(within(beta).getByText("Setup: Waiting for your details")).toBeInTheDocument();
    expect(within(beta).queryByRole("button", { name: /Open/ })).not.toBeInTheDocument();
    expect(within(beta).queryByRole("link", { name: "Billing in User Admin" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expired" }));
    expect(within(acme).queryByText("Easy CRM")).not.toBeInTheDocument();
    expect(within(acme).getByText("EasyBook")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("Connected Logins: what is known, and signing out of one product", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/connections": [ok({ subject: USER_ID, products: [connected()] })],
      "POST /api/v1/auth/account/connections/easy_crm/sign-out": [ok({ product_code: "easy_crm", status: "pending" })],
    });
    const { container } = render(<ConnectedLogins />);
    const card = await screen.findByRole("region", { name: "Easy CRM" });
    expect(within(card).getByText("Verified domain")).toBeInTheDocument();
    expect(within(card).getAllByText("WorkEasy360 sign-in still open").length).toBeGreaterThan(0);
    expect(within(card).getAllByText("Last seen: unknown").length).toBeGreaterThan(0);
    expect(within(card).getByText("SSO connection established")).toBeInTheDocument();
    expect(within(card).getAllByText(USER_ID).length).toBeGreaterThan(0);
    expect(card.textContent).not.toMatch(/\bActive\b/);
    await expectNoViolations(container);
    fireEvent.click(within(card).getByRole("button", { name: "Sign out of this product" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign out of Easy CRM" }));
    expect(await screen.findByText(/has been told to sign you out and has not confirmed yet/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/auth/account/connections/easy_crm/sign-out")).toBe(true);
  });

  it("Active Sessions: one row per sign-in; signing one out ends that sign-in", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/sessions": [
        ok({
          sessions: [
            { session_id: "here", current: true, signed_in_at: "2026-11-19T08:00:00Z", last_active_at: "2026-11-19T09:00:00Z", expires_at: "2026-12-19T08:00:00Z", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", portal_sessions: 2 },
            { session_id: "phone", current: false, signed_in_at: "2026-11-18T08:00:00Z", last_active_at: "2026-11-18T09:00:00Z", expires_at: "2026-12-18T08:00:00Z", ip_prefix: "198.51.100.0/24", device_label: "Safari on iPhone", portal_sessions: 1 },
          ],
        }),
      ],
      "POST /api/v1/auth/sessions/phone/revoke": [ok({ ok: true, ended: 1 })],
    });
    const { container } = render(<ActiveSessions />);
    expect(await screen.findByText("Safari on iPhone")).toBeInTheDocument();
    expect(screen.getByText("This device")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3); // header + one per sign-in
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: /Sign out Safari on iPhone/ }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/auth/sessions/phone/revoke")).toBe(true));
  });

  it("Login History: product names, and pages past the first", async () => {
    const row = (i: number, extra = {}) => ({
      occurred_at: `2026-11-19T09:${String(59 - i).padStart(2, "0")}:00Z`,
      kind: "sign_in",
      reason: "",
      ip_prefix: "203.0.113.0/24",
      device_label: "Chrome on Windows",
      product_name: null,
      ...extra,
    });
    const calls = mockApi({
      "GET /api/v1/auth/account/history": [
        ok({ results: [row(0, { kind: "product_sign_in", reason: "easy_crm", product_name: "Easy CRM" }), row(1, { kind: "sign_in_failed", reason: "invalid_credentials" })], next_cursor: "next", has_more: true }),
        ok({ results: [row(2)], next_cursor: null, has_more: false }),
      ],
    });
    const { container } = render(<LoginHistory />);
    expect(await screen.findByText("Easy CRM")).toBeInTheDocument();
    expect(screen.getByText("Failed sign-in attempt (wrong password)")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByText("Signed in")).toHaveLength(1));
    expect(calls[1].url).toContain("cursor=next");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("Account Settings: the name is saved through the profile API; Switch account starts ADR-066", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok(SESSION)],
      "GET /api/v1/auth/account/profile": [
        ok({ user_id: USER_ID, email: "ravi@example.com", name: "Ravi", email_verified: true, created_at: null, last_sign_in_at: null }),
      ],
      "PATCH /api/v1/auth/account/profile": [
        ok({ user_id: USER_ID, email: "ravi@example.com", name: "Ravi Kumar", email_verified: true, created_at: null, last_sign_in_at: null }),
      ],
      "POST /api/v1/auth/switch-account": [ok({ redirect_url: `${BASE.auth}/switch-account#handle=abc` })],
    });
    const { container } = render(<CenterSettings />);
    const name = await screen.findByLabelText("Full name");
    fireEvent.change(name, { target: { value: "Ravi Kumar" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("Your name is saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "PATCH /api/v1/auth/account/profile")?.body).toEqual({ full_name: "Ravi Kumar" });
    expect(screen.getByRole("link", { name: "Change how products sign you in" })).toHaveAttribute("href", "/account/sign-in-preferences");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Use a different account" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`${BASE.auth}/switch-account#handle=abc`));
  });
});

describe("the Super Admin's view of a person", () => {
  const T = "2026-11-19T09:00:00Z";
  const detail = {
    user_id: "u2",
    email: "u2@example.com",
    name: "Person Two",
    status: "active",
    is_staff: false,
    locked: false,
    organizations: 1,
    email_verified: true,
    acquisition_source: "self_signup",
    created_at: T,
    last_sign_in_at: T,
    lifecycle: "customer",
    orders: [],
    memberships: [
      { organization_id: ORG_ID, slug: "acme", name: "Acme Ltd", is_owner: true, access_class: "customer", subscription_status: "active", products: ["easy_crm"], seats: ["easy_crm"] },
    ],
    sessions: [],
    activity: [],
    central_user_id: "u2",
    connections: [
      {
        product_code: "easy_crm",
        name: "Easy CRM",
        domain: { host: "crm.workeasy360.com", verified: true },
        takes_backchannel_logout: true,
        platform_sign_in: "open",
        platform_sign_in_since: T,
        platform_sign_in_ended_at: null,
        last_sign_out: null,
        organizations: [{ organization_id: ORG_ID, name: "Acme Ltd", first_sign_in_at: T, last_sign_in_at: T, sign_in_count: 3, last_activity_at: null }],
      },
    ],
    sign_in_history: [{ occurred_at: T, kind: "product_sign_in", reason: "easy_crm", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", product_name: "Easy CRM" }],
    sign_ins: [{ session_id: "s1", signed_in_at: T, last_active_at: T, expires_at: T, ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", portal_sessions: 2 }],
  };

  it("maps the person to organizations, seats, product sign-ins, history and devices", async () => {
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "s@example.com" })],
      "GET /api/v1/admin/users/u2": [ok(detail)],
    });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_user.view"]}>
        <main>
          <UserDetailScreen userId="u2" />
        </main>
      </AdminContextForTest>,
    );
    await screen.findAllByText("Acme Ltd");
    expect(screen.getAllByText(/Seats: /)[0]).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Product sign-ins/ }));
    const table = screen.getByRole("table", { name: "Product sign-ins with WorkEasy360, per organization" });
    expect(table).toHaveTextContent("Easy CRM");
    expect(table).toHaveTextContent("WorkEasy360 sign-in still open");
    expect(table).toHaveTextContent("Unknown");
    expect(table.textContent).not.toMatch(/\bActive\b/);
    fireEvent.click(screen.getByRole("tab", { name: /Sessions/ }));
    expect(screen.getByRole("table", { name: "Signed-in devices, one row per sign-in" })).toHaveTextContent("Chrome on Windows");
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("table", { name: "Sign-in history: WorkEasy360 and product sign-ins" })).toHaveTextContent("Easy CRM");
    await expectNoViolations(container);
  });
});
