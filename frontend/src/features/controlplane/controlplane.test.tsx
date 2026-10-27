import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { AdminContext, AdminShell, PartnerShell } from "./ControlShell";
import { Customers } from "./AdminScreens";
import { PayoutsTab, type TabProps } from "./ResellerTabs";
import { PartnerBranding, PartnerCustomers, PartnerDomains } from "./PartnerScreens";
import { applyAccent, contrastOn, safeColor } from "./useBranding";
import { ReasonAction, useAction } from "./ui";

/**
 * Phase 7 screens: behaviour the backend cannot enforce for the browser
 * (what the UI sends, and what it never sends) plus the same non-visual axe
 * pass as Phase 4/5. Contrast, focus visibility and reflow remain manual -
 * jsdom paints nothing.
 */

let pathname = "/admin";
// One router object, as Next.js returns: a new one per render would re-run
// every effect that depends on it.
const nav = vi.hoisted(() => ({ router: { replace: () => {}, push: () => {}, refresh: () => {} } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => pathname,
}));

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };

async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

type Reply = { status: number; body?: unknown };
function mockApi(routes: Record<string, Reply[]>) {
  const calls: { key: string; body: unknown; headers: Record<string, string> }[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const raw = init?.body;
    calls.push({
      key,
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

const SIGNED_IN: Reply = { status: 200, body: { authenticated: true, user_id: "u1", email: "s@example.com" } };
const PARTNER = "11111111-1111-1111-1111-111111111111";

/** What the server resolves for a member (partners/resolver.py), spelled out
 * for the few capabilities the tests use. The shell draws its navigation from
 * this list and from nothing else. */
const CAPABILITY_PERMISSIONS: Record<string, string[]> = {
  "customer.provision": ["customer.view", "customer.provision", "customer.export"],
  "subscription.sell": ["customer.view", "pricing.view", "subscription.sell"],
  "pricing.view": ["pricing.view"],
  "referral.create": ["referral.view", "referral.create"],
  "commission.view": ["commission.view"],
  "payout.view": ["payout.view"],
  "branding.manage": ["branding.view", "branding.manage"],
  "domain.manage": ["domain.view", "domain.manage"],
};

function permissionsFor(capabilities: string[], status: string, admin: boolean): string[] {
  const own = capabilities.flatMap((c) => CAPABILITY_PERMISSIONS[c] ?? []);
  if (status === "draft") return [];
  if (status === "terminated") return own.filter((p) => ["commission.view", "payout.view"].includes(p)).map((p) => `platform.partner_${p}`);
  const base = ["dashboard.view", "profile.view", "member.view", "product.view", ...(admin ? ["profile.manage", "member.manage", "product.manage", "activity.view"] : [])];
  return [...own, ...base].map((p) => `platform.partner_${p}`);
}

function portal(capabilities: string[], status = "active", role = "admin"): Reply {
  return {
    status: 200,
    body: {
      partners: [
        {
          partner_id: PARTNER,
          code: "acme",
          display_name: "Acme Partners",
          status,
          role,
          is_admin: role === "admin",
          capabilities,
          permissions: permissionsFor(capabilities, status, role === "admin"),
        },
      ],
    },
  };
}

/** Every page in the menu, each collapsible section opened first. */
function navLabels(): string[] {
  const nav = screen.getByRole("navigation");
  for (const b of within(nav).queryAllByRole("button")) if (b.getAttribute("aria-expanded") === "false") fireEvent.click(b);
  return within(nav).queryAllByRole("link").map((a) => a.textContent ?? "");
}

beforeEach(() => {
  vi.restoreAllMocks();
  pathname = "/admin";
});

describe("the Super Admin shell", () => {
  it("shows only the navigation the staff member's permissions allow", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [
        { status: 200, body: { is_staff: true, permissions: ["platform.staff_customer.view", "platform.staff_audit.view"] } },
      ],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    expect(navLabels()).toEqual(["Dashboard", "Subscriptions", "Entitlements", "Audit"]);
  });

  it("says plainly that the account has no administrator access when the backend refuses, and offers the User way in", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [{ status: 403, body: { code: "permission_denied" } }],
    });
    const replace = vi.spyOn(nav.router, "replace");
    const { container } = render(
      <AdminShell>
        <h1>Secret</h1>
      </AdminShell>,
    );
    expect(await screen.findByRole("heading", { name: "You don't have administrator access." })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/administrator access/);
    // Nothing from the console: no page, no navigation, no permission named.
    expect(screen.queryByRole("heading", { name: "Secret" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/platform\.staff_/);
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Continue as User" }));
    expect(replace).toHaveBeenCalledWith("/dashboard?mode=user");
    replace.mockRestore();
  });

  it("sends a signed-out visitor to the Administrator sign-in, remembering the page", async () => {
    pathname = "/admin/partners/resellers";
    mockApi({ "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: false } }] });
    const replace = vi.spyOn(nav.router, "replace");
    render(
      <AdminShell>
        <h1>Secret</h1>
      </AdminShell>,
    );
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(`/login?mode=admin&next=${encodeURIComponent("/admin/partners/resellers")}`),
    );
    expect(screen.queryByRole("heading", { name: "Secret" })).not.toBeInTheDocument();
    replace.mockRestore();
    pathname = "/admin";
  });

  it("opens the control plane after a password sign-in, with nothing more to enter", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [
        { status: 200, body: { is_staff: true, permissions: ["platform.staff_customer.view"] } },
      ],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
  });

  it("says so when signing out fails, instead of pretending it worked", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/admin/session": [{ status: 200, body: { is_staff: true, permissions: [] } }],
      "POST /api/v1/auth/logout": [{ status: 503, body: { code: "unavailable" } }],
    });
    render(
      <AdminShell>
        <h1>Page</h1>
      </AdminShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    // Sign out lives in the account menu of the shared shell.
    fireEvent.click(screen.getByRole("button", { name: /Account menu/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/still signed in/);
  });
});

/** A partner's Payouts tab (ADR-048: the stand-alone payouts page is gone; the
 * payout lifecycle controls now live on the partner's own page). */
function PayoutsFor({ partnerId = PARTNER }: { partnerId?: string }) {
  const props = {
    partnerId,
    partner: { id: partnerId } as unknown as TabProps["partner"],
    summary: { status: "loading" },
    rev: 0,
    bump: () => {},
  } satisfies TabProps;
  return <PayoutsTab {...props} />;
}

describe("Super Admin screens", () => {
  it("the customer directory has no axe violations and labels its controls", async () => {
    mockApi({
      "GET /api/v1/admin/customers": [
        {
          status: 200,
          body: {
            results: [
              {
                organization_id: "o1",
                slug: "acme",
                display_name: "Acme Ltd",
                status: "active",
                created_at: "2026-01-01T00:00:00Z",
                owner_email: "owner@acme.test",
                subscription_status: "active",
                products: ["crm"],
                seat_capacity: 5,
                summary_as_of: "2026-01-02T00:00:00Z",
                partner: null,
              },
            ],
            next_cursor: null,
            has_more: false,
          },
        },
      ],
    });
    const { container } = render(
      <main>
        <Customers />
      </main>,
    );
    await screen.findByRole("table", { name: "Customer organizations" });
    expect(screen.getByLabelText("Search by name or slug")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("the directory searches on submit, not on every keystroke", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/customers": [{ status: 200, body: { results: [], next_cursor: null, has_more: false } }],
    });
    render(
      <main>
        <Customers />
      </main>,
    );
    await screen.findByText("No customers match.");
    const before = calls.length;
    fireEvent.change(screen.getByLabelText("Search by name or slug"), { target: { value: "acm" } });
    fireEvent.change(screen.getByLabelText("Search by name or slug"), { target: { value: "acme" } });
    expect(calls.length).toBe(before);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.length).toBe(before + 1));
  });

  it("preparing a payout sends only a partner and a date - never an amount", async () => {
    const calls = mockApi({
      "GET /api/v1/admin/payouts": [{ status: 200, body: { results: [], next_cursor: null, has_more: false } }],
      "POST /api/v1/admin/payouts": [{ status: 200, body: {} }],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_payout.manage", "platform.staff_commission.view"]}>
        <PayoutsFor />
      </AdminContextForTest>,
    );
    await screen.findByText("No payouts.");
    // The partner is the page's own: there is no partner field to type into.
    expect(screen.queryByLabelText(/Partner id/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare payout" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Include everything cleared by"), { target: { value: "2026-06-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare payout" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/admin/payouts")).toBe(true));
    const post = calls.find((c) => c.key === "POST /api/v1/admin/payouts")!;
    expect(Object.keys(post.body as object).sort()).toEqual(["partner_id", "period_end"]);
    expect(post.body).toEqual({ partner_id: PARTNER, period_end: "2026-06-30" });
    expect(post.headers["idempotency-key"]).toBeTruthy();
  });

  it("approval is offered only to someone who may approve, and never to prepare-only staff", async () => {
    const ready = {
      id: "p1",
      status: "ready",
      currency: "GBP",
      period_end: "2026-06-30",
      gross_minor: 1000,
      adjustments_minor: 0,
      payable_minor: 1000,
      line_count: 2,
      version: 3,
    };
    mockApi({ "GET /api/v1/admin/payouts": [{ status: 200, body: { results: [ready], next_cursor: null, has_more: false } }] });
    const { unmount } = render(
      <AdminContextForTest permissions={["platform.staff_payout.manage"]}>
        <PayoutsFor />
      </AdminContextForTest>,
    );
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/admin/payouts": [{ status: 200, body: { results: [ready], next_cursor: null, has_more: false } }] });
    render(
      <AdminContextForTest permissions={["platform.staff_payout.approve"]}>
        <PayoutsFor />
      </AdminContextForTest>,
    );
    expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("approves a payout in one click, recording a reason without asking", async () => {
    const ready = {
      id: "p1",
      status: "ready",
      currency: "GBP",
      period_end: "2026-06-30",
      gross_minor: 1000,
      adjustments_minor: 0,
      payable_minor: 1000,
      line_count: 1,
      version: 1,
    };
    const calls = mockApi({
      "GET /api/v1/admin/payouts": [{ status: 200, body: { results: [ready], next_cursor: null, has_more: false } }],
      "POST /api/v1/admin/payouts/p1/approve": [{ status: 200, body: { ...ready, status: "approved", version: 2 } }],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_payout.approve"]}>
        <PayoutsFor />
      </AdminContextForTest>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(screen.queryByLabelText(/Reason for/)).not.toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent(/Payout approved/);
    expect(calls.find((c) => c.key === "POST /api/v1/admin/payouts/p1/approve")!.body).toMatchObject({
      reason: "Approve - Super Admin console",
    });
  });

  it("a destructive action asks once to confirm, then puts keyboard focus on the outcome", async () => {
    function Harness() {
      const { act, feedback } = useAction();
      return (
        <>
          {feedback}
          <ReasonAction label="Suspend" danger onConfirm={(reason) => void act(async () => void reason, "Suspended.")} />
        </>
      );
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    const confirm = within(screen.getByRole("group", { name: "Confirm Suspend" })).getByRole("button", { name: "Confirm suspend" });
    expect(screen.queryByLabelText(/Reason for/)).not.toBeInTheDocument();
    fireEvent.click(confirm);
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Suspended.");
    await waitFor(() => expect(status).toHaveFocus());
  });

  it("puts keyboard focus nowhere new when the button that started the action is still there", async () => {
    const ready = {
      id: "p1",
      status: "ready",
      currency: "GBP",
      period_end: "2026-06-30",
      gross_minor: 1000,
      adjustments_minor: 0,
      payable_minor: 1000,
      line_count: 1,
      version: 1,
    };
    mockApi({
      "GET /api/v1/admin/payouts": [{ status: 200, body: { results: [ready], next_cursor: null, has_more: false } }],
      "POST /api/v1/admin/payouts/p1/approve": [{ status: 200, body: { ...ready, status: "approved", version: 2 } }],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_payout.approve"]}>
        <PayoutsFor />
      </AdminContextForTest>,
    );
    const approve = await screen.findByRole("button", { name: "Approve" });
    approve.focus();
    fireEvent.click(approve);
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/Payout approved/);
    expect(status).not.toHaveFocus();
  });
});

describe("the partner shell", () => {
  it("builds navigation from capabilities alone", async () => {
    pathname = `/partner/${PARTNER}`;
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal(["commission.view", "payout.view"])] });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const labels = navLabels();
    // Commission and payouts are read from Payments and Affiliates now (ADR-046).
    expect(labels).toEqual(expect.arrayContaining(["Dashboards", "Payments", "Affiliates"]));
    for (const absent of ["Users", "Agency", "Plans & Add-ons", "Brand Settings", "Currency & Pricing"]) {
      expect(labels).not.toContain(absent);
    }
  });

  it("a staff guest view says it is read-only on every page and offers the way out", async () => {
    pathname = `/partner/${PARTNER}`;
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const reply = portal(["customer.provision"], "active", "viewer");
    const body = reply.body as { partners: Record<string, unknown>[] };
    body.partners[0].guest = { partner_id: PARTNER, reason: "Support ticket 42", expires_at: "2026-03-01T10:30:00Z" };
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [reply],
      "POST /api/v1/partner/guest-view/end": [{ status: 200, body: { ended: true } }],
    });
    const { container } = render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const banner = screen.getByText(/Viewing Partner Portal as Acme Partners\./);
    expect(banner).toHaveTextContent("you cannot change anything here.");
    await expectNoViolations(container);
    fireEvent.click(within(banner).getByRole("button", { name: "Exit Guest Session" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith(`/admin/partners/${PARTNER}`));
    expect(calls.some((c) => c.key === "POST /api/v1/partner/guest-view/end")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("a reseller-like and an affiliate-like partner differ only by what they were granted", async () => {
    pathname = `/partner/${PARTNER}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["customer.provision", "subscription.sell", "pricing.view", "referral.create", "commission.view"])],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    await screen.findByRole("heading", { name: "Page" });
    const labels = navLabels();
    expect(labels).toEqual(expect.arrayContaining(["Dashboards", "Users", "Partner Products", "Plans & Add-ons", "Affiliates", "Payments"]));
    for (const absent of ["Agency", "Brand Settings"]) {
      expect(labels).not.toContain(absent);
    }
  });

  it("explains a suspended partner and keeps history readable", async () => {
    pathname = `/partner/${PARTNER}`;
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [portal(["commission.view"], "suspended")] });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    expect(await screen.findByText(/This partner account is suspended/)).toBeInTheDocument();
  });

  it("offers an ended partner only the money it is owed, and says so", async () => {
    pathname = `/partner/${PARTNER}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [
        portal(["customer.provision", "subscription.sell", "pricing.view", "branding.manage", "domain.manage", "commission.view", "payout.view"], "terminated"),
      ],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    expect(await screen.findByText(/This partner account has ended/)).toBeInTheDocument();
    // The server refuses everything else for an ended partner, so nothing else is offered.
    expect(navLabels()).toEqual(["Affiliates", "Payments"]);
  });

  it("offers a partner that was never activated nothing to do", async () => {
    pathname = `/partner/${PARTNER}`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["customer.provision", "commission.view"], "draft")],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    expect(await screen.findByText(/isn.t active yet/)).toBeInTheDocument();
    expect(navLabels()).toEqual([]);
  });

  it("does not reveal a partner the person does not belong to", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN], "GET /api/v1/partner/portal": [{ status: 200, body: { partners: [] } }] });
    render(
      <PartnerShell partnerId={PARTNER}>
        <h1>Page</h1>
      </PartnerShell>,
    );
    await screen.findByText(/don.t have access to this partner account/);
  });
});

describe("partner screens", () => {
  it("provisioning replays one command id after a failure and mints a new one after success", async () => {
    pathname = `/partner/${PARTNER}/customers`;
    const provision = [
      { status: 500, body: {} },
      { status: 200, body: { organization_id: "o1", slug: "x", replayed: false } },
      { status: 200, body: { organization_id: "o2", slug: "y", replayed: false } },
    ];
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["customer.provision"])],
      [`GET /api/v1/partner/${PARTNER}/customers`]: [{ status: 200, body: { results: [], next_cursor: null, has_more: false } }],
      [`POST /api/v1/partner/${PARTNER}/customers`]: provision,
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerCustomers />
      </PartnerShell>,
    );
    const submit = async () => {
      fireEvent.change(await screen.findByLabelText("Organization name"), { target: { value: "Globex" } });
      fireEvent.change(screen.getByLabelText(/Owner.s email/), { target: { value: "o@globex.test" } });
      fireEvent.click(screen.getByRole("button", { name: "Create customer" }));
    };
    await submit();
    await screen.findByText(/Something went wrong/);
    await submit();
    await screen.findByText(/Customer created/);
    await submit();
    await waitFor(() => expect(calls.filter((c) => c.key.startsWith("POST") && c.key.endsWith("/customers")).length).toBe(3));
    const keys = calls.filter((c) => c.key.startsWith("POST") && c.key.endsWith("/customers")).map((c) => c.headers["idempotency-key"]);
    expect(keys[0]).toBe(keys[1]); // the retry is the same command
    expect(keys[2]).not.toBe(keys[1]); // a new intent is a new command
  });

  it("hides provisioning from a partner that lacks the capability", async () => {
    pathname = `/partner/${PARTNER}/customers`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["subscription.sell"])],
      [`GET /api/v1/partner/${PARTNER}/customers`]: [{ status: 200, body: { results: [], next_cursor: null, has_more: false } }],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerCustomers />
      </PartnerShell>,
    );
    await screen.findByText("No customers yet.");
    expect(screen.queryByLabelText("Organization name")).not.toBeInTheDocument();
  });

  it("shows an ended relationship as history with nothing to act on", async () => {
    pathname = `/partner/${PARTNER}/customers`;
    const ended = {
      organization_id: "o-ended",
      name: "",
      organization_status: "",
      link_status: "ended",
      started_at: "2026-01-01T00:00:00Z",
      ended_at: "2026-06-01T00:00:00Z",
      subscription_status: "",
      products: [],
    };
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["customer.provision", "support.access", "subscription.sell"])],
      [`GET /api/v1/partner/${PARTNER}/customers`]: [{ status: 200, body: { results: [ended], next_cursor: null, has_more: false } }],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerCustomers />
      </PartnerShell>,
    );
    expect(await screen.findByText("Former customer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Diagnose launch" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sell/ })).not.toBeInTheDocument();
  });

  it("branding: a bad colour disables saving and never reaches the DOM", async () => {
    pathname = `/partner/${PARTNER}/branding`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["branding.manage"])],
      [`GET /api/v1/partner/${PARTNER}/branding`]: [{ status: 200, body: { brand: null } }],
    });
    const { container } = render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerBranding />
      </PartnerShell>,
    );
    fireEvent.change(await screen.findByLabelText("Brand name"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Accent colour/), { target: { value: "red;background:url(//evil)" } });
    expect(screen.getByRole("button", { name: "Save branding" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/six-digit hex/);
    // What the person typed stays in the input; nothing reaches a style or the page.
    const preview = container.querySelector("section[aria-labelledby=preview-h]") as HTMLElement;
    expect(preview.style.getPropertyValue("--w360-brand")).toBe("");
    expect(container.querySelectorAll("[style*=evil]")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText(/Accent colour/), { target: { value: "#1a56db" } });
    expect(screen.getByRole("button", { name: "Save branding" })).toBeEnabled();
    await expectNoViolations(container);
  });

  it("branding: saving without choosing a new logo sends the current one back (a save replaces the brand)", async () => {
    pathname = `/partner/${PARTNER}/branding`;
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["branding.manage"])],
      [`GET /api/v1/partner/${PARTNER}/branding`]: [
        {
          status: 200,
          body: {
            brand: {
              brand_name: "Acme",
              logo_file_id: "22222222-2222-2222-2222-222222222222",
              accent_color: "#1a56db",
              theme: {},
              support_email: "",
              support_url: "",
              version: 4,
            },
          },
        },
      ],
      [`PUT /api/v1/partner/${PARTNER}/branding`]: [{ status: 200, body: {} }],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerBranding />
      </PartnerShell>,
    );
    fireEvent.change(await screen.findByLabelText("Brand name"), { target: { value: "Acme Two" } });
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(true));
    const put = calls.find((c) => c.key.startsWith("PUT"))!;
    expect(put.body).toMatchObject({
      brand_name: "Acme Two",
      logo_file_id: "22222222-2222-2222-2222-222222222222",
      expected_version: 4,
    });
  });

  it("branding: a non-image logo is refused before anything is uploaded", async () => {
    pathname = `/partner/${PARTNER}/branding`;
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["branding.manage"])],
      [`GET /api/v1/partner/${PARTNER}/branding`]: [{ status: 200, body: { brand: null } }],
    });
    render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerBranding />
      </PartnerShell>,
    );
    const input = await screen.findByLabelText(/Logo/);
    const svg = new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" });
    fireEvent.change(input, { target: { files: [svg] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/PNG or JPEG/);
    expect(calls.some((c) => c.key.includes("logo"))).toBe(false);
  });

  it("domains show the DNS record to publish and pass axe", async () => {
    pathname = `/partner/${PARTNER}/domains`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["domain.manage"])],
      [`GET /api/v1/partner/${PARTNER}/domains`]: [
        {
          status: 200,
          body: {
            results: [
              {
                id: "d1",
                hostname: "app.acme.test",
                status: "requested",
                verification_record: { type: "TXT", name: "_w360-verify.app.acme.test", value: "w360-abc" },
                failure_code: "",
                version: 1,
              },
            ],
          },
        },
      ],
    });
    const { container } = render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerDomains />
      </PartnerShell>,
    );
    await screen.findByText(/_w360-verify\.app\.acme\.test/);
    expect(screen.getByRole("button", { name: "Check DNS" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    await expectNoViolations(container);
  });
});

