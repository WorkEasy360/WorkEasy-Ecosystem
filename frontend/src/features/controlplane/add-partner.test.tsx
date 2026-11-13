import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AddPartner } from "./AddPartner";
import { AdminContextForTest, expectNoViolations, mockApi, type Call, type Reply } from "./testSupport";

/**
 * Partners → New partner (ADR-048): the short form - partner name, admin email,
 * an invitation by default (2026-11-07) or a password and confirmation, partner
 * type, optional country. What these tests hold on to:
 *
 * - it asks for nothing more: no business details, products, prices or
 *   commission (those are Complete Your Profile's);
 * - the password is typed into `autocomplete="new-password"` inputs, goes in the
 *   one create request and nowhere else (no storage, no cookie, no other call),
 *   and is emptied once the request has been made;
 * - an address that already has an account is never overwritten - the form
 *   offers to link it (no password sent) or to cancel;
 * - the type is sent as `experience` and nothing names a capability;
 * - after creating, it opens the partner's page with the created flag;
 * - it is a modal dialog over All Partners (2026-10-26): focus starts inside,
 *   Escape, Close and × all return to the list.
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

/** Fill the form; a password (the default here) chooses "Set a password now". */
function fill({
  name = "ABC Technologies",
  email = "admin@abctech.example",
  password = PASSWORD as string | null,
  confirm = PASSWORD as string | null,
  type = "reseller",
} = {}) {
  fireEvent.change(field(/^Partner \/ Business name/), { target: { value: name } });
  fireEvent.change(field(/^Partner Email/), { target: { value: email } });
  if (password !== null) fireEvent.click(screen.getByRole("radio", { name: /Set a password now/ }));
  if (password !== null) fireEvent.change(field(/^Password/), { target: { value: password } });
  if (confirm !== null) fireEvent.change(field(/^Confirm password/), { target: { value: confirm } });
  if (type) fireEvent.change(field(/^Partner type/), { target: { value: type } });
}

const create = () => fireEvent.click(screen.getByRole("button", { name: /^Create partner/ }));
const createCalls = (calls: Call[]) => calls.filter((c) => c.key === "POST /api/v1/admin/partners/create");

