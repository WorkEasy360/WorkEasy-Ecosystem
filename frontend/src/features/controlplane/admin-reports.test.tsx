import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ReportScreen } from "./AdminReports";
import { dayBefore } from "./AdminListParts";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Admin > Reports (2026-11-09): one screen for eight reports. Every cell is written by the
 * kind the server gives its column; no figure is added up; the export carries the filters
 * on the screen.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/reports/sales",
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

const VIEW = "platform.staff_report.view";
const CATALOG = "platform.staff_catalog.view";
const T = "2026-11-08T10:00:00Z";

const asStaff = (permissions: string[], ui: React.ReactNode) => render(<AdminContextForTest permissions={permissions}>{ui}</AdminContextForTest>);

const report = (over: Record<string, unknown>) => ({
  from: "2026-10-11",
  to: "2026-11-09",
  total: 60,
  limit: 25,
  offset: 0,
  ...over,
});

const SALES = report({
  key: "sales",
  label: "Sales",
  description: "Invoices issued in the period.",
  columns: [
    { key: "number", label: "Invoice", kind: "text" },
    { key: "organization_name", label: "Customer", kind: "text" },
    { key: "issued_at", label: "Issued", kind: "date" },
    { key: "status", label: "Status", kind: "status" },
    { key: "total_minor", label: "Total", kind: "money" },
    { key: "due", label: "Nothing here", kind: "text" },
  ],
  results: [
    { number: "INV-1", organization_name: "Acme", issued_at: T, status: "paid", total_minor: 123456, currency: "INR", due: null },
    { number: "INV-2", organization_name: "Bright", issued_at: T, status: "void", total_minor: 5000, currency: "USD", due: "" },
  ],
});

const WALLETS = report({
  key: "wallet-movements",
  label: "Wallet Movements",
  description: "Every movement on the three balances.",
  columns: [
    { key: "wallet_kind", label: "Wallet", kind: "text" },
    { key: "amount", label: "Amount", kind: "amount" },
    { key: "balance_after", label: "Balance after", kind: "amount" },
  ],
  results: [
    { wallet_kind: "Platform Wallet", amount: -2500, balance_after: 100000, currency: "INR" },
    { wallet_kind: "AI Credits", amount: 4200, balance_after: 5000, currency: "" },
  ],
  total: 2,
});

const USAGE = report({
  key: "usage",
  label: "Usage by Product",
  description: "What products reported.",
  columns: [
    { key: "product_name", label: "Product", kind: "text" },
    { key: "meter", label: "Meter", kind: "text" },
    { key: "outcome", label: "Outcome", kind: "status" },
    { key: "quantity", label: "Units", kind: "number" },
    { key: "amount", label: "Charged", kind: "amount" },
  ],
  results: [{ product_name: "Easy CRM", meter: "ai_tokens", outcome: "charged", quantity: 12345, amount: 900 }],
  total: 1,
});

const PRODUCTS = { results: [{ id: "x1", code: "easy_crm", name: "Easy CRM" }] };
const routes = (kind: string, body: Reply, extra: Record<string, Reply[]> = {}) => ({
  [`GET /api/v1/admin/reports/${kind}`]: [body],
  ...extra,
});
const runs = (calls: Call[], kind: string) => calls.filter((c) => c.key === `GET /api/v1/admin/reports/${kind}`).map((c) => new URL(c.url, "http://x").searchParams);