describe("hostile text is rendered as text (XSS)", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

  function expectInert(container: HTMLElement) {
    expect(container.querySelector("img:not([data-w360-logo])")).toBeNull(); // the shell's own logo aside
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<img");
  }

  it("a partner name in the staff partner list", async () => {
    mockApi({
      "GET /api/v1/admin/partners/resellers": [
        {
          status: 200,
          body: {
            results: [
              {
                id: "p1",
                code: "evil",
                display_name: EVIL,
                version: 1,
                payout_currency: "GBP",
                status: "active",
                created_at: "2026-01-01T00:00:00Z",
                experience: [{ key: "reseller", label: EVIL }],
                primary_experience: "reseller",
                capability_count: 1,
                capabilities: ["customer.provision"],
                administrator: { email: EVIL, name: EVIL, role: "admin", last_login_at: null },
                customers: 0,
                products: [{ product_code: EVIL, plan_code: null }],
                active_subscriptions: 0,
                commission: [],
                payouts: { open: 0, latest: null },
                referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
                profile_completion: { percent: 100, complete: true, setup_status: "active" },
                last_activity_at: "2026-01-01T00:00:00Z",
              },
            ],
            next_cursor: null,
            has_more: false,
          },
        },
      ],
    });
    const { AllPartnersList } = await import("./PartnerConsole");
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <AllPartnersList />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Partners" });
    expect(within(table).getAllByText(EVIL).length).toBeGreaterThan(0);
    expectInert(container);
  });

  it("a customer's name in the directory", async () => {
    mockApi({
      "GET /api/v1/admin/customers": [
        {
          status: 200,
          body: {
            results: [
              {
                organization_id: "o1",
                slug: "x",
                display_name: EVIL,
                status: "active",
                created_at: "2026-01-01T00:00:00Z",
                owner_email: EVIL,
                subscription_status: "active",
                products: [EVIL],
                seat_capacity: null,
                summary_as_of: null,
                partner: { partner_id: "p", partner_code: "c", partner_name: EVIL, link_status: "active" },
              },
            ],
            next_cursor: null,
            has_more: false,
          },
        },
      ],
    });
    const { container } = render(
      <main>
        <Customers />
      </main>,
    );
    await screen.findByRole("table", { name: "Customer organizations" });
    expectInert(container);
  });

  it("a referral label, a domain, and a commission reason in the partner screens", async () => {
    pathname = `/partner/${PARTNER}/affiliates`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["referral.create", "referral.view"])],
      [`GET /api/v1/partner/${PARTNER}/affiliates`]: [
        {
          status: 200,
          body: {
            programme: { enabled: true, attribution_window_days: 90, visible_to_you: [], never_shown: [], eligible_products: [], payout_currency: "INR", commission_rules: [] },
            summary: { captured: 1 },
            codes: [{ id: "c1", code: "abc", label: EVIL, status: "active", url: "https://auth.example.com/signup?ref=abc" }],
            affiliates: [],
            not_implemented: {},
          },
        },
      ],
    });
    const { Affiliates } = await import("@/features/partnerpanel/Network");
    const { container } = render(
      <PartnerShell partnerId={PARTNER}>
        <Affiliates />
      </PartnerShell>,
    );
    await screen.findByRole("table", { name: "Your referral links" });
    expectInert(container);
  });

  it("a hostname and a brand name typed into the branding preview", async () => {
    pathname = `/partner/${PARTNER}/branding`;
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [portal(["branding.manage"])],
      [`GET /api/v1/partner/${PARTNER}/branding`]: [{ status: 200, body: { brand: null } }],
    });
    const { container } = render(
      <PartnerShell partnerId={PARTNER}>
        <PartnerBranding />
      </PartnerShell>,
    );
    fireEvent.change(await screen.findByLabelText("Brand name"), { target: { value: EVIL } });
    expect(container.querySelector("img:not([data-w360-logo])")).toBeNull(); // the shell's own logo aside
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe(EVIL);
  });
});

