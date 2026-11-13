import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { HelpContentScreen, TicketScreen, TicketsScreen } from "./AdminSupport";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call } from "./testSupport";

/**
 * Admin > Support (2026-11-09): the ticket desk and the help library. Real rows, what is
 * sent exactly, a refusal in the server's words, and what a read-only person is not offered.
 */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/tickets",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.push.mockReset();
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

    fireEvent.click(screen.getByRole("button", { name: "Actions for Setting up" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Unpublish to draft" }));
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
});
