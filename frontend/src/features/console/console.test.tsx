import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import type { ConsoleAccess } from "@/lib/api/console";
import { OrgAccessContext } from "./OrgAccess";
import { AdminInsights } from "./DashboardScreen";
import { MembersScreen } from "./MembersScreen";
import { InvitationsScreen } from "./InvitationsScreen";
import { OrganizationScreen } from "./OrganizationScreen";
import { BillingScreen, PAYMENT_POLL, checkoutNavigation, safeCheckoutUrl } from "./BillingScreen";
import { ExploreScreen } from "./ExploreScreen";
import { MarkdownLite } from "./MarkdownLite";
import { MySecurityScreen, describeAccountEvent } from "./MySecurityScreen";
import { AuthenticationPolicyScreen, NetworkPolicyScreen } from "./SecurityPolicyScreens";
import { AuditLogScreen, SignInsScreen } from "./AuditScreens";
import { AcceptInvitation } from "./AcceptInvitation";
import { TestCheckout } from "./TestCheckout";
import { MyAppsScreen, ProductAccessScreen } from "./MyAppsScreen";
import { ProfileScreen } from "./ProfileScreen";

/**
 * The organization console (Phase 8 extension): what each screen sends, what
 * it never offers, and the same non-visual axe pass as the other consoles.
 * Contrast, focus visibility and reflow remain manual - jsdom paints nothing.
 */

const nav = vi.hoisted(() => ({
  router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() },
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => nav.search,
  usePathname: () => "/o/acme/dashboard",
  useParams: () => ({ orgSlug: "acme" }),
}));

// Several flows here are three or four round trips plus an axe pass; on a
// busy machine the default one second per query is not enough to be fair.
configure({ asyncUtilTimeout: 4000 });

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

type Reply = { status: number; body?: unknown };
type Call = { key: string; url: string; body: unknown; headers: Record<string, string> };

/** Routes `fetch` by "METHOD /path" to a queue of replies (the last repeats). */
function mockApi(routes: Record<string, Reply[]>) {
  const calls: Call[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const raw = init?.body;
    calls.push({
      key,
      url,
      body: typeof raw === "string" ? JSON.parse(raw) : undefined,
      headers: Object.fromEntries(new Headers(init?.headers).entries()),
    });
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

const ORG = "/api/v1/orgs/acme";
const STEP_UP: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify a code first." } };
const STEPPED_UP: Reply = { status: 200, body: { aal: 2 } };
const page = <T,>(results: T[]) => ({ status: 200, body: { results, next_cursor: null, has_more: false } });

const ADMIN = [
  "platform.organization.view",
  "platform.organization.update",
  "platform.member.view",
  "platform.member.invite",
  "platform.member.update",
  "platform.member.remove",
  "platform.member.sign_out",
  "platform.role.assign",
  "platform.seat.assign",
  "platform.audit.view",
  "platform.security.view",
  "platform.security.update",
];
const OWNER_BILLING = [
  "commercial.subscription.view",
  "commercial.subscription.manage",
  "platform.billing.view",
  "platform.billing.manage",
];

function access(permissions: string[]): ConsoleAccess {
  return {
    organization: {
      organization_id: "o1",
      slug: "acme",
      display_name: "Acme Ltd",
      status: "active",
      write_allowed: true,
    },
    membership: { membership_id: "m-me", is_owner: false, roles: ["admin"] },
    permissions,
    console: "admin",
  };
}

function renderOrg(ui: React.ReactNode, permissions: string[]) {
  const reload = vi.fn();
  const result = render(
    <OrgAccessContext.Provider value={{ slug: "acme", access: access(permissions), reload }}>
      <main>{ui}</main>
    </OrgAccessContext.Provider>,
  );
  return { ...result, reload };
}

async function stepUpWith(code = "123456") {
  const input = await screen.findByLabelText(/recovery code/);
  fireEvent.change(input, { target: { value: code } });
  fireEvent.click(screen.getByRole("button", { name: "Verify" }));
}

const posted = (calls: Call[], key: string) => calls.filter((c) => c.key === key);

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  nav.router.push.mockReset();
  nav.search = new URLSearchParams();
});

// --- dashboard -----------------------------------------------------------------------------

const DASHBOARD = {
  window_days: 7,
  generated_at: "2026-09-25T10:00:00Z",
  organization: access([]).organization,
  members: {
    active_members: 12,
    suspended_members: 1,
    invited_members: 2,
    members_added: 3,
    pending_invitations: 4,
    expired_invitations: 5,
    invitations_accepted: 6,
  },
  activity: {
    series: [
      { date: "2026-09-19", members: 2, sign_ins: 3 },
      { date: "2026-09-20", members: 0, sign_ins: 0 },
      { date: "2026-09-21", members: 5, sign_ins: 8 },
    ],
    most_active: [
      {
        membership_id: "m2",
        email: "bob@acme.test",
        name: "Bob Jones",
        sign_ins: 9,
        last_sign_in_at: "2026-09-24T09:00:00Z",
      },
    ],
  },
  apps: { entitled: 4 },
  subscription: {
    status: "active",
    billing_interval: "semi_annual",
    current_period_end: "2027-01-01T00:00:00Z",
    trial_ends_at: null,
    products: ["books", "desk"],
  },
};

function tileValue(label: string): string {
  return screen.getByText(label).nextElementSibling?.textContent ?? "";
}

describe("the dashboard", () => {
  it("shows tiles, the sign-in chart, the most active members and the subscription", async () => {
    mockApi({ [`GET ${ORG}/dashboard`]: [{ status: 200, body: DASHBOARD }] });
    const { container } = renderOrg(<AdminInsights />, ["platform.member.invite", "platform.seat.assign"]);
    await screen.findByText("Members who signed in, per day", { selector: "figcaption" });
    expect(tileValue("Users")).toBe("12");
    expect(tileValue("Added in last 7 days")).toBe("3");
    expect(tileValue("Invited – not joined yet")).toBe("6");
    expect(tileValue("Expired invitations")).toBe("5");
    expect(tileValue("Suspended")).toBe("1");
    expect(tileValue("Apps")).toBe("4");
    const top = screen.getByRole("table", { name: "Last 7 days" });
    expect(within(top).getByText("Bob Jones")).toBeInTheDocument();
    expect(within(top).getByText("9")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Subscription" })).toBeInTheDocument();
    expect(screen.getByText("Bi-annual")).toBeInTheDocument();
    expect(screen.getByText("books, desk")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Invite members" })).toHaveAttribute("href", "/o/acme/settings/invitations");
    expect(screen.getByRole("link", { name: "Manage product access" })).toHaveAttribute("href", "/o/acme/settings/products");
    await expectNoViolations(container);
  });

  it("does not show sections the server left out, nor actions the member cannot use", async () => {
    mockApi({
      [`GET ${ORG}/dashboard`]: [
        { status: 200, body: { window_days: 7, generated_at: DASHBOARD.generated_at, organization: DASHBOARD.organization } },
      ],
    });
    const { container } = renderOrg(<AdminInsights />, []);
    await screen.findByText(/nothing to show here/);
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
    expect(screen.queryByText("Apps")).not.toBeInTheDocument();
    expect(screen.queryByText("Members who signed in, per day")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Subscription" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Invite members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Manage product access" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says 'No records found' when nobody signed in, and changes the period on request", async () => {
    const quiet = {
      ...DASHBOARD,
      apps: undefined,
      subscription: undefined,
      activity: { series: DASHBOARD.activity.series.map((s) => ({ ...s, members: 0 })), most_active: [] },
    };
    const calls = mockApi({ [`GET ${ORG}/dashboard`]: [{ status: 200, body: quiet }] });
    const { container } = renderOrg(<AdminInsights />, []);
    expect(await screen.findByText("No records found")).toBeInTheDocument();
    expect(screen.queryByText("Apps")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "true");
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("window=30"))).toBe(true));
    expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "false");
  });
});

// --- members -----------------------------------------------------------------------------------

function member(id: string, name: string, email: string, extra: Record<string, unknown> = {}) {
  return {
    membership_id: id,
    user_id: `u-${id}`,
    email,
    name,
    status: "active",
    is_owner: false,
    member_type: "standard",
    joined_at: "2026-01-02T00:00:00Z",
    added_at: "2026-01-02T00:00:00Z",
    last_sign_in_at: null,
    roles: ["member"],
    ...extra,
  };
}

const MEMBERS = [
  member("m-owner", "Olive Owner", "olive@acme.test", { is_owner: true, roles: ["owner"] }),
  member("m-me", "Me Admin", "me@acme.test", { roles: ["admin"], last_sign_in_at: "2026-09-24T09:00:00Z" }),
  member("m2", "Bob Jones", "bob@acme.test"),
  member("m3", "", "sue@acme.test", { status: "suspended", roles: ["readonly"] }),
];

