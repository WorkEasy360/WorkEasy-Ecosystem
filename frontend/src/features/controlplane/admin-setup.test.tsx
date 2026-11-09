import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CustomerSetupScreen } from "./AdminSetup";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Admin → Customers → Setup (2026-11-09). A state of its own, never a subscription's:
 * reading needs the customer grant, deciding the subscription grant, and what a product
 * asks for the catalog grant.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/customers/setup",
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

const SETUP = "/api/v1/admin/setup";
const REQUIREMENTS = `${SETUP}/requirements`;
const PRODUCTS = "/api/v1/admin/catalog/products";
const T = "2026-11-01T10:00:00Z";

const VIEW = ["platform.staff_customer.view"];
const DECIDE = [...VIEW, "platform.staff_subscription.manage"];
const CATALOG_VIEW = [...VIEW, "platform.staff_catalog.view"];
const CATALOG_MANAGE = [...VIEW, "platform.staff_catalog.manage"];

const REQUIREMENT = {
  id: "q1",
  product_code: "easy_crm",
  key: "abc123",
  label: "Sales region",
  help_text: "Where you sell.",
  is_required: true,
  is_active: true,
  display_order: 2,
};

const RECORD = {
  id: "s1",
  organization_id: "o1",
  organization_name: "BrightMart",
  product_code: "easy_crm",
  status: "submitted",
  answers: { abc123: "South India" },
  submitted_at: T,
  decided_at: null,
  decided_by_name: "",
  decision_reason: "",
  version: 4,
};

const COUNTS = { pending: 2, submitted: 3, correction_required: 1, approved: 4 };

const queue = (results: unknown[], extra: Record<string, unknown> = {}) =>
  ok({ results, total: results.length, limit: 25, offset: 0, counts: COUNTS, requirements: [REQUIREMENT], ...extra });

const PRODUCT_LIST = ok({ results: [{ id: "p1", code: "easy_crm", name: "Easy CRM (easy_crm)" }, { id: "p2", code: "easybook", name: "EasyBook" }] });

function routes(extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return { [`GET ${SETUP}`]: [queue([RECORD])], [`GET ${PRODUCTS}`]: [PRODUCT_LIST], [`GET ${REQUIREMENTS}`]: [ok({ results: [REQUIREMENT] })], ...extra };
}

const open = (permissions: string[]) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <ToastProvider>
        <main>
          <CustomerSetupScreen />
        </main>
      </ToastProvider>
    </AdminContextForTest>,
  );

const lastUrl = (calls: Call[]) => new URL(calls.filter((c) => c.key === `GET ${SETUP}`).at(-1)!.url, "https://x.invalid");
const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;

