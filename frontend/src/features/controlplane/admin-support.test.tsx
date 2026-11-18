import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { HelpContentScreen, TicketScreen, TicketsScreen } from "./AdminSupport";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";

/**
 * Admin > Support (2026-11-09): the ticket desk and the help library. Real rows, what is
 * sent exactly, a refusal in the server's words, and what a read-only person is not offered.
 */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }, query: "" }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query),
  usePathname: () => "/admin/tickets",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.push.mockReset();
  nav.router.replace.mockReset();
  nav.query = "";
});

const VIEW = "platform.staff_support.view";
const MANAGE = "platform.staff_support.manage";
const T = "2026-11-08T10:00:00Z";

const asStaff = (permissions: string[], ui: React.ReactNode) => render(<AdminContextForTest permissions={permissions}>{ui}</AdminContextForTest>);

function choose(name: string | RegExp, option: string) {
  fireEvent.click(screen.getByRole("combobox", { name }));
  fireEvent.click(screen.getByRole("option", { name: option }));
}

const urlsOf = (calls: Call[], key: string) => calls.filter((c) => c.key === key).map((c) => c.url);
const bodyOf = (calls: Call[], key: string) => calls.find((c) => c.key === key)?.body;

const CUSTOMER_TICKET = {
  id: "t1",
  number: 12,
  reference: "#12",
  direction: "direct",
  audience: "customer",
  subject: "Invoice is wrong",
  category: "billing",
  priority: "urgent",
  status: "in_progress",
  opened_at: T,
  updated_at: T,
  resolved_at: null,
  sla: { state: "at_risk" },
  partner_id: null,
  partner_name: "",
  organization_id: "o1",
  organization_name: "Acme Logistics",
  assigned_to: null,
  assigned_to_name: "",
  resolution: "",
};

const PARTNER_TICKET = {
  ...CUSTOMER_TICKET,
  id: "t2",
  number: 7,
  reference: "#7",
  direction: "platform",
  audience: "partner",
  subject: "Payout question",
  category: "payout",
  priority: "normal",
  status: "open",
  partner_id: "p1",
  partner_name: "Bright Partner",
  organization_id: null,
  organization_name: "",
  assigned_to: "u2",
  assigned_to_name: "Sam Rao",
};

const COUNTS = { open: 2, in_progress: 3, waiting: 1, resolved: 4, closed: 5 };
const QUEUE = { items: [CUSTOMER_TICKET, PARTNER_TICKET], counts: COUNTS, total: 60, limit: 25, offset: 0 };
const TICKETS = "GET /api/v1/admin/tickets";

describe("Tickets with Partner tickets in it (admin-ux phase 1)", () => {
  it("opens with Who asked from the address, keeps a change there, and shows partners' SLA", async () => {
    nav.query = "audience=partner";
    const calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    const { container } = asStaff([VIEW, "platform.staff_partner.view"], <TicketsScreen />);
    const table = await screen.findByRole("table", { name: /^Tickets/ });
    expect(urlsOf(calls, TICKETS)[0]).toContain("audience=partner");
    expect(screen.getByRole("combobox", { name: "Who asked" })).toHaveTextContent("Partners");
    // The old Partner tickets list's SLA column, from the same row.
    expect(within(table).getByRole("columnheader", { name: "SLA" })).toBeInTheDocument();
    expect(within(within(table).getByRole("row", { name: /Invoice is wrong/ })).getByText("Due soon")).toBeInTheDocument();
    await expectNoViolations(container);
    choose("Who asked", "Customers");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?audience=customer", { scroll: false });
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("audience=customer"));
    expect(screen.queryByRole("columnheader", { name: "SLA" })).not.toBeInTheDocument();
    choose("Who asked", "All");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets", { scroll: false });
  });

  it("ignores a Who asked it does not know", async () => {
    nav.query = "audience=everyone";
    const calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("table", { name: /^Tickets/ });
    expect(urlsOf(calls, TICKETS)[0]).not.toContain("audience=");
  });

  it("shows a staff member with only the partner grant the partner desk, Who asked fixed at Partners", async () => {
    nav.query = "audience=partner";
    const calls = mockApi({
      "GET /api/v1/admin/partner-tickets": [
        ok({
          items: [{ ...PARTNER_TICKET, partner_name: "Bright Partner", sla: { state: "on_track", resolution: "on_track" } }],
          counts: { open: 1 },
          total: 1,
          priorities: [],
          categories: [],
        }),
      ],
    });
    const { container } = asStaff(["platform.staff_partner.view"], <TicketsScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Tickets" })).toBeInTheDocument();
    expect(await screen.findByText("Bright Partner")).toBeInTheDocument();
    expect(calls.some((c) => c.key === TICKETS)).toBe(false);
    const who = screen.getByRole("combobox", { name: "Who asked" });
    expect(who).toHaveTextContent("Partners");
    expect(who).toBeDisabled();
    await expectNoViolations(container);
  });
});