describe("members", () => {
  it("never offers actions on the owner or on yourself, and labels roles for people", async () => {
    mockApi({ [`GET ${ORG}/members`]: [page(MEMBERS)] });
    const { container } = renderOrg(<MembersScreen />, ADMIN);
    const table = await screen.findByRole("table", { name: "Organization members" });
    for (const who of ["Olive Owner", "Me Admin"]) {
      expect(screen.queryByRole("button", { name: new RegExp(who) })).not.toBeInTheDocument();
      expect(screen.queryByLabelText(`Role for ${who}`)).not.toBeInTheDocument();
    }
    expect(within(table).getByText("Owner", { selector: "td" })).toBeInTheDocument();
    expect(within(table).getByText("Administrator", { selector: "td" })).toBeInTheDocument();
    expect(within(table).getByText("Read Only", { selector: "td" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Suspend Bob Jones" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reactivate sue@acme.test" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend sue@acme.test" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Invitations" })).toHaveAttribute("href", "/o/acme/settings/invitations");
    await expectNoViolations(container);
  });

  it("shows no actions at all to someone who may only view members", async () => {
    mockApi({ [`GET ${ORG}/members`]: [page(MEMBERS)] });
    renderOrg(<MembersScreen />, ["platform.member.view"]);
    await screen.findByRole("table", { name: "Organization members" });
    expect(screen.queryAllByRole("button", { name: /Suspend|Reactivate|Remove|Sign out|Save role/ })).toEqual([]);
    expect(screen.queryByRole("link", { name: "Invitations" })).not.toBeInTheDocument();
  });

  it("suspend, reactivate, sign out and remove each call their own endpoint", async () => {
    const calls = mockApi({
      [`GET ${ORG}/members`]: [page(MEMBERS)],
      [`POST ${ORG}/members/m2/suspend`]: [{ status: 200, body: {} }],
      [`POST ${ORG}/members/m3/reactivate`]: [{ status: 200, body: {} }],
      [`POST ${ORG}/members/m2/sign-out`]: [{ status: 200, body: {} }],
      [`DELETE ${ORG}/memberships/m2`]: [{ status: 200, body: { ok: true } }],
    });
    renderOrg(<MembersScreen />, ADMIN);

    fireEvent.click(await screen.findByRole("button", { name: "Suspend Bob Jones" }));
    expect(await screen.findByText(/Bob Jones is suspended/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Reactivate sue@acme.test" }));
    expect(await screen.findByText(/sue@acme.test is active again/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Sign out of this organization: Bob Jones" }));
    expect(await screen.findByText(/signed out of this organization/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Remove Bob Jones" }));
    const confirm = screen.getByRole("button", { name: "Remove member" });
    expect(confirm).toHaveFocus();
    expect(posted(calls, `DELETE ${ORG}/memberships/m2`)).toHaveLength(0);
    fireEvent.click(confirm);
    expect(await screen.findByText(/was removed from the organization/)).toBeInTheDocument();

    expect(posted(calls, `POST ${ORG}/members/m2/suspend`)).toHaveLength(1);
    expect(posted(calls, `POST ${ORG}/members/m3/reactivate`)).toHaveLength(1);
    expect(posted(calls, `POST ${ORG}/members/m2/sign-out`)).toHaveLength(1);
    expect(posted(calls, `DELETE ${ORG}/memberships/m2`)).toHaveLength(1);
  });

  it("a role change asks for a code when the server wants one, then retries the same change", async () => {
    const calls = mockApi({
      [`GET ${ORG}/members`]: [page(MEMBERS)],
      [`POST ${ORG}/members/m2/role`]: [STEP_UP, { status: 200, body: {} }],
      "POST /api/v1/auth/mfa/step-up": [STEPPED_UP],
    });
    const { container } = renderOrg(<MembersScreen />, ADMIN);
    const select = await screen.findByLabelText("Role for Bob Jones");
    const save = screen.getByRole("button", { name: "Save role for Bob Jones" });
    expect(save).toBeDisabled();
    fireEvent.change(select, { target: { value: "readonly" } });
    fireEvent.click(save);
    await screen.findByLabelText(/recovery code/);
    await expectNoViolations(container);
    await stepUpWith();
    expect(await screen.findByText("Bob Jones is now Read Only.")).toBeInTheDocument();
    const roleCalls = posted(calls, `POST ${ORG}/members/m2/role`);
    expect(roleCalls.map((c) => c.body)).toEqual([{ role: "readonly" }, { role: "readonly" }]);
  });

  it("searches on submit and filters by status", async () => {
    const calls = mockApi({ [`GET ${ORG}/members`]: [page([])] });
    renderOrg(<MembersScreen />, ADMIN);
    await screen.findByText("No members yet.");
    fireEvent.change(screen.getByLabelText("Search by name or email"), { target: { value: "bob" } });
    const before = calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.length).toBe(before + 1));
    expect(calls.at(-1)!.url).toContain("q=bob");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "suspended" } });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("status=suspended"));
  });
});

// --- invitations -------------------------------------------------------------------------------------

const INVITES = [
  {
    invitation_id: "i1",
    email: "new@acme.test",
    role: "member",
    state: "pending",
    invited_by: "me@acme.test",
    created_at: "2026-09-20T00:00:00Z",
    expires_at: "2026-09-27T00:00:00Z",
    last_sent_at: "2026-09-21T00:00:00Z",
    send_count: 2,
    accepted_at: null,
  },
  {
    invitation_id: "i2",
    email: "old@acme.test",
    role: "billing_manager",
    state: "accepted",
    invited_by: null,
    created_at: "2026-08-01T00:00:00Z",
    expires_at: "2026-08-08T00:00:00Z",
    last_sent_at: null,
    send_count: 1,
    accepted_at: "2026-08-02T00:00:00Z",
  },
];

describe("invitations", () => {
  const roleOptions = () =>
    within(screen.getByLabelText("Role")).getAllByRole("option").map((o) => o.textContent);

  it("offers administrator and billing manager only to someone who may assign roles", async () => {
    mockApi({ [`GET ${ORG}/invitations`]: [page(INVITES)] });
    const { unmount } = renderOrg(<InvitationsScreen />, ["platform.member.invite", "platform.member.view"]);
    await screen.findByRole("table", { name: "Invitations" });
    expect(roleOptions()).toEqual(["Member", "Read Only"]);
    unmount();

    mockApi({ [`GET ${ORG}/invitations`]: [page(INVITES)] });
    const { container } = renderOrg(<InvitationsScreen />, ["platform.member.invite", "platform.role.assign"]);
    await screen.findByRole("table", { name: "Invitations" });
    expect(roleOptions()).toEqual(["Administrator", "Billing Manager", "Member", "Read Only"]);
    await expectNoViolations(container);
  });

  it("sends an email and a role, and offers resend and revoke only on open invitations", async () => {
    const calls = mockApi({
      [`GET ${ORG}/invitations`]: [page(INVITES)],
      [`POST ${ORG}/invitations`]: [{ status: 201, body: INVITES[0] }],
      [`POST ${ORG}/invitations/i1/resend`]: [{ status: 200, body: INVITES[0] }],
      [`POST ${ORG}/invitations/i1/revoke`]: [{ status: 200, body: { ...INVITES[0], state: "revoked" } }],
    });
    renderOrg(<InvitationsScreen />, ["platform.member.invite"]);
    await screen.findByRole("table", { name: "Invitations" });
    expect(screen.getByText("Billing Manager")).toBeInTheDocument();
    expect(screen.getByText("Sent 2 times")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resend invitation to old@acme.test" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: " x@acme.test " } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "readonly" } });
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));
    expect(await screen.findByText("Invitation sent to x@acme.test.")).toBeInTheDocument();
    expect(posted(calls, `POST ${ORG}/invitations`)[0].body).toEqual({ email: "x@acme.test", role: "readonly" });

    fireEvent.click(screen.getByRole("button", { name: "Resend invitation to new@acme.test" }));
    expect(await screen.findByText(/previous link no longer works/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revoke invitation to new@acme.test" }));
    expect(await screen.findByText(/was revoked/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "all" } });
    await waitFor(() => expect(calls.at(-1)!.url).toContain("state=all"));
  });
});

// --- organization profile ---------------------------------------------------------------------------

describe("organization profile", () => {
  it("renames and reloads the shell's access so the header shows the new name", async () => {
    const calls = mockApi({
      [`PATCH ${ORG}/organization`]: [{ status: 200, body: { ...access([]).organization, display_name: "Acme Group" } }],
    });
    const { container, reload } = renderOrg(<OrganizationScreen />, ["platform.organization.update"]);
    expect(screen.getByText("/o/acme")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save name" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Organization name"), { target: { value: "Acme Group " } });
    fireEvent.click(save);
    expect(await screen.findByText("The organization is now called Acme Group.")).toBeInTheDocument();
    expect(posted(calls, `PATCH ${ORG}/organization`)[0].body).toEqual({ display_name: "Acme Group" });
    expect(reload).toHaveBeenCalled();
    await expectNoViolations(container);
  });

  it("is read-only without the update permission", () => {
    mockApi({});
    renderOrg(<OrganizationScreen />, []);
    expect(screen.getByText("Acme Ltd")).toBeInTheDocument();
    expect(screen.queryByLabelText("Organization name")).not.toBeInTheDocument();
  });
});

