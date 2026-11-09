import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CreditPacksScreen, UsageRatesScreen } from "./AdminUsagePricing";
import { PlansScreen } from "./AdminPlans";
import { AppLinksPanel } from "./productmanager/AppLinksPanel";
import { ToastProvider } from "./kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * Usage rates and credit packs (2026-11-09): every meter stays on the page, no rate
 * means no charge, AI tokens charge whole credits and everything else charges money.
 * Plus the plan's rules and a product's app links.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/usage-pricing",
}));

const VIEW = ["platform.staff_catalog.view"];
const MANAGE = [...VIEW, "platform.staff_pricing.manage"];

const RATES = "/api/v1/admin/usage-rates";
const PACKS = "/api/v1/admin/credit-packs";

const METERS = [
  { meter: "ai_tokens", label: "AI tokens" },
  { meter: "whatsapp", label: "WhatsApp messages" },
  { meter: "sms", label: "SMS messages" },
  { meter: "email", label: "Emails" },
  { meter: "api_trigger", label: "Billable API triggers" },
  { meter: "webhook", label: "Webhook deliveries" },
];

const rate = (over: Record<string, unknown> = {}) => ({
  id: "r1",
  meter: "sms",
  meter_label: "SMS messages",
  currency: "INR",
  unit_amount: 50,
  per_units: 1,
  included_units: 100,
  effective_from: "2026-11-01T00:00:00Z",
  effective_to: null,
  reason: "Launch price",
  ...over,
});

const aiRate = rate({ id: "r2", meter: "ai_tokens", meter_label: "AI tokens", currency: "", unit_amount: 2, per_units: 1000, included_units: 0 });

const closedRate = rate({ id: "r0", unit_amount: 30, effective_to: "2026-11-01T00:00:00Z", reason: "Old launch price" });

const pack = (over: Record<string, unknown> = {}) => ({
  id: "c1",
  code: "starter",
  name: "Starter",
  credits: 1000,
  price_minor: 49900,
  currency: "INR",
  status: "active",
  ...over,
});

const asStaff = (permissions: string[], ui: React.ReactElement) =>
  render(
    <AdminContextForTest permissions={permissions}>
      <ToastProvider>{ui}</ToastProvider>
    </AdminContextForTest>,
  );

const requests = (calls: Call[], key: string) => calls.filter((c) => c.key === key);

const typeInto = (name: RegExp | string, value: string) => fireEvent.change(screen.getByRole("textbox", { name }), { target: { value } });