describe("the ticket list", () => {
  it("shows who asked by audience, the counts and the first page; passes axe", async () => {
    const calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    const { container } = asStaff([VIEW], <TicketsScreen />);
    const table = await screen.findByRole("table", { name: /^Tickets/ });
    const customer = within(table).getByRole("row", { name: /Invoice is wrong/ });
    expect(within(customer).getByRole("link", { name: "#12" })).toHaveAttribute("href", "/admin/tickets/t1");
    expect(within(customer).getByRole("link", { name: "Acme Logistics" })).toHaveAttribute("href", "/admin/customers/o1");
    expect(within(customer).getByText("Customer")).toBeInTheDocument();
    expect(within(customer).getByText("Billing")).toBeInTheDocument();
    expect(within(customer).getByText("Urgent")).toBeInTheDocument();
    expect(within(customer).getByText("In progress")).toBeInTheDocument();
    expect(within(customer).getByText("Unassigned")).toBeInTheDocument();
    const partner = within(table).getByRole("row", { name: /Payout question/ });
    expect(within(partner).getByRole("link", { name: "Bright Partner" })).toHaveAttribute("href", "/admin/partners/p1");
    expect(within(partner).getByText("Partner")).toBeInTheDocument();
    expect(within(partner).getByText("Sam Rao")).toBeInTheDocument();

    // "Open" covers all three statuses still waiting for someone, as the server does.
    const chips = screen.getByRole("group", { name: "Ticket status" });
    expect(within(chips).getByRole("button", { name: "Open 6" })).toHaveAttribute("aria-pressed", "true");
    expect(within(chips).getByRole("button", { name: "Resolved 4" })).toBeInTheDocument();
    expect(within(chips).getByRole("button", { name: "All 15" })).toBeInTheDocument();

    expect(screen.getByText("Showing 1–2 of 60")).toBeInTheDocument();
    expect(urlsOf(calls, TICKETS)[0]).toBe("/api/v1/admin/tickets?status=open&limit=25&offset=0");
    await expectNoViolations(container);
  });

  it("opens the ticket from a click anywhere on its row", async () => {
    mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    fireEvent.click(await screen.findByText("Invoice is wrong"));
    expect(nav.router.push).toHaveBeenCalledWith("/admin/tickets/t1");
  });

  it("sends each filter to the server and goes back to the first page", async () => {
    const calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("table", { name: /^Tickets/ });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("offset=25"));
    await screen.findByRole("table", { name: /^Tickets/ });
    choose("Who asked", "Partners");
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toBe("/api/v1/admin/tickets?audience=partner&status=open&limit=25&offset=0"));
    await screen.findByRole("table", { name: /^Tickets/ });
    choose("Assigned", "Me");
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("assigned=me"));
    await screen.findByRole("table", { name: /^Tickets/ });
    fireEvent.click(screen.getByRole("button", { name: /^Resolved/ }));
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("status=resolved"));
    await screen.findByRole("table", { name: /^Tickets/ });
    fireEvent.change(screen.getByLabelText("Search subject, partner or #number"), { target: { value: "invoice" } });
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("q=invoice"));
  });

  it("says when there are none, and shows the server's message when it fails", async () => {
    mockApi({ [TICKETS]: [ok({ items: [], counts: COUNTS, total: 0, limit: 25, offset: 0 })] });
    const view = asStaff([VIEW], <TicketsScreen />);
    expect(await screen.findByText("No tickets here")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Pages" })).toBeNull();
    view.unmount();

    mockApi({ [TICKETS]: [{ status: 500, body: { detail: "The desk could not be read." } }] });
    asStaff([VIEW], <TicketsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The desk could not be read.");
  });

  it("offers Try again when the list fails", async () => {
    const calls = mockApi({ [TICKETS]: [{ status: 500, body: { detail: "The desk could not be read." } }, ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: /^Tickets/ })).toHaveTextContent("Invoice is wrong");
    expect(urlsOf(calls, TICKETS)).toHaveLength(2);
  });

  it("keeps every filter in the address, sends the Opened days, and lists them as removable tags (admin-ux phase 3)", async () => {
    const calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("table", { name: /^Tickets/ });
    fireEvent.click(screen.getByRole("button", { name: /^Waiting/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=waiting", { scroll: false });
    choose("Assigned", "Unassigned");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=waiting&assigned=none", { scroll: false });
    await screen.findByRole("table", { name: /^Tickets/ });
    fireEvent.click(screen.getByRole("button", { name: /^Opened: Any time/ }));
    const panel = screen.getByRole("dialog", { name: "Opened between" });
    fireEvent.change(within(panel).getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(within(panel).getByLabelText("To"), { target: { value: "2026-11-08" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).toContain("from=2026-11-01"));
    const sent = new URL(urlsOf(calls, TICKETS).at(-1)!, "https://x.invalid").searchParams;
    expect(Object.fromEntries(sent)).toEqual({ status: "waiting", assigned: "none", from: "2026-11-01", to: "2026-11-08", limit: "25", offset: "0" });
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=waiting&assigned=none&from=2026-11-01&to=2026-11-08", { scroll: false });
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getAllByRole("listitem").map((t) => t.textContent)).toEqual(["Assigned: Unassigned", expect.stringMatching(/^Opened: .*1.*Nov.*2026.*8.*Nov.*2026$/)]);
    fireEvent.click(within(tags).getByRole("button", { name: /^Remove filter Opened/ }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=waiting&assigned=none", { scroll: false });
    await waitFor(() => expect(urlsOf(calls, TICKETS).at(-1)).not.toContain("from="));
    // Reset filters keeps the status chip: it is the list, not a filter on it.
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=waiting", { scroll: false });
  });

  it("reads every filter back from the address, drops what it does not know, and starts on the first page", async () => {
    nav.query = "status=resolved&audience=customer&assigned=me&q=invoice&from=2026-11-01&to=2026-11-05";
    let calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    const view = asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("table", { name: /^Tickets/ });
    const first = new URL(urlsOf(calls, TICKETS)[0], "https://x.invalid").searchParams;
    expect(Object.fromEntries(first)).toEqual({
      audience: "customer",
      status: "resolved",
      assigned: "me",
      q: "invoice",
      from: "2026-11-01",
      to: "2026-11-05",
      limit: "25",
      offset: "0",
    });
    expect(screen.getByRole("button", { name: /^Resolved/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Opened: .*Nov.*2026/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Search subject, partner or #number")).toHaveValue("invoice");
    view.unmount();

    nav.query = "status=lost&assigned=bob&from=2026-11-09&to=2026-11-01";
    calls = mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    await screen.findByRole("table", { name: /^Tickets/ });
    expect(urlsOf(calls, TICKETS)[0]).toBe("/api/v1/admin/tickets?status=open&limit=25&offset=0");
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("sorts on its columns' values over the rows shown", async () => {
    mockApi({ [TICKETS]: [ok(QUEUE)] });
    asStaff([VIEW], <TicketsScreen />);
    const table = await screen.findByRole("table", { name: /^Tickets/ });
    const subjects = () => within(table).getAllByRole("row").slice(1).map((r) => (r.textContent?.includes("Invoice") ? "Invoice" : "Payout"));
    expect(subjects()).toEqual(["Invoice", "Payout"]);
    // By urgency, not by name: normal before urgent going up.
    fireEvent.click(within(table).getByRole("button", { name: "Priority" }));
    expect(subjects()).toEqual(["Payout", "Invoice"]);
    fireEvent.click(within(table).getByRole("button", { name: "Reference" }));
    expect(subjects()).toEqual(["Payout", "Invoice"]);
    for (const name of ["Subject", "Who asked", "Category", "Status", "Assignee", "Updated"]) {
      expect(within(table).getByRole("button", { name })).toBeInTheDocument();
    }
  });
});

const MESSAGES = [
  { id: "m1", side: "customer", author: "Acme Logistics", body: "Line one\nLine two <b>not bold</b>", internal: false, created_at: T },
  { id: "m2", side: "platform", author: "Workeasy360 Support", body: "Check the tax first.", internal: true, created_at: T },
];
const DETAIL = { ...CUSTOMER_TICKET, messages: MESSAGES, may_answer: true };
const ONE = "/api/v1/admin/tickets/t1";
const PEOPLE = { "GET /api/v1/admin/ticket-people": [ok({ results: [{ id: "u2", name: "Sam Rao" }] })] };

describe("one ticket", () => {
  it("shows the conversation as plain text with internal notes marked; passes axe", async () => {
    mockApi({ [`GET ${ONE}`]: [ok(DETAIL)], ...PEOPLE });
    const { container } = asStaff([VIEW, MANAGE], <TicketScreen ticketId="t1" />);
    expect(await screen.findByRole("heading", { level: 1, name: "#12 Invoice is wrong" })).toBeInTheDocument();
    const conversation = screen.getByRole("list", { name: "Conversation" });
    const [first, second] = within(conversation).getAllByRole("listitem");
    // Markup stays words on the page, and the line break is still in the text.
    const body = within(first).getByText(/Line one/);
    expect(body.textContent).toBe("Line one\nLine two <b>not bold</b>");
    expect(body.querySelector("b")).toBeNull();
    expect(within(first).queryByText("Internal note")).toBeNull();
    expect(within(second).getByText("Internal note")).toBeInTheDocument();
    expect(screen.getByText("At risk")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Acme Logistics" })).toHaveAttribute("href", "/admin/customers/o1");
    await expectNoViolations(container);
  });

  it("sends a reply with the internal option and the status, then refreshes", async () => {
    const calls = mockApi({ [`GET ${ONE}`]: [ok(DETAIL)], [`POST ${ONE}`]: [ok(DETAIL)], ...PEOPLE });
    asStaff([VIEW, MANAGE], <TicketScreen ticketId="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Reply" }));
    const popup = screen.getByRole("dialog", { name: "Reply to #12" });
    expect(within(popup).getByRole("button", { name: "Save status" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Reply"), { target: { value: "  Looking into it.  " } });
    fireEvent.click(within(popup).getByLabelText(/Internal note/));
    fireEvent.click(within(popup).getByRole("combobox", { name: /Set status/ }));
    fireEvent.click(screen.getByRole("option", { name: "Waiting on the asker" }));
    fireEvent.click(within(popup).getByRole("button", { name: "Save note" }));
    expect(await screen.findByText("Internal note saved.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${ONE}`)).toEqual({ message: "Looking into it.", internal: true, status: "waiting" });
    await waitFor(() => expect(urlsOf(calls, `GET ${ONE}`).length).toBe(2));
  });

  it("keeps a refused reply in the popup, in the server's words", async () => {
    mockApi({
      [`GET ${ONE}`]: [ok(DETAIL)],
      [`POST ${ONE}`]: [{ status: 409, body: { detail: "This ticket is closed. Open a new one to continue." } }],
      ...PEOPLE,
    });
    asStaff([VIEW, MANAGE], <TicketScreen ticketId="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Reply" }));
    const popup = screen.getByRole("dialog", { name: "Reply to #12" });
    fireEvent.change(within(popup).getByLabelText("Reply"), { target: { value: "Hello" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Send reply" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("This ticket is closed. Open a new one to continue.");
    expect(within(popup).getByLabelText("Reply")).toHaveValue("Hello");
  });

  it("assigns to a person on the staff list, and resolves with the words given", async () => {
    const calls = mockApi({
      [`GET ${ONE}`]: [ok(DETAIL)],
      [`POST ${ONE}/assign`]: [ok(DETAIL)],
      [`POST ${ONE}/resolve`]: [ok(DETAIL)],
      ...PEOPLE,
    });
    asStaff([VIEW, MANAGE], <TicketScreen ticketId="t1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Assign" }));
    const assign = screen.getByRole("dialog", { name: "Assign #12" });
    expect(within(assign).getByRole("button", { name: "Assign" })).toBeDisabled();
    await waitFor(() => expect(calls.some((c) => c.key === "GET /api/v1/admin/ticket-people")).toBe(true));
    fireEvent.click(within(assign).getByRole("combobox", { name: /Assign to/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Sam Rao" }));
    fireEvent.click(within(assign).getByRole("button", { name: "Assign" }));
    expect(await screen.findByText("Assigned to Sam Rao.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${ONE}/assign`)).toEqual({ user_id: "u2" });

    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    const resolve = screen.getByRole("dialog", { name: "Resolve #12" });
    const box = within(resolve).getByLabelText("How it was resolved");
    fireEvent.change(box, { target: { value: "Fix" } });
    expect(within(resolve).getByRole("button", { name: "Resolve" })).toBeDisabled();
    fireEvent.change(box, { target: { value: "Corrected the invoice." } });
    fireEvent.click(within(resolve).getByRole("button", { name: "Resolve" }));
    expect(await screen.findByText("Ticket resolved.")).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${ONE}/resolve`)).toEqual({ resolution: "Corrected the invoice." });
  });

  it("offers no write action without may_answer, and asks for no staff list", async () => {
    const calls = mockApi({ [`GET ${ONE}`]: [ok({ ...DETAIL, may_answer: false })] });
    asStaff([VIEW], <TicketScreen ticketId="t1" />);
    await screen.findByRole("heading", { level: 1 });
    for (const name of ["Reply", "Assign", "Resolve"]) expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.getByText(/You can read this ticket/)).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual([`GET ${ONE}`]);
  });

  it("offers no reply on a closed ticket, and shows its resolution", async () => {
    mockApi({ [`GET ${ONE}`]: [ok({ ...DETAIL, status: "closed", resolution: "Done\nand dusted", resolved_at: T })] });
    asStaff([VIEW, MANAGE], <TicketScreen ticketId="t1" />);
    expect(await screen.findByText(/takes no more replies/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
    expect(screen.getByText(/Done/).textContent).toBe("Done\nand dusted");
  });

  it("says so when the ticket cannot be read", async () => {
    mockApi({ [`GET ${ONE}`]: [{ status: 500, body: { detail: "The ticket could not be read." } }] });
    asStaff([VIEW], <TicketScreen ticketId="t1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The ticket could not be read.");
  });
});

const ARTICLE = {
  id: "a1",
  slug: "reset",
  kind: "faq",
  audience: "customer",
  status: "draft",
  title: "How do I reset?",
  body: "First\nSecond <i>step</i>",
  product_code: "",
  display_order: 3,
  published_at: null,
  updated_at: T,
};
const PUBLISHED = { ...ARTICLE, id: "a2", title: "Setting up", kind: "setup_guide", audience: "all", status: "published", product_code: "easy_crm", display_order: 1 };
const HELP = "/api/v1/admin/help-content";

describe("help content", () => {
  it("lists articles, says what reaches readers, filters through the server; passes axe", async () => {
    const calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [ARTICLE, PUBLISHED] })] });
    const { container } = asStaff([VIEW, MANAGE], <HelpContentScreen />);
    const table = await screen.findByRole("table", { name: "Help articles" });
    expect(within(table).getByText("How do I reset?")).toBeInTheDocument();
    expect(within(table).getByText("Setup guide")).toBeInTheDocument();
    expect(within(table).getByText("Customers")).toBeInTheDocument();
    expect(within(table).getByText("easy_crm")).toBeInTheDocument();
    expect(screen.getByText(/Only published articles reach readers/)).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Published" }));
    await waitFor(() => expect(urlsOf(calls, `GET ${HELP}`).at(-1)).toContain("status=published"));
    await screen.findByRole("table", { name: "Help articles" });
    choose("Kind", "Setup guide");
    await waitFor(() => expect(urlsOf(calls, `GET ${HELP}`).at(-1)).toContain("kind=setup_guide"));
  });

  it("creates a draft with the exact fields, and validates the order first", async () => {
    const calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [] })], [`POST ${HELP}`]: [{ status: 201, body: ARTICLE }] });
    asStaff([VIEW, MANAGE], <HelpContentScreen />);
    await screen.findByText("No articles here");
    fireEvent.click(screen.getByRole("button", { name: "New article" }));
    const popup = screen.getByRole("dialog", { name: "New article" });
    const create = within(popup).getByRole("button", { name: "Create draft" });
    expect(create).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Title"), { target: { value: "  Getting started " } });
    fireEvent.change(within(popup).getByLabelText("Body"), { target: { value: "Step one\nStep two" } });
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Kind/ }));
    fireEvent.click(screen.getByRole("option", { name: "Setup guide" }));
    fireEvent.click(within(popup).getByRole("combobox", { name: /^Audience/ }));
    fireEvent.click(screen.getByRole("option", { name: "Partners" }));
    fireEvent.change(within(popup).getByLabelText("Product code (optional)"), { target: { value: "easy_crm" } });
    fireEvent.change(within(popup).getByLabelText("Order"), { target: { value: "abc" } });
    expect(create).toBeDisabled();
    expect(within(popup).getByText(/whole number from 0 to 9999/)).toBeInTheDocument();
    fireEvent.change(within(popup).getByLabelText("Order"), { target: { value: "5" } });
    fireEvent.click(create);
    expect(await screen.findByText(/Draft created/)).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${HELP}`)).toEqual({
      title: "Getting started",
      body: "Step one\nStep two",
      kind: "setup_guide",
      audience: "partner",
      product_code: "easy_crm",
      display_order: 5,
    });
    expect(urlsOf(calls, `GET ${HELP}`).length).toBe(2);
  });

  it("edits, keeping what was there, and shows a refusal in the popup", async () => {
    const calls = mockApi({
      [`GET ${HELP}`]: [ok({ results: [ARTICLE] })],
      [`PUT ${HELP}/a1`]: [{ status: 400, body: { detail: "Give it a title of at most 200 characters." } }, ok(ARTICLE)],
    });
    asStaff([VIEW, MANAGE], <HelpContentScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for How do I reset?" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    const popup = screen.getByRole("dialog", { name: "Edit article" });
    expect(within(popup).getByLabelText("Body")).toHaveValue("First\nSecond <i>step</i>");
    expect(within(popup).getByLabelText("Order")).toHaveValue("3");
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Give it a title of at most 200 characters.");
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Article saved.")).toBeInTheDocument();
    expect(bodyOf(calls, `PUT ${HELP}/a1`)).toEqual({
      title: "How do I reset?",
      body: "First\nSecond <i>step</i>",
      kind: "faq",
      audience: "customer",
      product_code: "",
      display_order: 3,
    });
  });

  it("publishes, takes back to draft, and archives only after confirming", async () => {
    const calls = mockApi({
      [`GET ${HELP}`]: [ok({ results: [ARTICLE, PUBLISHED] })],
      [`POST ${HELP}/a1/status`]: [ok({ ...ARTICLE, status: "published" })],
      [`POST ${HELP}/a2/status`]: [ok({ ...PUBLISHED, status: "draft" })],
    });
    asStaff([VIEW, MANAGE], <HelpContentScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for How do I reset?" }));
    expect(screen.queryByRole("menuitem", { name: "Unpublish to draft" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Publish" }));
    expect(await screen.findByText(/Readers can see it now/)).toBeInTheDocument();
    expect(bodyOf(calls, `POST ${HELP}/a1/status`)).toEqual({ status: "published" });

    // Unpublishing takes it from readers: asked first (admin-ux phase 3), nothing sent until confirmed.
    fireEvent.click(screen.getByRole("button", { name: "Actions for Setting up" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpublish to draft" }));
    const unpublish = screen.getByRole("dialog", { name: 'Unpublish "Setting up"?' });
    expect(within(unpublish).getByText(/Readers stop seeing it at once/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === `POST ${HELP}/a2/status`)).toBe(false);
    fireEvent.click(within(unpublish).getByRole("button", { name: "Unpublish" }));
    await waitFor(() => expect(bodyOf(calls, `POST ${HELP}/a2/status`)).toEqual({ status: "draft" }));
    await screen.findByText(/Readers no longer see it/);

    calls.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Actions for How do I reset?" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    const confirm = screen.getByRole("dialog", { name: 'Archive "How do I reset?"?' });
    expect(calls.some((c) => c.key === `POST ${HELP}/a1/status`)).toBe(false);
    fireEvent.click(within(confirm).getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(bodyOf(calls, `POST ${HELP}/a1/status`)).toEqual({ status: "archived" }));
    expect(await screen.findByText("Archived.")).toBeInTheDocument();
  });

  it("shows a refused publish in the server's words", async () => {
    mockApi({
      [`GET ${HELP}`]: [ok({ results: [ARTICLE] })],
      [`POST ${HELP}/a1/status`]: [{ status: 403, body: { detail: "You may not publish help content." } }],
    });
    asStaff([VIEW, MANAGE], <HelpContentScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for How do I reset?" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You may not publish help content.");
  });

  it("lets a read-only person read an article as text, and change nothing", async () => {
    mockApi({ [`GET ${HELP}`]: [ok({ results: [ARTICLE] })] });
    asStaff([VIEW], <HelpContentScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for How do I reset?" }));
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Read"]);
    expect(screen.queryByRole("button", { name: "New article" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Read" }));
    const reader = screen.getByRole("dialog", { name: "How do I reset?" });
    const body = within(reader).getByText(/First/);
    expect(body.textContent).toBe("First\nSecond <i>step</i>");
    expect(body.querySelector("i")).toBeNull();
  });

  it("shows the server's message when the list fails", async () => {
    mockApi({ [`GET ${HELP}`]: [{ status: 500, body: { detail: "The library could not be read." } }] });
    asStaff([VIEW], <HelpContentScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The library could not be read.");
  });

  it("offers Try again when the list fails", async () => {
    const calls = mockApi({ [`GET ${HELP}`]: [{ status: 500, body: { detail: "The library could not be read." } }, ok({ results: [ARTICLE] })] });
    asStaff([VIEW], <HelpContentScreen />);
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("How do I reset?")).toBeInTheDocument();
    expect(urlsOf(calls, `GET ${HELP}`)).toHaveLength(2);
  });

  it("keeps its status, kind and search in the address, with the kind and search as removable tags (admin-ux phase 3)", async () => {
    const calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [ARTICLE, PUBLISHED] })] });
    asStaff([VIEW], <HelpContentScreen />);
    await screen.findByRole("table", { name: "Help articles" });
    fireEvent.click(screen.getByRole("button", { name: "Published" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=published", { scroll: false });
    choose("Kind", "Store policy");
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=published&kind=policy", { scroll: false });
    fireEvent.change(screen.getByLabelText("Search title or text"), { target: { value: "refund" } });
    await waitFor(() => expect(urlsOf(calls, `GET ${HELP}`).at(-1)).toContain("q=refund"));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=published&kind=policy&q=refund", { scroll: false });
    const tags = screen.getByRole("list", { name: "Active filters" });
    expect(within(tags).getAllByRole("listitem").map((t) => t.textContent)).toEqual(["Search: “refund”", "Kind: Store policy"]);
    fireEvent.click(within(tags).getByRole("button", { name: "Remove filter Kind: Store policy" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=published&q=refund", { scroll: false });
    // Reset filters keeps the status chip: it is the list, not a filter on it.
    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(nav.router.replace).toHaveBeenLastCalledWith("/admin/tickets?status=published", { scroll: false });
  });

  it("reads its filters back from the address and drops a kind it does not know", async () => {
    nav.query = "status=archived&kind=faq&q=reset";
    let calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [ARTICLE] })] });
    const view = asStaff([VIEW], <HelpContentScreen />);
    await screen.findByRole("table", { name: "Help articles" });
    const first = new URL(urlsOf(calls, `GET ${HELP}`)[0], "https://x.invalid");
    expect(Object.fromEntries(first.searchParams)).toEqual({ kind: "faq", status: "archived", q: "reset" });
    expect(screen.getByRole("button", { name: "Archived" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("combobox", { name: "Kind" })).toHaveTextContent("FAQ");
    view.unmount();

    nav.query = "kind=poem";
    calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [ARTICLE] })] });
    asStaff([VIEW], <HelpContentScreen />);
    await screen.findByRole("table", { name: "Help articles" });
    expect(urlsOf(calls, `GET ${HELP}`)[0]).not.toContain("kind=");
    expect(screen.getByText(/were not recognised and were left out/)).toBeInTheDocument();
  });

  it("does not unpublish when the confirmation is cancelled", async () => {
    const calls = mockApi({ [`GET ${HELP}`]: [ok({ results: [PUBLISHED] })] });
    asStaff([VIEW, MANAGE], <HelpContentScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Setting up" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpublish to draft" }));
    const ask = screen.getByRole("dialog", { name: 'Unpublish "Setting up"?' });
    fireEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.some((c) => c.key.startsWith("POST "))).toBe(false);
  });
});
