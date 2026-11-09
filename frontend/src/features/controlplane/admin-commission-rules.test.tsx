import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import Page from "@/app/admin/partners/commission-rules/page";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "./testSupport";

/**
 * Partners → Commission rules: the rules as the server holds them, a new rule and a new
 * version with the exact request, the server's refusal, the grants, the attribution policy
 * and the platform's own terms.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/admin/partners/commission-rules",
}));

const ADMIN = "/api/v1/admin";
const VIEW = "platform.staff_commission.view";
const MANAGE = "platform.staff_commission.manage";
const PARTNER_VIEW = "platform.staff_partner.view";
const PARTNER_MANAGE = "platform.staff_partner.manage";

const RULE = {
  id: "r1",
  code: "programme",
  version: 2,
  partner_id: null,
  partner_name: "",
  product_code: "",
  plan_code: "",
  rate_bp: 1250,
  recurring: true,
  recurring_months: 12,
  fixed_amount_minor: null,
  fixed_currency: "",
  effective_from: "2026-01-01T00:00:00+00:00",
  retired_at: null,
};
const FIXED = {
  ...RULE,
  id: "r2",
  code: "crm-launch",
  version: 1,
  product_code: "easy_crm",
  rate_bp: 0,
  recurring: false,
  recurring_months: null,
  fixed_amount_minor: 50000,
  fixed_currency: "INR",
};
const POLICY = {
  attribution_days: 45,
  revoked_links: "stop",
  updated_at: null,
  attribution_rule: "first_touch",
  max_attribution_days: 365,
};
const TERMS = {
  clearance_days: 21,
  referral_earning_days: 90,
  reversal_reasons: ["chargeback", "refund"],
  min_withdrawal_minor: 100000,
  withdrawal_currency: "INR",
  tds_rate_bp: 200,
  tds_currency: "INR",
};

function routes(extra: Record<string, Reply[]> = {}) {
  return {
    [`GET ${ADMIN}/commission/rules`]: [ok({ results: [RULE, FIXED] })],
    [`GET ${ADMIN}/commission/terms`]: [ok(TERMS)],
    [`GET ${ADMIN}/affiliate-policy`]: [ok(POLICY)],
    [`GET ${ADMIN}/catalog/products`]: [ok({ results: [{ id: "p1", code: "easy_crm", name: "Easy CRM" }] })],
    ...extra,
  };
}

function mount(permissions: string[]) {
  return render(
    <ToastProvider>
      <AdminContextForTest permissions={permissions}>
        <Page />
      </AdminContextForTest>
    </ToastProvider>,
  );
}

const ALL = [VIEW, MANAGE, PARTNER_VIEW, PARTNER_MANAGE];

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the rules", () => {
  it("shows each rule as the server holds it, and every section; passes axe", async () => {
    mockApi(routes());
    const { container } = mount(ALL);
    const table = await screen.findByRole("table", { name: "Commission rules" });
    const first = within(table).getByRole("row", { name: /programme v2/ });
    expect(within(first).getByText("12.5%")).toBeInTheDocument();
    expect(within(first).getByText("Renewals for 12 months")).toBeInTheDocument();
    expect(within(first).getByText("All partners, every product")).toBeInTheDocument();
    const fixed = within(table).getByRole("row", { name: /crm-launch v1/ });
    expect(within(fixed).getByText("First sale only")).toBeInTheDocument();
    expect(within(fixed).getByText(/per payment/)).toBeInTheDocument();
    expect(await screen.findByText("45 days")).toBeInTheDocument();
    expect(await screen.findByText("21 days after the payment")).toBeInTheDocument();
    expect(screen.getByText("Chargeback, Refund")).toBeInTheDocument();
    expect(screen.getByText("2% on INR withdrawals")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("says so when there are no rules", async () => {
    mockApi(routes({ [`GET ${ADMIN}/commission/rules`]: [ok({ results: [] })] }));
    mount([VIEW]);
    expect(await screen.findByText("No commission rules yet")).toBeInTheDocument();
  });

  it("says so when the rules can't be loaded", async () => {
    mockApi(routes({ [`GET ${ADMIN}/commission/rules`]: [{ status: 500, body: {} }] }));
    mount([VIEW]);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Commission rules" })).toBeNull();
  });

  it("asks the server for replaced versions rather than filtering what it has", async () => {
    const calls = mockApi(routes({ [`GET ${ADMIN}/commission/rules`]: [ok({ results: [RULE] }), ok({ results: [RULE, { ...RULE, id: "r0", version: 1, retired_at: "2026-01-01T00:00:00+00:00" }] })] }));
    mount([VIEW]);
    await screen.findByRole("row", { name: /programme v2/ });
    fireEvent.click(screen.getByRole("button", { name: "Show replaced versions" }));
    expect(await screen.findByRole("row", { name: /programme v1/ })).toBeInTheDocument();
    expect(screen.getByText("Replaced")).toBeInTheDocument();
    expect(calls.some((c) => c.key === `GET ${ADMIN}/commission/rules` && c.url.includes("all=1"))).toBe(true);
  });

  it("creates a rule with exactly what was entered and refreshes the list", async () => {
    const calls = mockApi(
      routes({
        [`POST ${ADMIN}/commission/rules`]: [{ status: 201, body: { id: "r3", code: "crm-q4", version: 1 } }],
        [`GET ${ADMIN}/commission/rules`]: [ok({ results: [RULE] }), ok({ results: [RULE, { ...RULE, id: "r3", code: "crm-q4", version: 1 }] })],
      }),
    );
    mount(ALL);
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));
    fireEvent.change(screen.getByLabelText(/Rule code/), { target: { value: "crm-q4" } });
    fireEvent.change(screen.getByLabelText("Product"), { target: { value: "easy_crm" } });
    fireEvent.change(screen.getByLabelText(/Commission %/), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Renewals"), { target: { value: "yes" } });
    fireEvent.change(screen.getByLabelText("For how many months"), { target: { value: "6" } });
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${ADMIN}/commission/rules`)?.body).toMatchObject({
      code: "crm-q4",
      product_code: "easy_crm",
      plan_code: "",
      rate_bp: 1500,
      fixed_amount_minor: null,
      fixed_currency: "",
      recurring: true,
      recurring_months: 6,
    }));
    expect(await screen.findByRole("row", { name: /crm-q4 v1/ })).toBeInTheDocument();
  });

  it("won't send a bad rate, and shows the server's refusal in the form", async () => {
    mockApi(
      routes({
        [`POST ${ADMIN}/commission/rules`]: [{ status: 400, body: { detail: "A commission rule cannot take effect in the past." } }],
      }),
    );
    mount(ALL);
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));
    fireEvent.change(screen.getByLabelText(/Rule code/), { target: { value: "crm-q4" } });
    const save = screen.getByRole("button", { name: "Create rule" });
    fireEvent.change(screen.getByLabelText(/Commission %/), { target: { value: "150" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Commission %/), { target: { value: "15" } });
    fireEvent.click(save);
    expect(await screen.findByText("A commission rule cannot take effect in the past.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create rule" })).toBeInTheDocument();
  });

  it("changes a rule by adding a version and keeps who it covers", async () => {
    const calls = mockApi(
      routes({
        [`GET ${ADMIN}/commission/rules`]: [ok({ results: [{ ...RULE, partner_id: "pp1", partner_name: "Acme Partners" }] })],
        [`POST ${ADMIN}/commission/rules`]: [{ status: 201, body: { id: "r4", code: "programme", version: 3 } }],
      }),
    );
    mount(ALL);
    await screen.findByRole("row", { name: /programme v2/ });
    fireEvent.click(screen.getByRole("button", { name: /Actions for programme version 2/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Change" }));
    expect(screen.getByText("Applies to: Acme Partners, every product")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Commission %/), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${ADMIN}/commission/rules`)?.body).toMatchObject({
      code: "programme",
      partner_id: "pp1",
      product_code: "",
      rate_bp: 2000,
    }));
  });

  it("hides every change from staff who may only look", async () => {
    mockApi(routes());
    mount([VIEW, PARTNER_VIEW]);
    await screen.findByRole("row", { name: /programme v2/ });
    await screen.findByText("45 days");
    expect(screen.queryByRole("button", { name: "New rule" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Change attribution" })).toBeNull();
  });
});

describe("the policy around the rules", () => {
  it("saves the attribution window with exactly what was entered", async () => {
    const calls = mockApi(routes({ [`PUT ${ADMIN}/affiliate-policy`]: [ok({ ...POLICY, attribution_days: 60 })] }));
    mount(ALL);
    fireEvent.click(await screen.findByRole("button", { name: "Change attribution" }));
    fireEvent.change(screen.getByLabelText(/can convert for/), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.key === `PUT ${ADMIN}/affiliate-policy`)?.body).toEqual({ attribution_days: 60, revoked_links: "stop" }));
  });

  it("reports what the ledger check found, including a problem", async () => {
    mockApi(
      routes({
        [`GET ${ADMIN}/commission/reconcile`]: [
          ok({ organizations: 4, is_clean: false, missing: [{}], duplicates: [], wrong: [{}], unexpected: [], skipped: {} }),
        ],
      }),
    );
    mount(ALL);
    fireEvent.click(await screen.findByRole("button", { name: "Check the ledger against sales" }));
    expect(await screen.findByText("2 differences found: 1 missing, 0 duplicated, 1 wrong, 0 unexpected.")).toBeInTheDocument();
  });
});