// --- billing ------------------------------------------------------------------------------------------

const ATTEMPT = "5b2a3c4d-1111-4222-8333-444455556666";
const INVOICE = {
  invoice_id: "inv1",
  number: "INV-0001",
  status: "open",
  currency: "GBP",
  total_minor: 12000,
  amount_paid_minor: 0,
  amount_refunded_minor: 0,
  amount_due_minor: 12000,
  billing_interval: "monthly",
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  issued_at: "2026-09-01T00:00:00Z",
  due_at: "2026-09-15T00:00:00Z",
  paid_at: null,
  dunning_stage: null,
  version: 1,
};
const PAID_INVOICE = { ...INVOICE, invoice_id: "inv0", number: "INV-0000", status: "paid", amount_due_minor: 0, amount_paid_minor: 12000, paid_at: "2026-08-10T00:00:00Z" };

function attempt(status: string, checkout_url: string | null = null) {
  return {
    attempt_id: ATTEMPT,
    invoice_id: "inv1",
    status,
    amount_minor: 12000,
    currency: "GBP",
    provider: "test",
    checkout_url,
    failure_code: null,
    expires_at: "2026-09-25T11:00:00Z",
  };
}

function billingRoutes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    [`GET ${ORG}/subscriptions`]: [
      {
        status: 200,
        body: {
          results: [
            {
              id: "s1",
              status: "active",
              currency: "GBP",
              billing_interval: "monthly",
              renewal_policy: "auto_renew",
              current_period_start: "2026-09-01T00:00:00Z",
              current_period_end: "2026-10-01T00:00:00Z",
              trial_ends_at: null,
              cancel_at: null,
              version: 3,
              items: [
                { id: "it1", product_code: "books", plan_code: "books-pro", bundle_code: "", quantity: 5, added_at: "2026-09-01T00:00:00Z", removed_at: null },
                { id: "it2", product_code: "old", plan_code: "old-plan", bundle_code: "", quantity: 1, added_at: "2026-01-01T00:00:00Z", removed_at: "2026-02-01T00:00:00Z" },
              ],
            },
          ],
        },
      },
    ],
    [`GET ${ORG}/billing-account`]: [
      {
        status: 200,
        body: { billing_account: { id: "b1", currency: "GBP", collection_mode: "automatic", billing_email: "ap@acme.test", billing_region: "GB" } },
      },
    ],
    [`GET ${ORG}/billing/invoices`]: [{ status: 200, body: { results: [INVOICE, PAID_INVOICE] } }],
    ...extra,
  };
}