describe("brand colour handling", () => {
  it("accepts only a six-digit hex colour", () => {
    expect(safeColor("#1a56db")).toBe("#1a56db");
    for (const bad of ["red", "#fff", "#1a56db;x", "url(x)", "#1a56dbzz", "", null, undefined]) {
      expect(safeColor(bad as string | null | undefined)).toBeNull();
    }
  });

  it("sets the accent through the CSSOM and falls back to the default on a bad value", () => {
    const el = document.createElement("div");
    applyAccent(el, "#1a56db");
    expect(el.style.getPropertyValue("--w360-brand")).toBe("#1a56db");
    applyAccent(el, "javascript:alert(1)");
    expect(el.style.getPropertyValue("--w360-brand")).toBe("");
  });

  it("derives a readable text colour so no accent can hide the sign-in label", () => {
    expect(contrastOn("#ffffff")).toBe("#000000");
    expect(contrastOn("#ffff00")).toBe("#000000");
    expect(contrastOn("#000000")).toBe("#ffffff");
    expect(contrastOn("#1a56db")).toBe("#ffffff");
    const el = document.createElement("div");
    applyAccent(el, "#ffffff");
    expect(el.style.getPropertyValue("--w360-brand-contrast")).toBe("#000000");
    applyAccent(el, "not-a-colour");
    expect(el.style.getPropertyValue("--w360-brand-contrast")).toBe("");
  });
});

function AdminContextForTest({ permissions, children }: { permissions: string[]; children: React.ReactNode }) {
  return <AdminContext.Provider value={{ permissions }}>{children}</AdminContext.Provider>;
}