describe("the short form", () => {
  // A large form plus an axe pass: slow in jsdom when the whole suite runs in parallel.
  it("asks for the business name, the Partner Email, the type, then optional country and mobile, and how they sign in", async () => {
    mockApi({});
    const { container } = mount();
    const dialog = screen.getByRole("dialog", { name: "New partner" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("heading", { level: 2, name: "New partner" })).toBeInTheDocument();
    expect(field(/^Partner \/ Business name/)).toHaveAttribute("placeholder", "Enter the partner's business name");
    expect(field(/^Partner \/ Business name/)).toBeRequired();
    expect(field(/^Partner Email/)).toBeRequired();
    // An invitation by default: no password to type or share.
    expect(screen.getByRole("radio", { name: /Send an invitation email/ })).toBeChecked();
    expect(screen.queryByLabelText(/^Password/, { selector: "input" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /Set a password now/ }));
    expect(field(/^Password/)).toHaveAttribute("type", "password");
    // The eye inside the input shows it, and hides it again.
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(field(/^Password/)).toHaveAttribute("type", "text");
    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(field(/^Password/)).toHaveAttribute("type", "password");
    expect(field(/^Password/)).toHaveAttribute("autocomplete", "new-password");
    expect(field(/^Confirm password/)).toHaveAttribute("autocomplete", "new-password");
    expect(field(/^Country/)).not.toBeRequired();
    const type = field(/^Partner type/) as unknown as HTMLSelectElement;
    expect(type).toBeRequired();
    expect([...type.options].map((o) => o.value)).toEqual(["", "reseller", "white_label", "affiliate"]);
    // The chosen type is described under the list.
    for (const [key, text] of [
      ["reseller", "Sell WorkEasy360 products and manage customers."],
      // ADR-063: White Label is an add-on - selling or referring is ticked beside it.
      ["white_label", "Their own branding and domain. White Label alone sells and refers nothing: tick Reseller or Affiliate below to allow that too."],
      ["affiliate", "Promote WorkEasy360 products and earn commissions."],
    ]) {
      fireEvent.change(type, { target: { value: key } });
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    // Nothing about the business, products, prices or commission is asked here.
    for (const absent of [/business email/i, /products?/i, /price/i, /commission rate/i, /legal/i, /partner code/i]) {
      expect(screen.queryByLabelText(absent)).not.toBeInTheDocument();
    }
    // The Partner Email is the partner's own login, never a staff address.
    expect(field(/^Partner Email/)).toHaveAttribute("placeholder", "Enter partner email address");
    expect(screen.getByText(/Not a Workeasy360 staff email/)).toBeInTheDocument();
    expect(field(/^Mobile number/)).not.toBeRequired();
    // A separate contact email only when asked for.
    expect(screen.queryByLabelText(/^Contact email/, { selector: "input" })).not.toBeInTheDocument();
    // Three text inputs (name, email, mobile) and two lists (type, country); the passwords are not textboxes.
    expect(screen.getAllByRole("textbox").length + screen.getAllByRole("combobox").length).toBeLessThanOrEqual(5);
    fireEvent.click(screen.getByRole("radio", { name: /Send an invitation email/ }));
    await expectNoViolations(container);
  }, 60_000);

  it("preselects the type named in the address without choosing a capability", () => {
    query = "type=white-label";
    mockApi({});
    mount();
    expect(field(/^Partner type/).value).toBe("white_label");
  });

  it("says what is missing and sends nothing", async () => {
    const calls = mockApi({});
    mount();
    create();
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Enter the partner's business name.");
    expect(summary).toHaveTextContent("Enter the partner's email address.");
    expect(summary).toHaveTextContent("Choose the partner type.");
    // An invitation needs no password.
    expect(summary).not.toHaveTextContent("Enter a password.");
    fireEvent.click(screen.getByRole("radio", { name: /Set a password now/ }));
    create();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Enter a password."));
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
    expect(screen.queryByLabelText(/^Partner \/ Business name/)).not.toBeInTheDocument();
  });
});

describe("the dialog", () => {
  it("starts on the first field and keeps the keyboard inside", () => {
    mockApi({});
    mount();
    expect(field(/^Partner \/ Business name/)).toHaveFocus();
    const dialog = screen.getByRole("dialog");
    const add = within(dialog).getByRole("button", { name: "Create partner & send invitation" });
    add.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it.each([
    ["Escape", () => fireEvent.keyDown(document, { key: "Escape" })],
    // The × in the title bar and the Cancel button at the bottom.
    ["×", () => fireEvent.click(screen.getByRole("button", { name: "Close" }))],
    ["Cancel", () => fireEvent.click(screen.getByRole("button", { name: "Cancel" }))],
  ])("%s returns to All partners and sends nothing", (_name, act) => {
    const calls = mockApi({});
    mount();
    fill();
    act();
    expect(push).toHaveBeenCalledWith("/admin/partners/all");
    expect(calls).toHaveLength(0);
  });
});

describe("creating", () => {
  it("sends only the few fields, then opens All Partners with the new row marked", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [{ status: 201, body: CREATED }] });
    mount();
    fill({ type: "reseller" });
    fireEvent.change(field(/^Country/), { target: { value: "IN" } });
    create();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/all?created=p-new"));
    const [call] = createCalls(calls);
    expect(call.body).toEqual({
      experience: "reseller",
      partner: { display_name: "ABC Technologies", country: "IN" },
      // A password typed by staff must be replaced at first sign-in, unless unticked.
      administrator: { email: "admin@abctech.example", mode: "create", password: PASSWORD, password_confirm: PASSWORD, require_password_change: true },
    });
    // No other request carried the password, and nothing was stored.
    expect(calls.filter((c) => JSON.stringify(c.body ?? {}).includes(PASSWORD))).toHaveLength(1);
    expect(sessionStorage.length + localStorage.length).toBe(0);
    expect(document.cookie).not.toContain(PASSWORD);
  });

  it("invites the administrator by default: no password is typed or sent", async () => {
    const calls = mockApi({
      "POST /api/v1/admin/partners/create": [
        {
          status: 201,
          body: {
            ...CREATED,
            administrator: { ...CREATED.administrator, invited: true },
            invitation: { state: "queued", delivery_mode: "smtp" },
          },
        },
      ],
    });
    mount();
    fill({ password: null, confirm: null });
    // The button says an invitation goes out.
    expect(screen.getByRole("button", { name: "Create partner & send invitation" })).toBeInTheDocument();
    create();
    // What happened to the invitation travels with the new row: queued, not "delivered".
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/partners/all?created=p-new&invite=queued&mail=smtp"));
    expect(createCalls(calls)[0].body).toEqual({
      experience: "reseller",
      partner: { display_name: "ABC Technologies" },
      administrator: { email: "admin@abctech.example", mode: "invite" },
    });
  });

  it("sends the optional mobile number and a separate contact email when given", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [{ status: 201, body: CREATED }] });
    mount();
    fill({ password: null, confirm: null });
    fireEvent.change(field(/^Mobile number/), { target: { value: "+91 98765 43210" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Use a different contact email" }));
    fireEvent.change(field(/^Contact email/), { target: { value: "accounts@abctech.example" } });
    create();
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect((createCalls(calls)[0].body as { partner: unknown }).partner).toEqual({
      display_name: "ABC Technologies",
      phone: "+91 98765 43210",
      contact_email: "accounts@abctech.example",
    });
  });

  it("refuses a malformed mobile number or a contact email equal to the Partner Email, and sends nothing", async () => {
    const calls = mockApi({});
    mount();
    fill({ password: null, confirm: null });
    fireEvent.change(field(/^Mobile number/), { target: { value: "call me" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Use a different contact email" }));
    fireEvent.change(field(/^Contact email/), { target: { value: "ADMIN@abctech.example" } });
    create();
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Enter a valid mobile number");
    expect(summary).toHaveTextContent("The contact email is the same as the Partner Email");
    expect(createCalls(calls)).toHaveLength(0);
  });

  it("offers to confirm a different partner when a live one has the same name, then sends that confirmation", async () => {
    const calls = mockApi({
      "POST /api/v1/admin/partners/create": [
        {
          status: 400,
          body: {
            code: "partner_validation_error",
            detail: "A partner named ABC Technologies (abc-technologies) already exists.",
            problems: [{ field: "partner.display_name", message: "A partner named ABC Technologies (abc-technologies) already exists. Open it from All Partners, give this partner a name that tells them apart, or confirm that it is a different partner." }],
          },
        },
        { status: 201, body: CREATED },
      ],
    });
    mount();
    fill({ password: null, confirm: null });
    create();
    const confirmButton = await screen.findByRole("button", { name: "It is a different partner: create it" });
    expect(createCalls(calls)[0].body).not.toHaveProperty("partner.allow_same_name");
    fireEvent.click(confirmButton);
    await waitFor(() => expect(createCalls(calls)).toHaveLength(2));
    expect((createCalls(calls)[1].body as { partner: { allow_same_name?: boolean } }).partner.allow_same_name).toBe(true);
  });

  it("leaves the country out when none is chosen", async () => {
    const calls = mockApi({ "POST /api/v1/admin/partners/create": [{ status: 201, body: CREATED }] });
    mount();
    fill({ type: "affiliate" });
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
    expect(within(box).getByRole("button", { name: "Use another email" })).toBeInTheDocument();
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

  it("Use another email clears the address and asks for another, with the password inputs back", async () => {
    mockApi({ "POST /api/v1/admin/partners/create": [taken] });
    mount();
    fill({ email: "someone@customer.example" });
    create();
    await screen.findByText(EXISTING);
    fireEvent.click(screen.getByRole("button", { name: "Use another email" }));
    expect(screen.queryByText(EXISTING)).not.toBeInTheDocument();
    expect(field(/^Partner Email/).value).toBe("");
    expect(field(/^Password/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