describe("subscription & billing", () => {
  let assign: ReturnType<typeof vi.fn>;
  const originalAssign = checkoutNavigation.assign;

  beforeEach(() => {
    PAYMENT_POLL.intervalMs = 5;
    PAYMENT_POLL.timeoutMs = 3000;
    assign = vi.fn();
    checkoutNavigation.assign = assign as unknown as (url: string) => void;
  });
  afterEach(() => {
    checkoutNavigation.assign = originalAssign;
  });

  it("shows the subscription, the account and the invoices, with Pay only on an open invoice", async () => {
    mockApi(billingRoutes());
    const { container } = renderOrg(<BillingScreen />, OWNER_BILLING);
    await screen.findByRole("table", { name: "Your invoices, newest first" });
    await screen.findByRole("table", { name: "Products in this subscription" });
    expect(screen.getByRole("heading", { level: 1, name: "Subscription & billing" })).toBeInTheDocument();
    expect(screen.getByText("books-pro")).toBeInTheDocument();
    expect(screen.queryByText("old-plan")).not.toBeInTheDocument();
    expect(screen.getByText("ap@acme.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pay invoice INV-0001" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pay invoice INV-0000" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("cancels at the end of the period after a confirm: one key, the shown version, never immediate (Phase 12)", async () => {
    const routes = billingRoutes({
      [`POST ${ORG}/subscriptions/s1/cancel`]: [
        { status: 200, body: { id: "s1", status: "cancelled", cancel_at: "2026-10-01T00:00:00Z" } },
      ],
    });
    const cancelled = structuredClone(routes[`GET ${ORG}/subscriptions`][0]) as Reply;
    const row = (cancelled.body as { results: Record<string, unknown>[] }).results[0];
    row.status = "cancelled";
    row.cancel_at = "2026-10-01T00:00:00Z";
    routes[`GET ${ORG}/subscriptions`].push(cancelled);
    const calls = mockApi(routes);
    const { container } = renderOrg(<BillingScreen />, OWNER_BILLING);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel subscription" }));
    expect(screen.getByText(/stay available until/)).toBeInTheDocument();
    expect(posted(calls, `POST ${ORG}/subscriptions/s1/cancel`)).toHaveLength(0); // nothing before the confirm
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel at the end of the period" }));
    expect(await screen.findByText(/Subscription cancelled\./)).toBeInTheDocument();
    const sent = posted(calls, `POST ${ORG}/subscriptions/s1/cancel`);
    expect(sent).toHaveLength(1);
    expect(sent[0].headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(sent[0].body).toEqual({ expected_version: 3 });
    expect(await screen.findByText("Ends on")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel subscription" })).not.toBeInTheDocument();
  });

  it("offers no cancel to someone who may see the subscription but not manage it (Phase 12)", async () => {
    mockApi(billingRoutes());
    renderOrg(<BillingScreen />, ["commercial.subscription.view"]);
    await screen.findByRole("table", { name: "Products in this subscription" });
    expect(screen.queryByRole("button", { name: "Cancel subscription" })).not.toBeInTheDocument();
  });

  it("hides Pay from someone who may view but not pay, and each section from those who may not see it", async () => {
    mockApi(billingRoutes());
    renderOrg(<BillingScreen />, ["platform.billing.view"]);
    await screen.findByRole("table", { name: "Your invoices, newest first" });
    expect(screen.queryByRole("button", { name: /^Pay invoice/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Subscription" })).not.toBeInTheDocument();
  });

  it("pays: one idempotency key, waits for the hosted page, and goes there only when it is HTTPS", async () => {
    const calls = mockApi(
      billingRoutes({
        [`POST ${ORG}/billing/invoices/inv1/pay`]: [{ status: 202, body: attempt("pending") }],
        [`GET ${ORG}/billing/payments/${ATTEMPT}`]: [
          { status: 200, body: attempt("pending") },
          { status: 200, body: attempt("awaiting_payment", "https://pay.example.com/c/abc") },
        ],
      }),
    );
    renderOrg(<BillingScreen />, OWNER_BILLING);
    fireEvent.click(await screen.findByRole("button", { name: "Pay invoice INV-0001" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://pay.example.com/c/abc"));
    const pay = posted(calls, `POST ${ORG}/billing/invoices/inv1/pay`);
    expect(pay).toHaveLength(1);
    expect(pay[0].headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(pay[0].body).toBeUndefined();
    expect(posted(calls, `GET ${ORG}/billing/payments/${ATTEMPT}`).length).toBeGreaterThanOrEqual(2);
  });

  it("refuses to follow a checkout address that is neither HTTPS nor this app's own", async () => {
    mockApi(
      billingRoutes({
        [`POST ${ORG}/billing/invoices/inv1/pay`]: [{ status: 202, body: attempt("awaiting_payment", "http://evil.example/steal") }],
      }),
    );
    const { container } = renderOrg(<BillingScreen />, OWNER_BILLING);
    fireEvent.click(await screen.findByRole("button", { name: "Pay invoice INV-0001" }));
    expect(await screen.findByText(/could not be verified/)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    await expectNoViolations(container);
  });

  it("retries a payment that needed a code with the same idempotency key", async () => {
    const calls = mockApi(
      billingRoutes({
        [`POST ${ORG}/billing/invoices/inv1/pay`]: [STEP_UP, { status: 202, body: attempt("awaiting_payment", "https://pay.example.com/c/1") }],
        "POST /api/v1/auth/mfa/step-up": [STEPPED_UP],
      }),
    );
    renderOrg(<BillingScreen />, OWNER_BILLING);
    fireEvent.click(await screen.findByRole("button", { name: "Pay invoice INV-0001" }));
    await stepUpWith();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://pay.example.com/c/1"));
    const keys = posted(calls, `POST ${ORG}/billing/invoices/inv1/pay`).map((c) => c.headers["idempotency-key"]);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("a failed attempt is reported, and the next Pay is a new command", async () => {
    const failed = { ...attempt("failed"), failure_code: "checkout_unavailable" };
    const calls = mockApi(
      billingRoutes({
        [`POST ${ORG}/billing/invoices/inv1/pay`]: [
          { status: 202, body: failed },
          { status: 202, body: attempt("awaiting_payment", "https://pay.example.com/c/2") },
        ],
      }),
    );
    renderOrg(<BillingScreen />, OWNER_BILLING);
    fireEvent.click(await screen.findByRole("button", { name: "Pay invoice INV-0001" }));
    expect(await screen.findByText(/couldn't be completed \(checkout unavailable\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pay invoice INV-0001" }));
    await waitFor(() => expect(assign).toHaveBeenCalled());
    const keys = posted(calls, `POST ${ORG}/billing/invoices/inv1/pay`).map((c) => c.headers["idempotency-key"]);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("coming back from the provider, asks the server whether the payment arrived", async () => {
    nav.search = new URLSearchParams(`payment=${ATTEMPT}&outcome=success`);
    mockApi(
      billingRoutes({
        [`GET ${ORG}/billing/payments/${ATTEMPT}`]: [
          { status: 200, body: attempt("awaiting_payment", "https://pay.example.com/c/1") },
          { status: 200, body: attempt("succeeded") },
        ],
      }),
    );
    const { container } = renderOrg(<BillingScreen />, OWNER_BILLING);
    expect(await screen.findByText(/Payment received/)).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    await expectNoViolations(container);
  });

  it("a cancelled return says so without asking the server anything", async () => {
    nav.search = new URLSearchParams(`payment=${ATTEMPT}&outcome=cancelled`);
    const calls = mockApi(billingRoutes());
    renderOrg(<BillingScreen />, OWNER_BILLING);
    expect(await screen.findByText(/Payment cancelled/)).toBeInTheDocument();
    await screen.findByRole("table", { name: "Your invoices, newest first" });
    expect(calls.some((c) => c.key.includes("/billing/payments/"))).toBe(false);
  });

  it("ignores a return parameter that is not an attempt id", async () => {
    nav.search = new URLSearchParams("payment=../../admin&outcome=success");
    const calls = mockApi(billingRoutes());
    renderOrg(<BillingScreen />, OWNER_BILLING);
    await screen.findByRole("table", { name: "Your invoices, newest first" });
    expect(screen.queryByText(/Checking your payment/)).not.toBeInTheDocument();
    expect(calls.some((c) => c.key.includes("/billing/payments/"))).toBe(false);
  });

  it("opens a billing account with a chosen currency", async () => {
    const calls = mockApi(
      billingRoutes({
        [`GET ${ORG}/billing-account`]: [{ status: 200, body: { billing_account: null } }],
        [`POST ${ORG}/billing-account`]: [{ status: 200, body: { id: "b1", currency: "JPY", collection_mode: "automatic" } }],
        [`GET ${ORG}/billing/invoices`]: [{ status: 200, body: { results: [] } }],
      }),
    );
    const { container } = renderOrg(<BillingScreen />, OWNER_BILLING);
    const currency = await screen.findByLabelText("Currency");
    expect(within(currency).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a currency",
      "AED",
      "AUD",
      "CAD",
      "EUR",
      "GBP",
      "INR",
      "JPY",
      "USD",
    ]);
    await expectNoViolations(container);
    const open = screen.getByRole("button", { name: "Open billing account" });
    expect(open).toBeDisabled();
    fireEvent.change(currency, { target: { value: "JPY" } });
    fireEvent.change(screen.getByLabelText("Billing email (optional)"), { target: { value: "ap@acme.test" } });
    // Phase 9: no region field - collection mode and region are Workeasy360's.
    expect(screen.queryByLabelText("Billing region (optional)")).not.toBeInTheDocument();
    fireEvent.click(open);
    expect(await screen.findByText(/billed in JPY/)).toBeInTheDocument();
    expect(posted(calls, `POST ${ORG}/billing-account`)[0].body).toEqual({
      currency: "JPY",
      billing_email: "ap@acme.test",
    });
  });

  it("shows an invoice's lines on request", async () => {
    mockApi(
      billingRoutes({
        [`GET ${ORG}/billing/invoices/inv1`]: [
          {
            status: 200,
            body: {
              ...INVOICE,
              lines: [{ description: "Books Pro × 5", product_code: "books", plan_code: "books-pro", bundle_code: "", quantity: 5, amount_minor: 12000 }],
              attempts: [],
            },
          },
        ],
      }),
    );
    const { container } = renderOrg(<BillingScreen />, ["platform.billing.view"]);
    const toggle = await screen.findByRole("button", { name: "Details of invoice INV-0001" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(await screen.findByRole("table", { name: "Lines on invoice INV-0001" })).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Books Pro × 5")).toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("checkout address check", () => {
  const here = "http://localhost:3000/o/acme/settings/billing";
  it("allows HTTPS anywhere and this app's own origin, nothing else", () => {
    expect(safeCheckoutUrl("https://pay.example.com/c/1", here)).toBe("https://pay.example.com/c/1");
    expect(safeCheckoutUrl("http://localhost:3000/billing/test-checkout#reference=r&org=o", here)).toBe(
      "http://localhost:3000/billing/test-checkout#reference=r&org=o",
    );
    expect(safeCheckoutUrl("/billing/test-checkout#reference=r", here)).toBe(
      "http://localhost:3000/billing/test-checkout#reference=r",
    );
    for (const bad of [
      "http://evil.example/pay",
      "//evil.example/pay",
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "http://localhost:3001/x",
      "",
      null,
    ]) {
      expect(safeCheckoutUrl(bad, here), String(bad)).toBeNull();
    }
  });
});

// --- marketplace --------------------------------------------------------------------------------------

const HOSTILE = `Plain **bold** and *soft*.

<img src=x onerror=alert(1)> read the [docs](https://example.com/docs) or [this](javascript:alert(1)).

- first point
- second point`;

function offers(overrides: Record<string, unknown> = {}) {
  return {
    currency: "GBP",
    billing_account: true,
    held_products: ["desk"],
    plans: [
      {
        plan_version_id: "pv1",
        plan_code: "books-pro",
        name: "Books Pro",
        caption: "For growing teams",
        description: HOSTILE,
        billing_type: "paid",
        kind: "standard",
        addon_for: null,
        product_code: "books",
        product_name: "Books",
        included_seats: 5,
        max_seats: null,
        trial_days: 0,
        prices: [
          { interval: "monthly", currency: "GBP", model: "per_seat", amount_minor: 1200, from_tiers: false },
          { interval: "annual", currency: "GBP", model: "per_seat", amount_minor: 12000, from_tiers: false },
        ],
      },
      {
        plan_version_id: "pv2",
        plan_code: "desk-basic",
        name: "Desk Basic",
        caption: "",
        description: "",
        billing_type: "free_trial",
        kind: "standard",
        addon_for: null,
        product_code: "desk",
        product_name: "Desk",
        included_seats: 1,
        max_seats: null,
        trial_days: 14,
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 900, from_tiers: false }],
      },
      {
        plan_version_id: "pv3",
        plan_code: "books-storage",
        name: "Extra storage",
        caption: "More room",
        description: "",
        billing_type: "free_trial",
        kind: "add_on",
        addon_for: "books-pro",
        product_code: "books",
        product_name: "Books",
        included_seats: 0,
        max_seats: null,
        trial_days: 30,
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 300, from_tiers: false }],
      },
    ],
    bundles: [
      {
        bundle_version_id: "bv1",
        bundle_code: "suite",
        name: "Suite",
        caption: "Everything together",
        description: "- Books\n- Desk",
        billing_type: "paid",
        trial_days: 0,
        includes: [
          { product_code: "books", product_name: "Books", plan_name: "Books Pro", quantity: 1 },
          { product_code: "desk", product_name: "Desk", plan_name: "Desk Basic", quantity: 1 },
        ],
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 2500, from_tiers: false }],
      },
    ],
    ...overrides,
  };
}

function card(name: string): HTMLElement {
  return screen.getByRole("article", { name });
}

/** Phase 11: an empty `explore` response, so the plan/bundle-focused tests
 * below (which predate the Products section) see it in its "nothing new"
 * state and their own assertions are undisturbed. */
function explore(overrides: Record<string, unknown> = {}) {
  return { currency: "GBP", billing_account: true, products: [], bundles: [], ...overrides };
}

function exploreProduct(code: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    code,
    slug: code.replace(/_/g, "-"),
    name,
    tagline: `${name} for growing teams`,
    description: `${name} does useful things.`,
    availability: "available",
    category: "Sales",
    icon: "customers",
    features: [],
    plans: [
      {
        plan_version_id: "epv1",
        plan_code: `${code}_starter`,
        name: `${name} Starter`,
        caption: "",
        description: "",
        billing_type: "paid",
        kind: "standard",
        addon_for: null,
        product_code: code,
        product_name: name,
        included_seats: 3,
        max_seats: null,
        trial_days: 0,
        prices: [{ interval: "monthly", currency: "GBP", model: "flat", amount_minor: 1900, from_tiers: false }],
      },
    ],
    starting_price: { amount_minor: 1900, currency: "GBP", interval: "monthly" },
    trial: { available: false, duration_days: null, seat_limit: null, payment_method_required: false, plan_codes: [] },
    holding: null,
    trial_state: "available",
    trial_ends_at: null,
    trial_days_left: null,
    trial_outcome: null,
    ...overrides,
  };
}

describe("explore products", () => {
  it("renders descriptions safely: a little Markdown, and hostile markup as text", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers() }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore() }],
    });
    const { container } = renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Plans" });
    const books = card("Books Pro");
    expect(books.querySelector("img")).toBeNull();
    expect(books.querySelector("[onerror]")).toBeNull();
    expect(books.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(within(books).getByText("bold").tagName).toBe("STRONG");
    expect(within(books).getByText("soft").tagName).toBe("EM");
    const docs = within(books).getByRole("link", { name: "docs" });
    expect(docs).toHaveAttribute("href", "https://example.com/docs");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(books).queryByRole("link", { name: "this" })).not.toBeInTheDocument();
    expect(books.textContent).toContain("[this](javascript:alert(1))");
    expect(within(books).getAllByRole("listitem").map((li) => li.textContent)).toEqual(
      expect.arrayContaining(["first point", "second point"]),
    );
    await expectNoViolations(container);
  });

  it("offers only the billing cycles something is priced for, and prices follow the cycle", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers() }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore() }],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    const group = await screen.findByRole("group", { name: "Billing cycle" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Monthly", "Annual"]);
    expect(within(card("Books Pro")).getByText("£12.00")).toBeInTheDocument();
    fireEvent.click(within(group).getByRole("button", { name: "Annual" }));
    expect(within(group).getByRole("button", { name: "Annual" })).toHaveAttribute("aria-pressed", "true");
    expect(within(card("Books Pro")).getByText("£120.00")).toBeInTheDocument();
    expect(within(card("Suite")).getByText("Price on request")).toBeInTheDocument();
    expect(within(card("Suite")).getByRole("button", { name: "Buy: Suite" })).toBeDisabled();
  });

  it("shows trial, add-on and included badges, and never offers what the organization already holds", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers() }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore() }],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Add-ons" });
    expect(within(card("Desk Basic")).getByText("Free trial · 14 days")).toBeInTheDocument();
    expect(within(card("Desk Basic")).getByText("Purchased")).toBeInTheDocument();
    expect(within(card("Desk Basic")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card("Extra storage")).getByText("Add-on for Books Pro")).toBeInTheDocument();
    expect(within(card("Extra storage")).getByRole("button", { name: "Start free trial: Extra storage" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Product packages" })).toBeInTheDocument();
  });

  it("buying opens the checkout with a selection and a cycle only - never a price, and no purchase yet", async () => {
    const calls = mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers() }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore() }],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    const group = await screen.findByRole("group", { name: "Billing cycle" });
    fireEvent.click(within(group).getByRole("button", { name: "Annual" }));
    fireEvent.click(screen.getByRole("button", { name: "Buy: Books Pro" }));
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/o/acme/checkout?interval=annual&plan=pv1"));
    expect(calls.filter((c) => c.key.startsWith("POST"))).toEqual([]);
  });

  it("without the owner's permission there is nothing to buy; without an account, a way to open one", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ billing_account: false, currency: null }) }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore() }],
    });
    const { container } = renderOrg(<ExploreScreen />, []);
    await screen.findByRole("heading", { name: "Plans" });
    expect(screen.queryAllByRole("button", { name: /^(Buy|Start free trial):/ })).toEqual([]);
    expect(screen.getByRole("link", { name: "set them up now" })).toHaveAttribute("href", "/o/acme/settings/billing");
    expect(screen.getByText(/Only the organization owner can buy/)).toBeInTheDocument();
    await expectNoViolations(container);
  });
});

