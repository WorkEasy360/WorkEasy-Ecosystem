import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AddPartner } from "./AddPartner";
import { AdminContextForTest, expectNoViolations, mockApi, type Call, type Reply } from "./testSupport";

/**
 * Partners → Add Partner (ADR-048): the short form - partner name, admin email,
 * password and confirmation, partner type, optional country. What these tests
 * hold on to:
 *
 * - it asks for nothing more: no business details, products, prices or
 *   commission (those are Complete Your Profile's);
 * - the password is typed into `autocomplete="new-password"` inputs, goes in the
 *   one create request and nowhere else (no storage, no cookie, no other call),
 *   and is emptied once the request has been made;
 * - an address that already has an account is never overwritten - the form
 *   offers to link it (no password sent) or to cancel;
 * - the type is sent as `experience` and nothing names a capability;
 * - after creating, it opens the partner's page with the created flag.
 */

let query = "";
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push, refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/partners/new",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
  query = "";
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage"];
const PASSWORD = "Violet Harbor Lantern 2049";
const EXISTING = "This email already belongs to an existing Workeasy360 account.";

const CREATED = {
  id: "p-new",
  code: "abc-technologies",
  display_name: "ABC Technologies",
  status: "draft",
  experience: "reseller",
  default_experience: "reseller",
  administrator: { email: "admin@abctech.example", account_created: true },
  capabilities: ["customer.provision"],
  payout_currency: "INR",
};

function mount(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <AddPartner />
    </AdminContextForTest>,
  );
}

const field = (name: RegExp) => screen.getByLabelText(name, { selector: "input, select" }) as HTMLInputElement;

function fill({ name = "ABC Technologies", email = "admin@abctech.example", password = PASSWORD, confirm = PASSWORD, type = /^Reseller/ } = {}) {
  fireEvent.change(field(/^Partner name/), { target: { value: name } });
  fireEvent.change(field(/^Admin email/), { target: { value: email } });
  if (password !== null) fireEvent.change(field(/^Password/), { target: { value: password } });
  if (confirm !== null) fireEvent.change(field(/^Confirm password/), { target: { value: confirm } });
  if (type) fireEvent.click(screen.getByRole("radio", { name: type }));
}

const create = () => fireEvent.click(screen.getByRole("button", { name: "Create Partner" }));
const createCalls = (calls: Call[]) => calls.filter((c) => c.key === "POST /api/v1/admin/partners/create");

describe("the short form", () => {
  // A large form plus an axe pass: slow in jsdom when the whole suite runs in parallel.
  it("asks only for the name, the admin email and password, the type and an optional country", async () => {
    mockApi({});
    const { container } = mount();
    expect(screen.getByRole("heading", { level: 1, name: "Add Partner" })).toBeInTheDocument();
    expect(field(/^Partner name/)).toBeRequired();
    expect(field(/^Admin email/)).toBeRequired();
    expect(field(/^Password/)).toHaveAttribute("type", "password");
    expect(field(/^Password/)).toHaveAttribute("autocomplete", "new-password");
    expect(field(/^Confirm password/)).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText(/^Country \(optional\)/)).not.toBeRequired();
    const types = screen.getAllByRole("radio");
    expect(types.map((r) => r.getAttribute("value"))).toEqual(["reseller", "white_label", "affiliate"]);
    expect(screen.getByText("Sell Workeasy360 products and manage customers.")).toBeInTheDocument();
    expect(screen.getByText("Sell Workeasy360 products using your own brand and domain.")).toBeInTheDocument();
    expect(screen.getByText("Promote Workeasy360 products and earn commissions.")).toBeInTheDocument();
    // Nothing about the business, products, prices or commission is asked here.
    for (const absent of [/business email/i, /products?/i, /price/i, /commission rate/i, /legal/i, /partner code/i]) {
      expect(screen.queryByLabelText(absent)).not.toBeInTheDocument();
    }
    expect(screen.getAllByRole("textbox").length + screen.getAllByRole("combobox").length).toBeLessThanOrEqual(3);
    await expectNoViolations(container);
  }, 60_000);

  it("preselects the card named in the address without choosing a capability", () => {
    query = "type=white-label";
    mockApi({});
    mount();
    expect(screen.getByRole("radio", { name: /^White-label/ })).toBeChecked();
  });

  it("says what is missing and sends nothing", async () => {
    const calls = mockApi({});
    mount();
    create();
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Enter the partner's name.");
    expect(summary).toHaveTextContent("Enter the partner administrator's email address.");
    expect(summary).toHaveTextContent("Enter a password.");
    expect(summary).toHaveTextContent("Choose the partner type.");
    expect(calls).toHaveLength(0);
  });

  it("checks the password length and match before anything is sent", async () => {
    const calls = mockApi({});
    mount();
    // Five characters: under the minimum length.
    fill({ password: "x".repeat(5), confirm: "y".repeat(5) });
    create();
    expect((await screen.findAllByText("The password must be at least 12 characters.")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("The two passwords do not match.").length).toBeGreaterThan(0);
    expect(calls).toHaveLength(0);
  });

  it("is refused to staff who may not manage partners", () => {
    mockApi({});
    mount(["platform.staff_partner.view"]);
    expect(screen.getByRole("alert")).toHaveTextContent(/permission to manage partners/);
    expect(screen.queryByLabelText(/^Partner name/)).not.toBeInTheDocument();
  });
});