describe("the setup queue", () => {
  it("shows organization, product, status and answers, says setup is separate, and passes axe", async () => {
    mockApi(routes());
    const { container } = open(CATALOG_VIEW);
    const row = await screen.findByRole("row", { name: /BrightMart/ });
    expect(within(row).getByRole("link", { name: "BrightMart" })).toHaveAttribute("href", "/admin/customers/o1");
    await within(row).findByText("Easy CRM");
    expect(row).toHaveTextContent("Submitted");
    expect(row).toHaveTextContent("Sales region");
    expect(row).toHaveTextContent("South India");
    expect(screen.getByText(/Setup is separate from subscription status/)).toBeInTheDocument();
    await screen.findByRole("table", { name: "Setup requirements" });
    await expectNoViolations(container);
  });

  it("shows the counts and asks the server for the chosen status", async () => {
    const calls = mockApi(routes());
    open(VIEW);
    await screen.findByRole("row", { name: /BrightMart/ });
    const chips = within(screen.getByRole("group", { name: "Setup status" }));
    expect(chips.getByRole("button", { name: /^Pending setup/ })).toHaveTextContent("2");
    expect(chips.getByRole("button", { name: /^Submitted/ })).toHaveTextContent("3");
    expect(chips.getByRole("button", { name: /^Correction required/ })).toHaveTextContent("1");
    expect(chips.getByRole("button", { name: /^Approved/ })).toHaveTextContent("4");
    fireEvent.click(chips.getByRole("button", { name: /^Pending setup/ }));
    await waitFor(() => expect(lastUrl(calls).searchParams.get("status")).toBe("pending"));
  });

  it("filters by product on the server, and hides counts that would not match", async () => {
    const calls = mockApi(routes());
    open(VIEW);
    fireEvent.click(await screen.findByRole("combobox", { name: "Product" }));
    fireEvent.click(screen.getByRole("option", { name: "EasyBook" }));
    await waitFor(() => expect(lastUrl(calls).searchParams.get("product")).toBe("easybook"));
    expect(within(screen.getByRole("group", { name: "Setup status" })).getByRole("button", { name: /^Approved/ })).not.toHaveTextContent("4");
  });

  it("pages by offset", async () => {
    const calls = mockApi(routes({ [`GET ${SETUP}`]: [queue([RECORD], { total: 40 })] }));
    open(VIEW);
    await screen.findByText("Showing 1–1 of 40");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastUrl(calls).searchParams.get("offset")).toBe("25"));
  });

  it("has an empty state", async () => {
    mockApi(routes({ [`GET ${SETUP}`]: [queue([])] }));
    open(VIEW);
    expect(await screen.findByText("No setup records")).toBeInTheDocument();
  });

  it("has an error state", async () => {
    mockApi(routes({ [`GET ${SETUP}`]: [{ status: 500, body: { detail: "Setup is unavailable." } }] }));
    open(VIEW);
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup is unavailable.");
  });

  it("still lists records when the product list is refused", async () => {
    const calls = mockApi(routes({ [`GET ${PRODUCTS}`]: [{ status: 403, body: {} }] }));
    open(VIEW);
    expect(await screen.findByRole("row", { name: /BrightMart/ })).toHaveTextContent("Easy CRM");
    await waitFor(() => expect(calls.some((c) => c.key === `GET ${PRODUCTS}`)).toBe(true));
    expect(screen.queryByRole("combobox", { name: "Product" })).not.toBeInTheDocument();
  });

  it("approves with the version read, and refreshes", async () => {
    const calls = mockApi(routes({ [`POST ${SETUP}/s1/decision`]: [ok(RECORD)] }));
    open(DECIDE);
    fireEvent.click(await screen.findByRole("button", { name: /^Approve BrightMart/ }));
    const popup = screen.getByRole("dialog", { name: "Approve setup" });
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Setup approved.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${SETUP}/s1/decision`)).toEqual({ decision: "approve", expected_version: 4 });
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${SETUP}`).length).toBe(2));
  });

  it("requests a correction only with a reason", async () => {
    const calls = mockApi(routes({ [`POST ${SETUP}/s1/decision`]: [ok(RECORD)] }));
    open(DECIDE);
    fireEvent.click(await screen.findByRole("button", { name: /^Request correction from BrightMart/ }));
    const popup = screen.getByRole("dialog", { name: "Request a correction" });
    const send = within(popup).getByRole("button", { name: "Request correction" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("What to correct"), { target: { value: "Name the sales region in full." } });
    fireEvent.click(send);
    expect(await screen.findByText("Correction requested. The customer can answer again.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${SETUP}/s1/decision`)).toEqual({ decision: "request_correction", reason: "Name the sales region in full.", expected_version: 4 });
  });

  it("keeps a refused decision in the dialog", async () => {
    mockApi(routes({ [`POST ${SETUP}/s1/decision`]: [{ status: 400, body: { detail: "This changed in the meantime. Reload." } }] }));
    open(DECIDE);
    fireEvent.click(await screen.findByRole("button", { name: /^Approve BrightMart/ }));
    const popup = screen.getByRole("dialog", { name: "Approve setup" });
    fireEvent.click(within(popup).getByRole("button", { name: "Approve" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("This changed in the meantime. Reload.");
  });

  it("hides the decisions without the subscription grant, and on a record not submitted", async () => {
    mockApi(routes());
    const { unmount } = open(VIEW);
    await screen.findByRole("row", { name: /BrightMart/ });
    expect(screen.queryByRole("button", { name: /^Approve / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Request correction/ })).not.toBeInTheDocument();
    unmount();

    mockApi(routes({ [`GET ${SETUP}`]: [queue([{ ...RECORD, status: "pending", answers: {} }])] }));
    open(DECIDE);
    expect(await screen.findByRole("row", { name: /BrightMart/ })).toHaveTextContent("Nothing sent yet");
    expect(screen.queryByRole("button", { name: /^Approve / })).not.toBeInTheDocument();
  });
});

describe("setup requirements", () => {
  it("are not asked for without a catalog grant", async () => {
    const calls = mockApi(routes());
    open(VIEW);
    await screen.findByRole("row", { name: /BrightMart/ });
    expect(screen.queryByRole("heading", { name: "Setup requirements" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.key === `GET ${REQUIREMENTS}`)).toBe(false);
  });

  it("are read-only with the view grant", async () => {
    mockApi(routes());
    open(CATALOG_VIEW);
    expect(await screen.findByRole("row", { name: /Where you sell\./ })).toHaveTextContent("Sales region");
    expect(screen.queryByRole("button", { name: "Add requirement" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Sales region" })).not.toBeInTheDocument();
  });

  it("explain what having none means", async () => {
    mockApi(routes({ [`GET ${REQUIREMENTS}`]: [ok({ results: [] })] }));
    open(CATALOG_VIEW);
    expect(await screen.findByText(/no product needs setup/)).toBeInTheDocument();
  });

  it("show their own error state", async () => {
    mockApi(routes({ [`GET ${REQUIREMENTS}`]: [{ status: 500, body: { detail: "Requirements are unavailable." } }] }));
    open(CATALOG_VIEW);
    expect(await screen.findByRole("alert")).toHaveTextContent("Requirements are unavailable.");
  });

  it("add one for a chosen product, with the exact body", async () => {
    const calls = mockApi(routes({ [`POST ${REQUIREMENTS}`]: [{ status: 201, body: REQUIREMENT }] }));
    open(CATALOG_MANAGE);
    await screen.findByRole("combobox", { name: "Product" });
    fireEvent.click(await screen.findByRole("button", { name: "Add requirement" }));
    const popup = screen.getByRole("dialog", { name: "Add a setup requirement" });
    const add = within(popup).getByRole("button", { name: "Add requirement" });
    fireEvent.change(within(popup).getByLabelText("Label"), { target: { value: "Time zone" } });
    expect(add).toBeDisabled();
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Product/ }));
    fireEvent.click(screen.getByRole("option", { name: "EasyBook" }));
    fireEvent.click(add);
    expect(await screen.findByText("Requirement added.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${REQUIREMENTS}`)).toEqual({ product_code: "easybook", label: "Time zone", help_text: "", is_required: true, is_active: true, display_order: 0 });
    await waitFor(() => expect(calls.filter((c) => c.key === `GET ${REQUIREMENTS}`).length).toBe(2));
  });

  it("edit one, keeping its product", async () => {
    const calls = mockApi(routes({ [`PUT ${REQUIREMENTS}/q1`]: [ok(REQUIREMENT)] }));
    open(CATALOG_MANAGE);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Sales region" }));
    const popup = screen.getByRole("dialog", { name: "Edit Sales region" });
    fireEvent.change(within(popup).getByLabelText("Order"), { target: { value: "9" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Requirement saved.")).toBeInTheDocument();
    expect(bodyOf(calls, `PUT ${REQUIREMENTS}/q1`)).toEqual({
      product_code: "easy_crm",
      label: "Sales region",
      help_text: "Where you sell.",
      is_required: true,
      is_active: true,
      display_order: 9,
    });
  });

  it("keep a refusal in the form", async () => {
    mockApi(routes({ [`PUT ${REQUIREMENTS}/q1`]: [{ status: 403, body: { detail: "You need the catalog grant." } }] }));
    open(CATALOG_MANAGE);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Sales region" }));
    const popup = screen.getByRole("dialog", { name: "Edit Sales region" });
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("You need the catalog grant.");
  });
});