// --- Phase 11: the Products section of Explore ----------------------------------------------

describe("explore: products (Phase 11)", () => {
  it("shows what the server says about each product - buy, trial or nothing - and passes axe", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("crm", "Easy CRM", {
                trial_state: "available",
                trial: { available: true, duration_days: 14, seat_limit: null, payment_method_required: false, plan_codes: ["crm_starter"] },
              }),
              exploreProduct("easybook", "EasyBook", { trial_state: "unavailable" }),
              exploreProduct("desk", "Easy Help Desk", { trial_state: "owned" }),
            ],
          }),
        },
      ],
    });
    const { container } = renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Products" });

    // A trial-eligible product: its trial, with the length the server
    // states, then Buy now and Learn more (multi-portal brief, Part 12).
    const crm = card("Easy CRM");
    expect(within(crm).getByText("14-day free trial")).toBeInTheDocument();
    expect(within(crm).getByRole("button", { name: "Start 14-day free trial: Easy CRM" })).toHaveTextContent(
      "Start 14-Day Free Trial",
    );
    expect(within(crm).getByRole("button", { name: "Buy now: Easy CRM" })).toBeEnabled();
    expect(within(crm).getByRole("link", { name: "Learn more: Easy CRM" })).toHaveAttribute("href", "/products/crm");

    // No trial configured, but priced: Buy now, no trial.
    const book = card("EasyBook");
    expect(within(book).getByRole("button", { name: "Buy now: EasyBook" })).toBeEnabled();
    expect(within(book).queryByRole("button", { name: /free trial/ })).not.toBeInTheDocument();

    // Already owned: shown, with Open and no purchase action at all.
    const desk = card("Easy Help Desk");
    expect(within(desk).getByText("Active")).toBeInTheDocument();
    expect(within(desk).getByRole("button", { name: "Open Easy Help Desk" })).toBeEnabled();
    expect(within(desk).queryByRole("button", { name: /Buy|trial/ })).not.toBeInTheDocument();

    await expectNoViolations(container);
  });

  it("starting a trial calls the trials endpoint, never checkout, and reports success", async () => {
    const calls = mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("crm", "Easy CRM", {
                trial_state: "available",
                trial: { available: true, duration_days: 14, seat_limit: null, payment_method_required: false, plan_codes: ["crm_starter"] },
              }),
            ],
          }),
        },
      ],
      [`POST ${ORG}/trials`]: [{ status: 201, body: { id: "t1", product_code: "crm", plan_code: "crm_starter", status: "active" } }],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    fireEvent.click(await screen.findByRole("button", { name: "Start 14-day free trial: Easy CRM" }));

    await screen.findByText("Trial started: Easy CRM.");
    const started = posted(calls, `POST ${ORG}/trials`);
    expect(started).toHaveLength(1);
    expect(started[0].body).toEqual({ product_code: "crm", plan_code: "crm_starter" });
    expect(started[0].headers["idempotency-key"]).toBeTruthy();
    expect(nav.router.push).not.toHaveBeenCalled();
  });

  it("a refused trial shows why, in words, not the raw reason code", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("crm", "Easy CRM", {
                trial_state: "available",
                trial: { available: true, duration_days: 14, seat_limit: null, payment_method_required: false, plan_codes: ["crm_starter"] },
              }),
            ],
          }),
        },
      ],
      [`POST ${ORG}/trials`]: [
        { status: 409, body: { code: "trial_not_allowed", detail: "This trial cannot be started.", reason: "already_used" } },
      ],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    fireEvent.click(await screen.findByRole("button", { name: "Start 14-day free trial: Easy CRM" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your organization has already trialled this product.",
    );
    expect(screen.queryByText("already_used")).not.toBeInTheDocument();
  });

  it("buying a product card opens checkout with its plan and interval - never a price", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        { status: 200, body: explore({ products: [exploreProduct("easybook", "EasyBook", { trial_state: "unavailable" })] }) },
      ],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    fireEvent.click(await screen.findByRole("button", { name: "Buy now: EasyBook" }));
    await waitFor(() =>
      expect(nav.router.push).toHaveBeenCalledWith("/o/acme/checkout?plan=epv1&interval=monthly"),
    );
  });

  it("without the owner's permission, product cards offer nothing to click", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        { status: 200, body: explore({ products: [exploreProduct("crm", "Easy CRM")] }) },
      ],
    });
    renderOrg(<ExploreScreen />, []);
    await screen.findByRole("heading", { name: "Products" });
    expect(within(card("Easy CRM")).queryByRole("button")).not.toBeInTheDocument();
    // Reading about the product is open to every member.
    expect(within(card("Easy CRM")).getAllByRole("link").map((a) => a.textContent)).toEqual(["Learn more"]);
  });

  it("before billing details exist, a product on trial is priced at checkout - never 'Contact sales' (Phase 12)", async () => {
    const unpriced = exploreProduct("crm", "Easy CRM", { trial_state: "active", holding: "trial", starting_price: null });
    unpriced.plans[0].prices = [];
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ billing_account: false }) }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore({ billing_account: false, currency: null, products: [unpriced] }) }],
    });
    const { container } = renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Products" });
    const crm = card("Easy CRM");
    expect(within(crm).getByText("Priced in your billing currency at checkout")).toBeInTheDocument();
    expect(within(crm).queryByText("Contact sales for pricing")).not.toBeInTheDocument();
    // Upgrading goes straight to checkout, which asks for the billing details first.
    fireEvent.click(within(crm).getByRole("button", { name: "Upgrade: Easy CRM" }));
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/o/acme/checkout?plan=epv1&interval=monthly"));
    await expectNoViolations(container);
  });

  it("a new organization (no billing details yet) gets both the 7-day free trial and Buy now on every card", async () => {
    const products = ["easy_crm", "easybook"].map((code) => {
      const product = exploreProduct(code, code === "easy_crm" ? "Easy CRM" : "EasyBook", {
        starting_price: null,
        trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: [`${code}_starter`] },
      });
      product.plans[0].prices = [];
      return product;
    });
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ billing_account: false }) }],
      [`GET ${ORG}/explore`]: [{ status: 200, body: explore({ billing_account: false, currency: null, products }) }],
    });
    const { container } = renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Products" });
    for (const name of ["Easy CRM", "EasyBook"]) {
      const c = card(name);
      expect(within(c).getByText("7-day free trial")).toBeInTheDocument();
      expect(within(c).getByRole("button", { name: `Start 7-day free trial: ${name}` })).toBeEnabled();
      expect(within(c).getByRole("button", { name: `Buy now: ${name}` })).toBeEnabled();
    }
    fireEvent.click(within(card("EasyBook")).getByRole("button", { name: "Buy now: EasyBook" }));
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/o/acme/checkout?plan=epv1&interval=monthly"));
    await expectNoViolations(container);
  });

  it("an active trial shows the server's days remaining, Open and Upgrade", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("crm", "Easy CRM", {
                trial_state: "active",
                holding: "trial",
                trial_days_left: 5,
                trial_ends_at: "2026-10-16T09:00:00+00:00",
              }),
            ],
          }),
        },
      ],
    });
    const { container } = renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Products" });
    const crm = card("Easy CRM");
    expect(within(crm).getByText("Trial active — 5 days remaining")).toBeInTheDocument();
    expect(within(crm).getByRole("button", { name: "Open Easy CRM" })).toBeEnabled();
    expect(within(crm).getByRole("button", { name: "Upgrade: Easy CRM" })).toBeEnabled();
    expect(within(crm).queryByRole("button", { name: /free trial/ })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("an expired trial says so and offers Upgrade / Buy now, never another trial", async () => {
    mockApi({
      [`GET ${ORG}/marketplace`]: [{ status: 200, body: offers({ plans: [], bundles: [] }) }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("crm", "Easy CRM", {
                trial_state: "used",
                trial_outcome: "expired",
                trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: ["crm_starter"] },
              }),
            ],
          }),
        },
      ],
    });
    renderOrg(<ExploreScreen />, ["commercial.subscription.manage"]);
    await screen.findByRole("heading", { name: "Products" });
    const crm = card("Easy CRM");
    expect(within(crm).getByText("Trial expired")).toBeInTheDocument();
    expect(within(crm).getByRole("button", { name: "Upgrade / Buy now: Easy CRM" })).toBeEnabled();
    expect(within(crm).queryByRole("button", { name: /free trial|Open/ })).not.toBeInTheDocument();
  });
});