describe("creating", () => {
  it("sends only the few fields, then opens All Partners with the new row marked", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [{ status: 201, body: CREATED }] });
    mount();
    fill({ type: /^Reseller/ });
    fireEvent.change(screen.getByLabelText(/^Country \(optional\)/), { target: { value: "IN" } });
    create();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/all?created=p-new"));
    const [call] = createCalls(calls);
    expect(call.body).toEqual({
      experience: "reseller",
      partner: { display_name: "ABC Technologies", country: "IN" },
      administrator: { email: "admin@abctech.example", mode: "create", password: PASSWORD, password_confirm: PASSWORD },
    });
    // No other request carried the password, and nothing was stored.
    expect(calls.filter((c) => JSON.stringify(c.body ?? {}).includes(PASSWORD))).toHaveLength(1);
    expect(sessionStorage.length + localStorage.length).toBe(0);
    expect(document.cookie).not.toContain(PASSWORD);
  });

  it("leaves the country out when none is chosen", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [{ status: 201, body: CREATED }] });
    mount();
    fill({ type: /^Affiliate/ });
    create();
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect((createCalls(calls)[0].body as { partner: unknown }).partner).toEqual({ display_name: "ABC Technologies" });
    expect((createCalls(calls)[0].body as { experience: string }).experience).toBe("affiliate");
  });

  it("shows the server's password problems against the field and empties both inputs", async () => {
    mockApi({
      "POST /api/v1/admin/partners/create": [
        {
          status: 400,
          body: {
            code: "partner_invalid",
            detail: "This password is too common.",
            problems: [{ field: "administrator.password", message: "This password is too common." }],
          },
        },
      ],
    });
    mount();
    fill();
    create();
    expect((await screen.findAllByText("This password is too common.")).length).toBeGreaterThan(0);
    await waitFor(() => expect(field(/^Password/).value).toBe(""));
    expect(field(/^Confirm password/).value).toBe("");
    expect(push).not.toHaveBeenCalled();
  });

});

describe("an email that already has an account", () => {
  const taken: Reply = {
    status: 400,
    body: {
      code: "partner_invalid",
      detail: EXISTING,
      problems: [
        { field: "administrator.email", message: EXISTING },
        { field: "administrator.password", message: "This password is too similar to the email address." },
      ],
    },
  };

  it("says so and offers to link or cancel - its password is never sent again", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [taken, { status: 201, body: CREATED }] });
    const { container } = mount();
    fill({ email: "someone@customer.example" });
    create();
    const panel = await screen.findByText(EXISTING);
    const box = panel.closest("div")!.parentElement!;
    expect(within(box).getByRole("button", { name: "Link Existing User as Partner Administrator" })).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    // The password problem of a password that will not be used is not shown.
    expect(screen.queryByText(/too similar/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Password/, { selector: "input" })).not.toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Link Existing User as Partner Administrator" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/all?created=p-new"));
    const linked = createCalls(calls)[1];
    expect(linked.body).toEqual({
      experience: "reseller",
      partner: { display_name: "ABC Technologies" },
      administrator: { email: "someone@customer.example", mode: "link_existing" },
    });
  });

  it("Cancel clears the address and asks for another, with the password inputs back", async () => {
    mockApi({ "POST /api/v1/admin/partners/create": [taken] });
    mount();
    fill({ email: "someone@customer.example" });
    create();
    await screen.findByText(EXISTING);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(EXISTING)).not.toBeInTheDocument();
    expect(field(/^Admin email/).value).toBe("");
    expect(field(/^Password/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
