import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AllPartnerCustomers, OwnerInvitationActions, StaffOwnerActions } from "./PartnerCustomers";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";
import { partnerConsoleApi, portalOwnerInvitationApi, type PartnerCustomerAdminRow } from "@/lib/api/partnerConsole";

/**
 * Phase 10e: the console's side of the owner activation - what a customer row
 * says about it, and the two actions that change it. The server decides who may
 * act and when a recent verification is needed; the browser must show only what
 * can work, prompt for a second factor on the click that needs it, and never be
 * handed (or show) the link.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/partners/customers",
}));

const STEP_UP: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };
const STATE = {
  activation: "pending",
  expires_at: "2026-10-12T00:00:00Z",
  resolved_at: null,
  last_sent_at: "2026-09-28T09:00:00Z",
  send_count: 2,
};

function row(over: Partial<PartnerCustomerAdminRow> & { activation?: PartnerCustomerAdminRow["owner"]["activation"] } = {}): PartnerCustomerAdminRow {
  const { activation = "pending", ...rest } = over;
  return {
    link_id: "l1",
    organization_id: "o1",
    name: "Acme Ltd",
    slug: "acme",
    organization_status: "active",
    partner_id: "p1",
    partner_code: "reseller-one",
    partner_name: "Reseller One",
    link_status: "active",
    started_at: "2026-09-01T00:00:00Z",
    ended_at: null,
    end_reason: "",
    subscription_status: "",
    products: [],
    seat_capacity: null,
    owner: {
      activation,
      email: "owner@acme.test",
      invitation_id: "i1",
      expires_at: "2026-10-12T00:00:00Z",
      resolved_at: null,
      last_sent_at: "2026-09-28T09:00:00Z",
      send_count: 1,
    },
    ...rest,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

function actions(activation: PartnerCustomerAdminRow["owner"]["activation"], live = true, onDone = vi.fn()) {
  return render(
    <OwnerInvitationActions
      activation={activation}
      live={live}
      subject="Acme Ltd"
      resend={() => partnerConsoleApi.resendOwnerInvitation("p1", "o1")}
      revoke={() => partnerConsoleApi.revokeOwnerInvitation("p1", "o1")}
      onDone={onDone}
    />,
  );
}

describe("what can be done, by state", () => {
  it("a pending invitation can be sent again or withdrawn", async () => {
    const { container } = actions("pending");
    expect(screen.getByRole("button", { name: "Resend activation link for Acme Ltd" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw the activation link for Acme Ltd" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it.each(["expired", "revoked", "declined"] as const)("a %s invitation can only be replaced by a new one", (state) => {
    actions(state);
    expect(screen.getByRole("button", { name: "Send a new activation link for Acme Ltd" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Withdraw/ })).not.toBeInTheDocument();
  });

  it.each(["activated", "not_applicable"] as const)("offers nothing once %s", (state) => {
    const { container } = actions(state);
    expect(container).toBeEmptyDOMElement();
  });

  it("offers nothing on a relationship that has ended", () => {
    const { container } = actions("pending", false);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("acting", () => {
  it("resends through the staff endpoint, says what happened, and reloads", async () => {
    const onDone = vi.fn();
    const calls = mockApi({
      "POST /api/v1/admin/partners/p1/customers/o1/owner-invitation/resend": [ok(STATE)],
    });
    actions("pending", true, onDone);
    fireEvent.click(screen.getByRole("button", { name: "Resend activation link for Acme Ltd" }));
    expect(await screen.findByRole("status")).toHaveTextContent("A new activation link was emailed to the owner.");
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({});
  });

  it("withdraws, and says the customer is unchanged", async () => {
    mockApi({
      "POST /api/v1/admin/partners/p1/customers/o1/owner-invitation/revoke": [ok({ ...STATE, activation: "revoked" })],
    });
    actions("pending");
    fireEvent.click(screen.getByRole("button", { name: "Withdraw the activation link for Acme Ltd" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The customer is unchanged");
  });

  it("asks for a second factor on the click that needs it, then completes the same action", async () => {
    const calls = mockApi({
      "POST /api/v1/admin/partners/p1/customers/o1/owner-invitation/resend": [STEP_UP, ok(STATE)],
      "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
    });
    actions("pending");
    fireEvent.click(screen.getByRole("button", { name: "Resend activation link for Acme Ltd" }));
    const code = await screen.findByLabelText(/needs a recent verification/);
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText(/A new activation link was emailed/)).toBeInTheDocument();
    expect(calls.filter((c) => c.key.endsWith("/resend"))).toHaveLength(2);
  });

  it("reports a refusal without changing what is shown", async () => {
    mockApi({
      "POST /api/v1/admin/partners/p1/customers/o1/owner-invitation/resend": [
        { status: 400, body: { code: "partner_validation_error", detail: "The owner has already activated this account." } },
      ],
    });
    const onDone = vi.fn();
    actions("pending", true, onDone);
    fireEvent.click(screen.getByRole("button", { name: "Resend activation link for Acme Ltd" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already activated");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("the partner's own portal uses its own endpoints", async () => {
    const calls = mockApi({
      "POST /api/v1/partner/p1/customers/o1/owner-invitation/resend": [ok(STATE)],
      "POST /api/v1/partner/p1/customers/o1/owner-invitation/revoke": [ok({ ...STATE, activation: "revoked" })],
    });
    await portalOwnerInvitationApi.resend("p1", "o1");
    await portalOwnerInvitationApi.revoke("p1", "o1");
    expect(calls.map((c) => c.key)).toEqual([
      "POST /api/v1/partner/p1/customers/o1/owner-invitation/resend",
      "POST /api/v1/partner/p1/customers/o1/owner-invitation/revoke",
    ]);
  });

  it("StaffOwnerActions names the partner in the path, not just the customer", async () => {
    const calls = mockApi({
      "POST /api/v1/admin/partners/p9/customers/o1/owner-invitation/resend": [ok(STATE)],
    });
    render(<StaffOwnerActions row={row({ partner_id: "p9" })} onDone={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Resend activation link for Acme Ltd" }));
    await waitFor(() => expect(calls).toHaveLength(1));
  });
});

describe("the customers page", () => {
  const LIST = {
    results: [row(), row({ link_id: "l2", organization_id: "o2", name: "Done Ltd", activation: "activated" })],
    next_cursor: null,
    has_more: false,
  };

  // "Owner activation pending" is also an option in the filter, so every check
  // is made inside the table - and only after the table has loaded.
  async function customersTable(permissions: string[]) {
    mockApi({ "GET /api/v1/admin/partners/customers": [ok(LIST)] });
    const view = render(
      <AdminContextForTest permissions={permissions}>
        <AllPartnerCustomers />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "Partner customers" });
    return { ...view, table };
  }

  it("offers the actions to staff who may manage partners, on the rows that can use them", async () => {
    const { container, table } = await customersTable(["platform.staff_partner.view", "platform.staff_partner.manage"]);
    expect(within(table).getByText("Owner activation pending")).toBeInTheDocument();
    expect(within(table).getByText("Owner activated")).toBeInTheDocument();
    // Resend and withdraw, for the one row whose owner has not activated.
    expect(within(table).getAllByRole("button", { name: /activation link for/ })).toHaveLength(2);
    expect(within(table).getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows the state but no actions to staff who may only view", async () => {
    const { table } = await customersTable(["platform.staff_partner.view"]);
    expect(within(table).getByText("Owner activation pending")).toBeInTheDocument();
    expect(within(table).queryByRole("button", { name: /activation link for/ })).not.toBeInTheDocument();
    expect(within(table).queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
  });

  it("never shows a token, a hash or a link, whatever it was sent", async () => {
    const { table } = await customersTable(["platform.staff_partner.view"]);
    expect(table.textContent).not.toMatch(/token|owner-invitations\/accept/i);
  });
});