describe("MarkdownLite", () => {
  it("builds paragraphs, lists and HTTPS links, and nothing else", () => {
    const { container } = render(
      <MarkdownLite text={"One\ntwo\n\n- a\n- **b**\n\n[x](http://plain.example) [y](https://ok.example/p?q=1)\n<script>alert(1)</script>"} />,
    );
    expect(Array.from(container.querySelectorAll("p")).map((p) => p.textContent)).toEqual([
      "One two",
      "[x](http://plain.example) y <script>alert(1)</script>",
    ]);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("li strong")?.textContent).toBe("b");
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://ok.example/p?q=1");
    expect(container.querySelector("script")).toBeNull();
  });

  it("renders nothing for empty text", () => {
    const { container } = render(<MarkdownLite text="   " />);
    expect(container.innerHTML).toBe("");
  });
});

// --- my security -----------------------------------------------------------------------------------------

const TOTP = { factor_id: "f1", kind: "totp", label: "Authenticator app", confirmed: true, created_at: null, last_used_at: null };
const SESSIONS = {
  sessions: [
    {
      session_id: "s1",
      current: true,
      signed_in_at: "2026-09-25T08:00:00Z",
      last_active_at: "2026-09-25T10:00:00Z",
      expires_at: "2026-10-09T08:00:00Z",
      ip_prefix: "203.0.113.0/24",
      device_label: "Chrome on macOS",
    },
    {
      session_id: "s2",
      current: false,
      signed_in_at: "2026-09-20T08:00:00Z",
      last_active_at: "2026-09-21T10:00:00Z",
      expires_at: "2026-10-04T08:00:00Z",
      ip_prefix: "198.51.100.0/24",
      device_label: "Firefox on Windows",
    },
  ],
  aal: 1,
};
const EVENTS = {
  events: [
    { occurred_at: "2026-09-25T08:00:00Z", kind: "sign_in", reason: "", ip_prefix: "203.0.113.0/24", device_label: "Chrome on macOS" },
    { occurred_at: "2026-09-24T08:00:00Z", kind: "sign_in_failed", reason: "invalid_credentials", ip_prefix: "192.0.2.0/24", device_label: "Unknown device" },
    { occurred_at: "2026-09-23T08:00:00Z", kind: "other_sessions_revoked", reason: "", ip_prefix: "", device_label: "" },
  ],
};

function securityRoutes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP] } }],
    "GET /api/v1/auth/sessions": [{ status: 200, body: SESSIONS }],
    "GET /api/v1/auth/activity": [{ status: 200, body: EVENTS }],
    ...extra,
  };
}

describe("my security", () => {
  it("lists sessions (this device marked, not revocable) and recent activity in words", async () => {
    mockApi(securityRoutes());
    const { container } = renderOrg(<MySecurityScreen />, []);
    await screen.findByRole("table", { name: /Your sessions/ });
    await screen.findByText("Failed sign-in attempt (wrong password)");
    expect(screen.getByText("This device")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Sign out Chrome on macOS/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Sign out Firefox on Windows/ })).toBeInTheDocument();
    expect(screen.getByText("All other sessions were signed out")).toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.getByText(/change your password instead/)).toBeInTheDocument();
    await screen.findByRole("button", { name: "Remove Authenticator app" });
    await expectNoViolations(container);
  });

  it("signing a session out asks for a code, then retries", async () => {
    const calls = mockApi(
      securityRoutes({
        "POST /api/v1/auth/sessions/s2/revoke": [STEP_UP, { status: 200, body: { ok: true } }],
        "POST /api/v1/auth/mfa/step-up": [STEPPED_UP],
      }),
    );
    const { container } = renderOrg(<MySecurityScreen />, []);
    fireEvent.click(await screen.findByRole("button", { name: /^Sign out Firefox on Windows/ }));
    await screen.findByLabelText(/recovery code/);
    await expectNoViolations(container);
    await stepUpWith("654321");
    expect(await screen.findByText("Signed out the session on Firefox on Windows.")).toBeInTheDocument();
    expect(posted(calls, "POST /api/v1/auth/sessions/s2/revoke")).toHaveLength(2);
    expect(posted(calls, "POST /api/v1/auth/mfa/step-up")[0].body).toEqual({ code: "654321" });
  });

  it("signs out every other session", async () => {
    const calls = mockApi(
      securityRoutes({ "POST /api/v1/auth/sessions/revoke-others": [{ status: 200, body: { ok: true, ended: 1 } }] }),
    );
    renderOrg(<MySecurityScreen />, []);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out all other sessions" }));
    expect(await screen.findByText("Every other session has been signed out.")).toBeInTheDocument();
    expect(posted(calls, "POST /api/v1/auth/sessions/revoke-others")).toHaveLength(1);
  });

  it("describes every account event kind", () => {
    expect(describeAccountEvent({ kind: "sign_in_failed", reason: "account_locked" })).toBe("Failed sign-in attempt (account locked)");
    expect(describeAccountEvent({ kind: "password_changed", reason: "" })).toBe("Password changed");
    expect(describeAccountEvent({ kind: "session_revoked", reason: "" })).toBe("A session was signed out");
    expect(describeAccountEvent({ kind: "signed_out_everywhere", reason: "" })).toBe("Signed out everywhere");
    expect(describeAccountEvent({ kind: "account_unlocked", reason: "" })).toBe("Account unlocked");
    expect(describeAccountEvent({ kind: "something_new", reason: "" })).toBe("something new");
  });
});