const choose = (name: RegExp | string, option: string) => {
  fireEvent.click(screen.getByRole("combobox", { name }));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

const inDialog = (name: string) => within(screen.getByRole("dialog")).getByRole("button", { name });

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("usage rates", () => {
  const ratesApi = (results: unknown[] = [rate(), aiRate], extra: Record<string, Reply[]> = {}) =>
    mockApi({ [`GET ${RATES}`]: [ok({ meters: METERS, results })], ...extra });

  it("shows every meter, its price, what is included and from when; passes axe", async () => {
    ratesApi();
    const { container } = asStaff(MANAGE, <UsageRatesScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "Usage rates" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Usage rates by meter" });

    expect(within(table).getByText("₹0.50 per message")).toBeInTheDocument();
    expect(within(table).getByText("100 messages a month")).toBeInTheDocument();
    expect(within(table).getByText("2 credits per 1,000 tokens")).toBeInTheDocument();
    for (const { label } of METERS) expect(within(table).getByText(label)).toBeInTheDocument();
    expect(within(table).getAllByText("Not charged: no rate set")).toHaveLength(4);
    expect(screen.getByText("A new rate applies to future charges only. Past charges keep the rate they used.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("with no rate at all, still shows every meter as not charged", async () => {
    ratesApi([]);
    asStaff(VIEW, <UsageRatesScreen />);
    const table = await screen.findByRole("table", { name: "Usage rates by meter" });
    expect(within(table).getAllByText("Not charged: no rate set")).toHaveLength(METERS.length);
  });

  it("says when the rates could not be loaded, or are not allowed", async () => {
    mockApi({ [`GET ${RATES}`]: [{ status: 500, body: {} }] });
    const first = asStaff(VIEW, <UsageRatesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    first.unmount();

    mockApi({ [`GET ${RATES}`]: [{ status: 403, body: { detail: "No." } }] });
    asStaff(VIEW, <UsageRatesScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to usage rates.");
  });

  it("sets a money rate through toMinor, with the exact body", async () => {
    const calls = ratesApi(undefined, { [`POST ${RATES}`]: [{ status: 201, body: rate() }] });
    asStaff(MANAGE, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });

    fireEvent.click(screen.getByRole("button", { name: "Set rate" }));
    choose(/^Meter/, "Emails");
    choose(/^Currency/, "USD");
    expect(inDialog("Set rate")).toBeDisabled();
    typeInto(/^Price \(USD\)/, "1.25");
    typeInto(/Per how many emails/, "100");
    typeInto(/Emails included each month/, "500");
    typeInto(/Reason/, "New email supplier price");
    fireEvent.click(inDialog("Set rate"));

    await waitFor(() => expect(requests(calls, `POST ${RATES}`)).toHaveLength(1));
    expect(requests(calls, `POST ${RATES}`)[0].body).toEqual({
      meter: "email",
      currency: "USD",
      unit_amount: 125,
      per_units: 100,
      included_units: 500,
      reason: "New email supplier price",
    });
    expect(await screen.findByText("Rate set for Emails.")).toBeInTheDocument();
    await waitFor(() => expect(requests(calls, `GET ${RATES}`)).toHaveLength(2));
  });

  it("charges AI tokens in whole credits: no currency, and never a decimal", async () => {
    const calls = ratesApi([], { [`POST ${RATES}`]: [{ status: 201, body: aiRate }] });
    asStaff(MANAGE, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });

    fireEvent.click(screen.getByRole("button", { name: "Set rate for AI tokens" }));
    expect(screen.queryByRole("combobox", { name: /^Currency/ })).not.toBeInTheDocument();
    typeInto(/Credits charged/, "1.5");
    typeInto(/Per how many tokens/, "1000");
    typeInto(/Reason/, "First AI credit price");
    expect(inDialog("Set rate")).toBeDisabled();
    typeInto(/Credits charged/, "2");
    fireEvent.click(inDialog("Set rate"));

    await waitFor(() => expect(requests(calls, `POST ${RATES}`)).toHaveLength(1));
    expect(requests(calls, `POST ${RATES}`)[0].body).toEqual({
      meter: "ai_tokens",
      unit_amount: 2,
      per_units: 1000,
      included_units: 0,
      reason: "First AI credit price",
    });
  });

  it("fills the form from the live rate it replaces, and shows the server's refusal", async () => {
    const calls = ratesApi(undefined, { [`POST ${RATES}`]: [{ status: 400, body: { detail: "Rates must be above zero." } }] });
    asStaff(MANAGE, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });

    fireEvent.click(screen.getByRole("button", { name: "Set rate for SMS messages in INR" }));
    expect(screen.getByRole("textbox", { name: /^Price \(INR\)/ })).toHaveValue("0.50");
    expect(screen.getByRole("textbox", { name: /Messages included each month/ })).toHaveValue("100");
    typeInto(/Reason/, "A new SMS supplier price");
    fireEvent.click(inDialog("Set rate"));

    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("Rates must be above zero.");
    expect(requests(calls, `POST ${RATES}`)[0].body).toMatchObject({ meter: "sms", currency: "INR", unit_amount: 50 });
  });

  it("removes a rate after confirming, with the reason, saying the meter stops being charged", async () => {
    const path = "POST /api/v1/admin/usage-rates/r1/remove";
    const calls = ratesApi(undefined, { [path]: [ok({ ok: true })] });
    asStaff(MANAGE, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });

    fireEvent.click(screen.getByRole("button", { name: "Remove rate for SMS messages in INR" }));
    const box = screen.getByRole("dialog");
    expect(within(box).getByText(/This meter stops being charged/)).toBeInTheDocument();
    expect(requests(calls, path)).toHaveLength(0);
    fireEvent.change(within(box).getByRole("textbox", { name: /Reason/ }), { target: { value: "No longer sending any SMS" } });
    fireEvent.click(within(box).getByRole("button", { name: "Remove rate" }));

    await waitFor(() => expect(requests(calls, path)).toHaveLength(1));
    expect(requests(calls, path)[0].body).toEqual({ reason: "No longer sending any SMS" });
    expect(await screen.findByText("SMS messages is no longer charged.")).toBeInTheDocument();
  });

  it("shows a refused removal in its dialog", async () => {
    ratesApi(undefined, { "POST /api/v1/admin/usage-rates/r1/remove": [{ status: 404, body: { detail: "No such live rate." } }] });
    asStaff(MANAGE, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });

    fireEvent.click(screen.getByRole("button", { name: "Remove rate for SMS messages in INR" }));
    const box = screen.getByRole("dialog");
    fireEvent.click(within(box).getByRole("button", { name: "Remove rate" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("That doesn't exist");
  });

  it("lists closed rates only when asked for the history", async () => {
    const calls = mockApi({
      [`GET ${RATES}`]: [ok({ meters: METERS, results: [rate()] }), ok({ meters: METERS, results: [closedRate, rate()] })],
    });
    asStaff(VIEW, <UsageRatesScreen />);
    const live = await screen.findByRole("table", { name: "Usage rates by meter" });
    expect(within(live).getByText("₹0.50 per message")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Closed rates" })).not.toBeInTheDocument();
    expect(new URL(calls[0].url, "http://x").searchParams.get("history")).toBeNull();

    fireEvent.click(screen.getByRole("checkbox", { name: "Show closed rates" }));
    const closed = await screen.findByRole("table", { name: "Closed rates" });
    expect(new URL(calls[1].url, "http://x").searchParams.get("history")).toBe("1");
    expect(within(closed).getByText("₹0.30 per message")).toBeInTheDocument();
    expect(within(closed).getByText("Old launch price")).toBeInTheDocument();
    // The closed one is not the live one.
    expect(within(screen.getByRole("table", { name: "Usage rates by meter" })).queryByText("₹0.30 per message")).not.toBeInTheDocument();
  });

  it("hides Set rate and Remove rate without the pricing grant", async () => {
    ratesApi();
    asStaff(VIEW, <UsageRatesScreen />);
    await screen.findByRole("table", { name: "Usage rates by meter" });
    expect(screen.queryByRole("button", { name: /^Set rate/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove rate/ })).not.toBeInTheDocument();
  });
});

describe("credit packs", () => {
  const archived = pack({ id: "c2", code: "old", name: "Old pack", status: "archived", credits: 10, price_minor: 100 });

  it("lists packs in credits and money; passes axe", async () => {
    mockApi({ [`GET ${PACKS}`]: [ok({ results: [pack(), archived] })] });
    const { container } = asStaff(MANAGE, <CreditPacksScreen />);
    const table = await screen.findByRole("table", { name: "Credit packs" });
    expect(screen.getByRole("heading", { level: 1, name: "Credit packs" })).toBeInTheDocument();
    expect(within(table).getByText("Starter")).toBeInTheDocument();
    expect(within(table).getByText("1,000 credits")).toBeInTheDocument();
    expect(within(table).getByText("₹499.00")).toBeInTheDocument();
    expect(within(table).getByText("On sale")).toBeInTheDocument();
    expect(within(table).getByText("Archived")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("has an empty state and an error state", async () => {
    mockApi({ [`GET ${PACKS}`]: [ok({ results: [] })] });
    const first = asStaff(MANAGE, <CreditPacksScreen />);
    expect(await screen.findByText("No credit packs yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create the first pack" })).toBeInTheDocument();
    first.unmount();

    mockApi({ [`GET ${PACKS}`]: [{ status: 500, body: {} }] });
    asStaff(VIEW, <CreditPacksScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("creates a pack with the exact body, and keeps the server's refusal in the form", async () => {
    const calls = mockApi({
      [`GET ${PACKS}`]: [ok({ results: [] })],
      [`POST ${PACKS}`]: [{ status: 400, body: { detail: "Credits and price must be above zero." } }, { status: 201, body: pack() }],
    });
    asStaff(MANAGE, <CreditPacksScreen />);
    await screen.findByText("No credit packs yet");
    fireEvent.click(screen.getByRole("button", { name: "New credit pack" }));

    expect(inDialog("Create pack")).toBeDisabled();
    typeInto(/^Name/, "Starter");
    typeInto(/^Credits/, "1000.5");
    typeInto(/^Price \(INR\)/, "499");
    expect(inDialog("Create pack")).toBeDisabled();
    typeInto(/^Credits/, "1000");
    fireEvent.click(inDialog("Create pack"));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("Credits and price must be above zero.");

    fireEvent.click(inDialog("Create pack"));
    await waitFor(() => expect(requests(calls, `POST ${PACKS}`)).toHaveLength(2));
    expect(requests(calls, `POST ${PACKS}`)[1].body).toEqual({ name: "Starter", credits: 1000, price_minor: 49900, currency: "INR" });
    expect(await screen.findByText("Starter created.")).toBeInTheDocument();
  });

  it("edits a pack, keeping its status", async () => {
    const calls = mockApi({ [`GET ${PACKS}`]: [ok({ results: [pack()] })], "PUT /api/v1/admin/credit-packs/c1": [ok(pack())] });
    asStaff(MANAGE, <CreditPacksScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit Starter" }));

    expect(screen.getByRole("textbox", { name: /^Price \(INR\)/ })).toHaveValue("499.00");
    typeInto(/^Credits/, "2000");
    fireEvent.click(inDialog("Save pack"));

    await waitFor(() => expect(requests(calls, "PUT /api/v1/admin/credit-packs/c1")).toHaveLength(1));
    expect(requests(calls, "PUT /api/v1/admin/credit-packs/c1")[0].body).toEqual({
      name: "Starter",
      credits: 2000,
      price_minor: 49900,
      currency: "INR",
      status: "active",
    });
  });

  it("archives after confirming, and puts back on sale", async () => {
    const calls = mockApi({
      [`GET ${PACKS}`]: [ok({ results: [pack(), archived] })],
      "PUT /api/v1/admin/credit-packs/c1": [ok(pack({ status: "archived" }))],
      "PUT /api/v1/admin/credit-packs/c2": [ok(pack({ id: "c2" }))],
    });
    asStaff(MANAGE, <CreditPacksScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Archive Starter" }));
    const box = screen.getByRole("dialog");
    expect(within(box).getByText(/keep their credits/)).toBeInTheDocument();
    expect(requests(calls, "PUT /api/v1/admin/credit-packs/c1")).toHaveLength(0);
    fireEvent.click(within(box).getByRole("button", { name: "Archive pack" }));
    await waitFor(() => expect(requests(calls, "PUT /api/v1/admin/credit-packs/c1")).toHaveLength(1));
    expect(requests(calls, "PUT /api/v1/admin/credit-packs/c1")[0].body).toEqual({ name: "Starter", credits: 1000, price_minor: 49900, currency: "INR", status: "archived" });

    fireEvent.click(await screen.findByRole("button", { name: "Put Old pack back on sale" }));
    await waitFor(() => expect(requests(calls, "PUT /api/v1/admin/credit-packs/c2")).toHaveLength(1));
    expect(requests(calls, "PUT /api/v1/admin/credit-packs/c2")[0].body).toMatchObject({ name: "Old pack", status: "active" });
    expect(await screen.findByText("Old pack is back on sale.")).toBeInTheDocument();
  });

  it("hides every change without the pricing grant", async () => {
    mockApi({ [`GET ${PACKS}`]: [ok({ results: [pack(), archived] })] });
    asStaff(VIEW, <CreditPacksScreen />);
    await screen.findByRole("table", { name: "Credit packs" });
    for (const name of ["New credit pack", "Edit Starter", "Archive Starter", "Put Old pack back on sale"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});

describe("a plan's rules", () => {
  const plan = (over: Record<string, unknown> = {}) => ({
    id: "plan1",
    code: "crm_basic",
    name: "Basic",
    caption: "",
    description: "",
    status: "available",
    is_public: true,
    sort_order: 0,
    billing_type: "paid",
    kind: "standard",
    product: { id: "prod1", code: "crm", name: "CRM" },
    addon_for: null,
    versions: [],
    ...over,
  });
  const overview = (plans: unknown[]) => ({ products: [], plans, bundles: [], price_books: [], currencies: ["INR"], intervals: ["monthly"], billing_types: ["paid"] });
  const OVERVIEW = "GET /api/v1/admin/catalog/overview";
  const RULES = "PUT /api/v1/admin/catalog/plans/plan1/rules";
  const openPackages = async () => fireEvent.click(await screen.findByRole("tab", { name: /Product packages/ }));

  it("shows the plan's saved rules and sends the exact body", async () => {
    const calls = mockApi({
      [OVERVIEW]: [ok(overview([plan({ renewal_rule: "manual", change_rule: "upgrade_only", included_usage: { sms: 100 } })]))],
      [`GET ${RATES}`]: [ok({ meters: METERS, results: [] })],
      [RULES]: [ok({ renewal_rule: "none", change_rule: "upgrade_only", included_usage: { sms: 100, email: 50 } })],
    });
    asStaff([...MANAGE, "platform.staff_catalog.manage"], <PlansScreen />);
    await openPackages();
    fireEvent.click(await screen.findByRole("button", { name: "Rules for Basic" }));

    const box = screen.getByRole("dialog");
    expect(within(box).getByRole("combobox", { name: /^Renewal rule/ })).toHaveTextContent("Customer renews by hand");
    expect(within(box).getByRole("combobox", { name: /^Change rule/ })).toHaveTextContent("Upgrade only");
    expect(await within(box).findByRole("textbox", { name: /SMS messages/ })).toHaveValue("100");
    expect(within(box).queryByText(/could not read/)).not.toBeInTheDocument();

    choose(/^Renewal rule/, "Does not renew");
    typeInto(/^Emails/, "50");
    typeInto(/^Webhook deliveries/, "");
    fireEvent.click(within(box).getByRole("button", { name: "Save rules" }));

    await waitFor(() => expect(requests(calls, RULES)).toHaveLength(1));
    expect(requests(calls, RULES)[0].body).toEqual({ renewal_rule: "none", change_rule: "upgrade_only", included_usage: { sms: 100, email: 50 } });
    expect(await screen.findByText("Rules saved for Basic.")).toBeInTheDocument();
    await waitFor(() => expect(requests(calls, OVERVIEW)).toHaveLength(2));
  });

  it("shows the defaults, and says so, when the catalog does not carry the rules", async () => {
    const calls = mockApi({
      [OVERVIEW]: [ok(overview([plan()]))],
      [`GET ${RATES}`]: [ok({ meters: METERS, results: [] })],
      [RULES]: [{ status: 400, body: { detail: "Choose how this plan renews." } }],
    });
    asStaff(MANAGE, <PlansScreen />);
    await openPackages();
    fireEvent.click(await screen.findByRole("button", { name: "Rules for Basic" }));

    const box = screen.getByRole("dialog");
    expect(within(box).getByText(/could not read the plan's saved rules/)).toBeInTheDocument();
    expect(within(box).getByRole("combobox", { name: /^Renewal rule/ })).toHaveTextContent("Renews automatically");
    expect(within(box).getByRole("combobox", { name: /^Change rule/ })).toHaveTextContent("Upgrade or downgrade");

    await within(box).findByRole("textbox", { name: /SMS messages/ });
    typeInto(/SMS messages/, "1.5");
    expect(within(box).getByRole("button", { name: "Save rules" })).toBeDisabled();
    typeInto(/SMS messages/, "");
    fireEvent.click(within(box).getByRole("button", { name: "Save rules" }));
    await waitFor(() => expect(requests(calls, RULES)).toHaveLength(1));
    expect(requests(calls, RULES)[0].body).toEqual({ renewal_rule: "auto_renew", change_rule: "any", included_usage: {} });
    // A refusal stays in the popup.
    expect(await within(box).findByText("Choose how this plan renews.")).toBeInTheDocument();
  });

  it("offers Rules only with the pricing grant, and not on a bundle", async () => {
    mockApi({
      [OVERVIEW]: [
        ok({
          ...overview([plan()]),
          bundles: [{ id: "b1", code: "suite", name: "Suite", caption: "", description: "", status: "draft", is_public: false, kind: "suite", billing_type: "paid", versions: [] }],
        }),
      ],
    });
    const first = asStaff(["platform.staff_catalog.view", "platform.staff_catalog.manage"], <PlansScreen />);
    await openPackages();
    await screen.findByRole("button", { name: "Edit details of Basic" });
    expect(screen.queryByRole("button", { name: "Rules for Basic" })).not.toBeInTheDocument();
    first.unmount();

    asStaff(MANAGE, <PlansScreen />);
    await screen.findByText("Suite");
    expect(screen.queryByRole("button", { name: /Rules for/ })).not.toBeInTheDocument();
    await openPackages();
    expect(await screen.findByRole("button", { name: "Rules for Basic" })).toBeInTheDocument();
    // The pricing grant alone gives only this action.
    expect(screen.queryByRole("button", { name: "Edit details of Basic" })).not.toBeInTheDocument();
  });
});

describe("a product's app links", () => {
  const product = (over: Record<string, unknown> = {}) => ({ id: "prod1", ...over }) as unknown as React.ComponentProps<typeof AppLinksPanel>["product"];
  const PUT = "PUT /api/v1/admin/catalog/products/prod1/app-links";
  const panel = (p: React.ComponentProps<typeof AppLinksPanel>["product"], manage = true, reload: () => void = vi.fn()) =>
    render(<AppLinksPanel product={p} manage={manage} busy={false} reload={reload} />);

  it("shows what is saved, sends only filled https links, and reloads; passes axe", async () => {
    const reload = vi.fn();
    const calls = mockApi({ [PUT]: [ok({ app_links: { ios: "https://apps.apple.com/x", web: "https://app.example.com" } })] });
    const { container } = panel(product({ app_links: { web: "https://app.example.com" } }), true, reload);

    expect(screen.getByRole("heading", { name: "App links" })).toBeInTheDocument();
    expect(screen.getByText("app.example.com")).toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Edit app links" }));
    typeInto(/^iOS/, "https://apps.apple.com/x");
    fireEvent.click(inDialog("Save app links"));

    await waitFor(() => expect(requests(calls, PUT)).toHaveLength(1));
    expect(requests(calls, PUT)[0].body).toEqual({ app_links: { ios: "https://apps.apple.com/x", web: "https://app.example.com" } });
    expect(await screen.findByText("apps.apple.com/x")).toBeInTheDocument();
    expect(reload).toHaveBeenCalled();
  });

  it("refuses anything but https before sending, and shows the server's refusal", async () => {
    const calls = mockApi({ [PUT]: [{ status: 400, body: { detail: "An app link must be a secure web address (https)." } }] });
    panel(product({ app_links: {} }));

    fireEvent.click(screen.getByRole("button", { name: "Edit app links" }));
    typeInto(/^Android/, "http://play.example.com/x");
    expect(screen.getByText("Use an https:// address.")).toBeInTheDocument();
    expect(inDialog("Save app links")).toBeDisabled();

    typeInto(/^Android/, "https://play.example.com/x");
    fireEvent.click(inDialog("Save app links"));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("An app link must be a secure web address (https).");
    expect(requests(calls, PUT)).toHaveLength(1);
  });

  it("warns when the saved links could not be read, because saving replaces them", () => {
    mockApi({});
    panel(product());
    fireEvent.click(screen.getByRole("button", { name: "Edit app links" }));
    expect(within(screen.getByRole("dialog")).getByText(/could not read the links already saved/)).toBeInTheDocument();
  });

  it("hides the edit without the catalog grant", () => {
    mockApi({});
    panel(product({ app_links: { web: "https://app.example.com" } }), false);
    expect(screen.getByText("app.example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit app links" })).not.toBeInTheDocument();
  });
});