describe("a report", () => {
  it("takes its title and description from the server and writes each cell by its kind; passes axe", async () => {
    mockApi(routes("sales", ok(SALES)));
    const { container } = asStaff([VIEW], <ReportScreen kind="sales" />);
    expect(screen.getByRole("heading", { level: 1, name: "Sales" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Sales" });
    expect(screen.getByText("Invoices issued in the period.")).toBeInTheDocument();
    const first = within(table).getByRole("row", { name: /INV-1/ });
    expect(within(first).getByText("₹1,234.56")).toBeInTheDocument();
    expect(within(first).getByText("Paid")).toBeInTheDocument();
    expect(within(first).getByText("8 Nov 2026", { exact: false })).toBeInTheDocument();
    // Each row in its own currency; a column stays even when it is empty.
    expect(within(table).getByRole("row", { name: /INV-2/ })).toHaveTextContent("$50.00");
    expect(within(table).getByRole("columnheader", { name: "Nothing here" })).toBeInTheDocument();
    expect(screen.getByText("Period: 11 Oct 2026 to 9 Nov 2026")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–2 of 60")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("writes an amount as money with a currency, and as whole credits or units without one", async () => {
    mockApi(routes("wallet-movements", ok(WALLETS)));
    const view = asStaff([VIEW], <ReportScreen kind="wallet-movements" />);
    const table = await screen.findByRole("table", { name: "Wallet Movements" });
    expect(within(table).getByRole("row", { name: /Platform Wallet/ })).toHaveTextContent("-₹25.00");
    const credits = within(table).getByRole("row", { name: /AI Credits/ });
    expect(within(credits).getByText("4,200")).toBeInTheDocument();
    expect(credits).not.toHaveTextContent("₹");
    view.unmount();

    mockApi(routes("usage", ok(USAGE)));
    asStaff([VIEW], <ReportScreen kind="usage" />);
    const usage = await screen.findByRole("table", { name: "Usage by Product" });
    expect(within(usage).getByText("12,345")).toBeInTheDocument();
    expect(within(usage).getByText("900")).toBeInTheDocument();
    expect(within(usage).getByText("AI tokens")).toBeInTheDocument();
    expect(within(within(usage).getByRole("row", { name: /Easy CRM/ })).getByText("Charged")).toBeInTheDocument();
  });

  it("opens on the last 30 days, shown, and asks for 25 rows", async () => {
    const calls = mockApi(routes("sales", ok(SALES)));
    asStaff([VIEW], <ReportScreen kind="sales" />);
    await screen.findByRole("table", { name: "Sales" });
    expect(screen.getByLabelText("From")).toHaveValue(dayBefore(29));
    expect(screen.getByLabelText("To")).toHaveValue(dayBefore(0));
    const params = runs(calls, "sales")[0];
    expect(Object.fromEntries(params)).toEqual({ from: dayBefore(29), to: dayBefore(0), limit: "25", offset: "0" });
  });

  it("sends every filter, returns to the first page, and pages through the server", async () => {
    const calls = mockApi(routes("sales", ok(SALES), { "GET /api/v1/admin/catalog/products": [ok(PRODUCTS)] }));
    asStaff([VIEW, CATALOG], <ReportScreen kind="sales" />);
    await screen.findByRole("table", { name: "Sales" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(runs(calls, "sales").at(-1)?.get("offset")).toBe("25"));
    await screen.findByRole("table", { name: "Sales" });
    fireEvent.click(await screen.findByRole("combobox", { name: "Product" }));
    fireEvent.click(screen.getByRole("option", { name: "Easy CRM" }));
    await waitFor(() => expect(runs(calls, "sales").at(-1)?.get("product")).toBe("easy_crm"));
    expect(runs(calls, "sales").at(-1)?.get("offset")).toBe("0");
    await screen.findByRole("table", { name: "Sales" });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(runs(calls, "sales").at(-1)?.get("from")).toBe("2026-11-01"));
    await screen.findByRole("table", { name: "Sales" });
    fireEvent.change(screen.getByLabelText("Search by invoice number"), { target: { value: "INV" } });
    await waitFor(() => expect(runs(calls, "sales").at(-1)?.get("q")).toBe("INV"));
  });

  it("links the export to the same filters, without a page", async () => {
    mockApi(routes("sales", ok(SALES), { "GET /api/v1/admin/catalog/products": [ok(PRODUCTS)] }));
    asStaff([VIEW, CATALOG], <ReportScreen kind="sales" />);
    await screen.findByRole("table", { name: "Sales" });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(screen.getByLabelText("From")).toHaveValue("2026-11-01"));
    const link = await screen.findByRole("link", { name: "Export CSV" });
    const url = new URL(link.getAttribute("href") ?? "", "http://x");
    expect(url.pathname).toBe("/api/v1/admin/reports/sales");
    expect(Object.fromEntries(url.searchParams)).toEqual({ format: "csv", from: "2026-11-01", to: dayBefore(0) });
  });

  it("does not offer a product filter a report ignores, or one the grants cannot fill", async () => {
    const calls = mockApi(routes("payouts", ok({ ...SALES, key: "payouts", label: "Payouts", description: "Payouts prepared." })));
    const view = asStaff([VIEW, CATALOG], <ReportScreen kind="payouts" />);
    await screen.findByRole("table", { name: "Payouts" });
    expect(screen.queryByRole("combobox", { name: "Product" })).toBeNull();
    expect(calls.map((c) => c.key)).not.toContain("GET /api/v1/admin/catalog/products");
    view.unmount();

    const second = mockApi(routes("sales", ok(SALES)));
    asStaff([VIEW], <ReportScreen kind="sales" />);
    await screen.findByRole("table", { name: "Sales" });
    expect(screen.queryByRole("combobox", { name: "Product" })).toBeNull();
    expect(second.map((c) => c.key)).not.toContain("GET /api/v1/admin/catalog/products");
  });

  it("says commissions and payouts are earnings, and only there", async () => {
    mockApi(routes("commissions", ok({ ...SALES, key: "commissions", label: "Commissions" })));
    const view = asStaff([VIEW], <ReportScreen kind="commissions" />);
    expect(await screen.findByText("Earnings, not wallet balances")).toBeInTheDocument();
    view.unmount();

    mockApi(routes("payouts", ok({ ...SALES, key: "payouts", label: "Payouts" })));
    const again = asStaff([VIEW], <ReportScreen kind="payouts" />);
    expect(await screen.findByText("Earnings, not wallet balances")).toBeInTheDocument();
    again.unmount();

    mockApi(routes("sales", ok(SALES)));
    asStaff([VIEW], <ReportScreen kind="sales" />);
    await screen.findByRole("table", { name: "Sales" });
    expect(screen.queryByText("Earnings, not wallet balances")).toBeNull();
  });

  it("says when a period is empty, with nothing to export", async () => {
    mockApi(routes("sales", ok({ ...SALES, results: [], total: 0 })));
    const { container } = asStaff([VIEW], <ReportScreen kind="sales" />);
    expect(await screen.findByText("Nothing in this period")).toBeInTheDocument();
    // The export stays on the page, disabled, with the reason written beside it.
    expect(screen.queryByRole("link", { name: "Export CSV" })).toBeNull();
    const off = screen.getByRole("button", { name: "Export CSV" });
    expect(off).toBeDisabled();
    expect(off).toHaveAccessibleDescription("Nothing to export in this period.");
    expect(screen.queryByRole("navigation", { name: "Pages" })).toBeNull();
    await expectNoViolations(container);
  });

  it("shows a refusal in the server's words, keeps the filters, and says when access is missing", async () => {
    mockApi(routes("sales", { status: 400, body: { detail: "The period ends before it starts." } }));
    const view = asStaff([VIEW], <ReportScreen kind="sales" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The period ends before it starts.");
    expect(screen.getByLabelText("From")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Sales" })).toBeInTheDocument();
    view.unmount();

    mockApi(routes("sales", { status: 403, body: { detail: "Forbidden." } }));
    asStaff([], <ReportScreen kind="sales" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to this report.");
  });

  it("shows that it is loading", () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => undefined)) as typeof fetch;
    asStaff([VIEW], <ReportScreen kind="sales" />);
    expect(screen.getByText("Loading this report…")).toBeInTheDocument();
  });
});