// --- security policy --------------------------------------------------------------------------------------

const POLICY = {
  require_mfa: false,
  session_max_age_minutes: null,
  password_min_length: null,
  ip_allowlist_enabled: false,
  ip_allowlist: [] as string[],
  version: 7,
  updated_at: "2026-09-01T00:00:00Z",
  platform_password_min_length: 12,
};

describe("authentication policy", () => {
  it("saves the policy with its version, turning blank fields into 'no setting'", async () => {
    const calls = mockApi({
      [`GET ${ORG}/security/policy`]: [{ status: 200, body: POLICY }],
      [`PUT ${ORG}/security/policy`]: [{ status: 200, body: { ...POLICY, require_mfa: true, version: 8 } }],
    });
    const { container } = renderOrg(<AuthenticationPolicyScreen />, ADMIN);
    const mfa = await screen.findByLabelText("Require two-step verification");
    await expectNoViolations(container);
    fireEvent.click(mfa);
    fireEvent.change(screen.getByLabelText("Session length"), { target: { value: "480" } });
    fireEvent.change(screen.getByLabelText("Minimum password length"), { target: { value: "8" } });
    expect(screen.getByRole("button", { name: "Save authentication settings" })).toBeDisabled();
    expect(screen.getByText(/Enter a number from 12 to 64/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Minimum password length"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save authentication settings" }));
    expect(await screen.findByText("Authentication settings saved.")).toBeInTheDocument();
    expect(posted(calls, `PUT ${ORG}/security/policy`)[0].body).toEqual({
      require_mfa: true,
      session_max_age_minutes: 480,
      password_min_length: null,
      version: 7,
    });
    expect(screen.getByText(/deliberately not offered/)).toBeInTheDocument();
  });

  it("explains a conflicting save and offers to reload", async () => {
    mockApi({
      [`GET ${ORG}/security/policy`]: [{ status: 200, body: POLICY }],
      [`PUT ${ORG}/security/policy`]: [
        { status: 409, body: { code: "concurrent_modification", detail: "Someone else changed this policy. Reload it and try again." } },
      ],
    });
    const { container } = renderOrg(<AuthenticationPolicyScreen />, ADMIN);
    fireEvent.click(await screen.findByRole("button", { name: "Save authentication settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Someone else changed this policy/);
    expect(screen.getByRole("button", { name: "Reload settings" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("is view-only without the update permission", async () => {
    mockApi({ [`GET ${ORG}/security/policy`]: [{ status: 200, body: { ...POLICY, session_max_age_minutes: 90 } }] });
    renderOrg(<AuthenticationPolicyScreen />, ["platform.security.view"]);
    expect(await screen.findByLabelText("Require two-step verification")).toBeDisabled();
    expect(screen.getByLabelText("Session length")).toHaveValue("90");
    expect(screen.queryByRole("button", { name: "Save authentication settings" })).not.toBeInTheDocument();
  });
});

describe("network access", () => {
  const NETWORK = { ...POLICY, your_address: "203.0.113.7", your_network: "203.0.113.0/24" };

  it("explains a save that would lock the administrator out, and adds their network in one step", async () => {
    const calls = mockApi({
      [`GET ${ORG}/security/network`]: [{ status: 200, body: NETWORK }],
      [`PUT ${ORG}/security/network`]: [
        {
          status: 409,
          body: {
            code: "network_lockout",
            detail: "Your current network is not in the list, so saving it would lock you out. Add your own network first.",
            your_network: "203.0.113.0/24",
          },
        },
      ],
    });
    const { container } = renderOrg(<NetworkPolicyScreen />, ADMIN);
    expect(await screen.findByText(/You're connecting from 203.0.113.7/)).toBeInTheDocument();
    expect(screen.getByText(/server-to-server calls are not affected/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Only allow access from the networks below"));
    const save = screen.getByRole("button", { name: "Save network access" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Allowed network 1"), { target: { value: "198.51.100.0/24" } });
    fireEvent.click(save);
    expect(await screen.findByRole("alert")).toHaveTextContent(/would lock you out/);
    expect(screen.getByText(/Not saved - this would lock you out/)).toBeInTheDocument();
    expect(posted(calls, `PUT ${ORG}/security/network`)[0].body).toEqual({
      ip_allowlist_enabled: true,
      ip_allowlist: ["198.51.100.0/24"],
      version: 7,
    });
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Add my network (203.0.113.0/24)" }));
    expect(screen.getByLabelText("Allowed network 2")).toHaveValue("203.0.113.0/24");
    expect(screen.getByLabelText("Allowed network 2")).toHaveFocus();
    expect(screen.queryByRole("button", { name: /Add my network/ })).not.toBeInTheDocument();
  });

  it("edits the list row by row", async () => {
    mockApi({
      [`GET ${ORG}/security/network`]: [
        { status: 200, body: { ...NETWORK, ip_allowlist_enabled: true, ip_allowlist: ["203.0.113.0/24", "192.0.2.1"] } },
      ],
    });
    renderOrg(<NetworkPolicyScreen />, ADMIN);
    expect(await screen.findByLabelText("Allowed network 2")).toHaveValue("192.0.2.1");
    fireEvent.click(screen.getByRole("button", { name: "Add a network" }));
    expect(screen.getByLabelText("Allowed network 3")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Remove allowed network 2" }));
    expect(screen.getByLabelText("Allowed network 2")).toHaveValue("");
    expect(screen.queryByLabelText("Allowed network 3")).not.toBeInTheDocument();
  });
});

// --- audit and sign-ins ---------------------------------------------------------------------------------

describe("audit log and sign-in activity", () => {
  it("filters the audit log by action prefix", async () => {
    const calls = mockApi({
      [`GET ${ORG}/audit`]: [
        page([
          {
            id: "a1",
            occurred_at: "2026-09-24T10:00:00Z",
            action: "membership.suspended",
            actor_kind: "user",
            actor_user_id: "u1",
            resource_type: "organizations.membership",
            resource_id: "m2",
            outcome: "success",
            reason: "",
            changes: null,
          },
        ]),
      ],
    });
    const { container } = renderOrg(<AuditLogScreen />, ADMIN);
    expect(await screen.findByText("membership.suspended")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.change(screen.getByLabelText("Action starts with"), { target: { value: "security." } });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("action=security."));
    expect(await screen.findByRole("table", { name: /starting “security\.”/ })).toBeInTheDocument();
  });

  it("lists sign-ins, and says so when the member may not see them", async () => {
    mockApi({
      [`GET ${ORG}/sign-ins`]: [
        page([
          {
            occurred_at: "2026-09-24T10:00:00Z",
            membership_id: "m2",
            email: "bob@acme.test",
            name: "Bob Jones",
            ip_prefix: "198.51.100.0/24",
            device_label: "Firefox on Windows",
          },
        ]),
      ],
    });
    const { container, unmount } = renderOrg(<SignInsScreen />, ADMIN);
    expect(await screen.findByText("Bob Jones")).toBeInTheDocument();
    await expectNoViolations(container);
    unmount();

    mockApi({ [`GET ${ORG}/sign-ins`]: [{ status: 403, body: { code: "permission_denied" } }] });
    renderOrg(<SignInsScreen />, []);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to sign-in activity.");
  });
});

// --- accepting an invitation (public) ---------------------------------------------------------------------

const PREVIEW = {
  organization_name: "Acme Ltd",
  email: "new@acme.test",
  role: "member",
  role_name: "Member",
  expires_at: "2026-10-01T00:00:00Z",
  account_exists: false,
  signed_in_as_invitee: false,
};
const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const TOKEN = "0123456789abcdef0123456789abcdef.secret-part";

describe("accepting an invitation", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", `/invitations/accept#token=${TOKEN}`);
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("removes the token from the address bar, previews, and creates the account", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: PREVIEW }],
      "POST /api/v1/public/invitations/accept": [
        { status: 400, body: { code: "password_policy_violation", detail: "x", problems: ["This password is too common."] } },
        { status: 200, body: { organization_slug: "acme", membership_id: "m9", account_created: true } },
      ],
    });
    const { container } = render(<AcceptInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "Join Acme Ltd" })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/invitations/accept");
    expect(posted(calls, "POST /api/v1/public/invitations/preview")[0].body).toEqual({ token: TOKEN });
    await expectNoViolations(container);

    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Nia New" } });
    fireEvent.change(screen.getByLabelText("Choose a password"), { target: { value: "correct horse battery" } });
    fireEvent.change(screen.getByLabelText("Confirm the password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account and join" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This password is too common.");
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Create account and join" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(posted(calls, "POST /api/v1/public/invitations/accept")[1].body).toEqual({
      token: TOKEN,
      full_name: "Nia New",
      password: "correct horse battery",
    });
  });

  it("checks the passwords match before sending anything", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: PREVIEW }],
    });
    render(<AcceptInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nia" } });
    fireEvent.change(screen.getByLabelText("Choose a password"), { target: { value: "one long passphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm the password"), { target: { value: "another passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account and join" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The passwords don't match.");
    expect(posted(calls, "POST /api/v1/public/invitations/accept")).toHaveLength(0);
  });

  it("the signed-in invitee joins with one button", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u9", email: "new@acme.test" } }],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: { ...PREVIEW, account_exists: true, signed_in_as_invitee: true } }],
      "POST /api/v1/public/invitations/accept": [{ status: 200, body: { organization_slug: "acme", membership_id: "m9", account_created: false } }],
    });
    const { container } = render(<AcceptInvitation />);
    const join = await screen.findByRole("button", { name: "Join Acme Ltd" });
    expect(screen.queryByLabelText("Choose a password")).not.toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(join);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(posted(calls, "POST /api/v1/public/invitations/accept")[0].body).toEqual({ token: TOKEN });
  });

  it("asks an existing account to sign in first", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: { ...PREVIEW, account_exists: true } }],
    });
    const { container } = render(<AcceptInvitation />);
    expect(await screen.findByText(/An account already exists for new@acme.test/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByLabelText("Choose a password")).not.toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("switches to 'sign in first' when accepting finds the account already exists", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: PREVIEW }],
      "POST /api/v1/public/invitations/accept": [
        { status: 409, body: { code: "sign_in_required", detail: "An account already exists for this address. Sign in to accept." } },
      ],
    });
    render(<AcceptInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nia" } });
    fireEvent.change(screen.getByLabelText("Choose a password"), { target: { value: "one long passphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm the password"), { target: { value: "one long passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account and join" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Sign in first/);
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("tells someone signed in as another account, and shows the server's mismatch message", async () => {
    mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u1", email: "other@acme.test" } }],
      "POST /api/v1/public/invitations/preview": [{ status: 200, body: PREVIEW }],
    });
    const { container } = render(<AcceptInvitation />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You're signed in as other@acme.test, but this invitation was sent to new@acme.test.",
    );
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("an invalid or used link says so", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/public/invitations/preview": [
        { status: 404, body: { code: "invitation_invalid", detail: "This invitation link is not valid." } },
      ],
    });
    const { container } = render(<AcceptInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "This invitation can't be used" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/expired, been withdrawn, or already been used/);
    await expectNoViolations(container);
  });

  it("a link without a token is invalid without asking the server", async () => {
    window.history.replaceState(null, "", "/invitations/accept");
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<AcceptInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "This invitation can't be used" })).toBeInTheDocument();
    expect(calls.some((c) => c.key.includes("/invitations/"))).toBe(false);
  });
});

// --- development test checkout ----------------------------------------------------------------------------

describe("the development test checkout", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("reports the chosen outcome for the reference in the link", async () => {
    window.history.replaceState(null, "", "/billing/test-checkout#reference=ref_123&org=o1");
    const calls = mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u1", email: "me@acme.test" } }],
      "POST /api/v1/public/payments/test-checkout/complete": [{ status: 200, body: { received: true, outcome: "applied" } }],
    });
    const { container } = render(<TestCheckout />);
    expect(screen.getByText("Development test page")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Pay (succeed)" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Test payment succeeded/);
    expect(posted(calls, "POST /api/v1/public/payments/test-checkout/complete")[0].body).toEqual({
      reference: "ref_123",
      organization_id: "o1",
      outcome: "succeeded",
    });
    expect(screen.getByRole("link", { name: "Return to Workeasy360" })).toHaveAttribute("href", "/");
    await expectNoViolations(container);
  });

  it("says when the link is incomplete", async () => {
    window.history.replaceState(null, "", "/billing/test-checkout#reference=ref_123");
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<TestCheckout />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/incomplete/);
    expect(screen.queryByRole("button", { name: "Fail" })).not.toBeInTheDocument();
  });
});

// --- my apps, product access, profile ------------------------------------------------------------------------

function app(code: string, name: string, state: string, licensing_mode = "per_seat") {
  return {
    product_code: code,
    display_name: name,
    description: `${name} for teams.`,
    licensing_mode,
    state,
    can_launch: state === "active",
    entitlement_status: state === "no_entitlement" ? null : "active",
  };
}

describe("my apps and product access", () => {
  it("lists the directory and explains a refused launch in words", async () => {
    nav.search = new URLSearchParams("launch_error=no_seat");
    mockApi({ [`GET ${ORG}/apps`]: [{ status: 200, body: { apps: [app("books", "Books", "active"), app("desk", "Desk", "no_seat")] } }] });
    const { container } = renderOrg(<MyAppsScreen />, []);
    expect(await screen.findByRole("button", { name: "Open Books" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "My products" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("You don't have a seat for that product yet.");
    await expectNoViolations(container);
  });

  it("lists the rest of the catalog as expired or available, from the server's states (multi-portal brief)", async () => {
    mockApi({
      [`GET ${ORG}/apps`]: [{ status: 200, body: { apps: [app("books", "EasyBook", "active")] } }],
      [`GET ${ORG}/explore`]: [
        {
          status: 200,
          body: explore({
            products: [
              exploreProduct("books", "EasyBook", { trial_state: "owned" }),
              exploreProduct("crm", "Easy CRM", { trial_state: "used", trial_outcome: "expired" }),
              exploreProduct("desk", "Easy Help Desk", {
                trial_state: "available",
                trial: { available: true, duration_days: 7, seat_limit: null, payment_method_required: false, plan_codes: ["desk_starter"] },
              }),
              exploreProduct("ops", "Easy Project", { trial_state: "unavailable" }),
            ],
          }),
        },
      ],
    });
    const { container } = renderOrg(<MyAppsScreen />, []);
    const section = await screen.findByRole("region", { name: "Other products" });
    const rows = within(section).getAllByRole("listitem").map((li) => li.textContent);
    expect(rows).toEqual([
      "Easy CRMTrial expiredUpgrade / Buy now",
      "Easy Help DeskAvailableStart 7-Day Free Trial",
      "Easy ProjectAvailableExplore",
    ]);
    // The held product stays in the grid above, not in this list.
    expect(within(section).queryByText("EasyBook")).not.toBeInTheDocument();
    for (const link of within(section).getAllByRole("link")) expect(link).toHaveAttribute("href", "/o/acme/explore");
    await expectNoViolations(container);
  });

  it("an unknown launch error code is never echoed", async () => {
    nav.search = new URLSearchParams("launch_error=<b>boom</b>");
    mockApi({ [`GET ${ORG}/apps`]: [{ status: 200, body: { apps: [] } }] });
    renderOrg(<MyAppsScreen />, []);
    expect(await screen.findByText("Your organization doesn't have any products yet.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("That app couldn't be opened right now.");
    expect(screen.getByRole("alert").textContent).not.toContain("boom");
  });

  it("product access offers only per-seat products the organization holds, one at a time", async () => {
    const seats = (code: string) => ({
      status: 200,
      body: {
        product_code: code,
        display_name: code,
        licensing_mode: "per_seat",
        seats_total: 3,
        seats_assigned: 1,
        members: [{ membership_id: "m2", email: "bob@acme.test", held: true }],
        next_cursor: null,
        has_more: false,
      },
    });
    const calls = mockApi({
      [`GET ${ORG}/apps`]: [
        {
          status: 200,
          body: {
            apps: [
              app("books", "Books", "active"),
              app("desk", "Desk", "no_seat"),
              app("wiki", "Wiki", "no_entitlement"),
              app("mail", "Mail", "active", "per_organization"),
            ],
          },
        },
      ],
      [`GET ${ORG}/apps/books/seats`]: [seats("books")],
      [`GET ${ORG}/apps/desk/seats`]: [seats("desk")],
    });
    const { container } = renderOrg(<ProductAccessScreen />, ["platform.seat.assign"]);
    const group = await screen.findByRole("group", { name: "Product" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Books", "Desk"]);
    await screen.findByText("bob@acme.test");
    await expectNoViolations(container);
    fireEvent.click(within(group).getByRole("button", { name: "Desk" }));
    expect(within(group).getByRole("button", { name: "Desk" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(calls.some((c) => c.key === `GET ${ORG}/apps/desk/seats`)).toBe(true));
  });

  it("the profile shows the shared identity", async () => {
    mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u1", email: "me@acme.test", name: "Me Admin" } }],
    });
    const { container } = renderOrg(<ProfileScreen />, []);
    expect(await screen.findByText("me@acme.test")).toBeInTheDocument();
    expect(screen.getByText("Me Admin")).toBeInTheDocument();
    await expectNoViolations(container);
  });
});
