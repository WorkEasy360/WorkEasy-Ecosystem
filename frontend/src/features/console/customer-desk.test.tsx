import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CustomerSetupPanel, HelpLibrary, PlatformSupport } from "./CustomerDesk";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/o/acme/help",
  useParams: () => ({ orgSlug: "acme" }),
}));

const API = "/api/v1/orgs/acme";
const NO_PROVIDER = {
  provider: null,
  items: [],
  counts: {},
  total: 0,
  priorities: ["low", "normal", "high", "urgent"],
  categories: ["general", "technical", "billing", "commission", "payout", "account", "product"],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("tickets to Workeasy360 (an organization no partner looks after)", () => {
  it("lists tickets with status chips, opens a new one with what was typed, and shows the answer; passes axe", async () => {
    const calls = mockApi({
      [`GET ${API}/provider-tickets`]: [ok(NO_PROVIDER)],
      [`GET ${API}/support-tickets`]: [
        ok({
          items: [
            { id: "t1", reference: "#1", subject: "Old question", category: "general", status: "open", updated_at: "2026-11-01T10:00:00Z" },
            { id: "t0", reference: "#0", subject: "Closed one", category: "billing", status: "closed", updated_at: "2026-10-01T10:00:00Z" },
          ],
          counts: { open: 1, closed: 1 },
        }),
      ],
      [`POST ${API}/support-tickets`]: [ok({ id: "t2", reference: "#2", subject: "New", status: "open", updated_at: "2026-11-09T10:00:00Z" })],
      [`GET ${API}/support-tickets/t2`]: [
        ok({
          id: "t2",
          reference: "#2",
          subject: "New",
          status: "open",
          updated_at: "2026-11-09T10:00:00Z",
          messages: [{ id: "m1", side: "customer", author: "You", body: "Please help", created_at: "2026-11-09T10:00:00Z" }],
        }),
      ],
    });
    const { container } = render(<PlatformSupport slug="acme" />);
    expect(await screen.findByText("Old question")).toBeInTheDocument();
    expect(screen.getByText("Tickets to WorkEasy360, newest first.")).toBeInTheDocument();
    await expectNoViolations(container);
    // Status is a filter, never a menu.
    fireEvent.click(within(screen.getByRole("group", { name: "Ticket status" })).getByRole("button", { name: /^Closed/ }));
    expect(screen.queryByText("Old question")).not.toBeInTheDocument();
    expect(screen.getByText("Closed one")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Create support ticket" }));
    const dialog = screen.getByRole("dialog", { name: "Create support ticket" });
    // A partner's own categories are not a customer's.
    expect(within(within(dialog).getByLabelText("Category")).queryByRole("option", { name: "Commission" })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Subject"), { target: { value: "New" } });
    fireEvent.change(within(dialog).getByLabelText("How can we help?"), { target: { value: "Please help" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send ticket" }));
    expect(await screen.findByRole("heading", { name: "#2: New" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Conversation" })).toHaveTextContent("Please help");
    expect(calls.find((c) => c.key === `POST ${API}/support-tickets`)?.body).toEqual({
      subject: "New",
      message: "Please help",
      category: "general",
      priority: "normal",
    });
  });

  it("says the desk could not be reached rather than showing an empty list", async () => {
    mockApi({
      [`GET ${API}/provider-tickets`]: [ok(NO_PROVIDER)],
      [`GET ${API}/support-tickets`]: [{ status: 500, body: { code: "boom" } }],
    });
    render(<PlatformSupport slug="acme" />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("No tickets yet.")).not.toBeInTheDocument();
  });

  it("replies in the conversation", async () => {
    const thread = {
      id: "t1",
      reference: "#1",
      subject: "Old question",
      status: "waiting",
      updated_at: "2026-11-01T10:00:00Z",
      messages: [{ id: "m1", side: "platform", author: "Support", body: "Which product?", created_at: "2026-11-01T11:00:00Z" }],
    };
    const calls = mockApi({
      [`GET ${API}/provider-tickets`]: [ok(NO_PROVIDER)],
      [`GET ${API}/support-tickets`]: [ok({ items: [thread], counts: { waiting: 1 } })],
      [`GET ${API}/support-tickets/t1`]: [ok(thread)],
      [`POST ${API}/support-tickets/t1`]: [ok(thread)],
    });
    render(<PlatformSupport slug="acme" />);
    fireEvent.click(await screen.findByRole("button", { name: "Open ticket #1: Old question" }));
    expect(await screen.findByText("Which product?")).toBeInTheDocument();
    // The platform desk has no customer close; only a partner's does.
    expect(screen.queryByRole("button", { name: "Close ticket" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Your reply"), { target: { value: "Easy CRM" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reply" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${API}/support-tickets/t1`)?.body).toEqual({ message: "Easy CRM" }));
  });
});

describe("product setup", () => {
  const record = {
    id: "s1",
    product_code: "crm",
    status: "correction_required",
    answers: { gst: "OLD" },
    decision_reason: "That number has a typing mistake.",
    requirements: [{ key: "gst", label: "GST number", help_text: "", is_required: true }],
    editable: true,
  };

  it("shows what needs correcting and sends the corrected answer", async () => {
    const calls = mockApi({
      [`GET ${API}/setup`]: [ok({ results: [record] })],
      [`POST ${API}/setup/s1/submit`]: [ok({ results: [] })],
    });
    render(<CustomerSetupPanel slug="acme" />);
    expect(await screen.findByText("That number has a typing mistake.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Correct and send" }));
    fireEvent.change(screen.getByLabelText("GST number (required)"), { target: { value: "NEW" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.find((c) => c.key === `POST ${API}/setup/s1/submit`)?.body).toEqual({ answers: { gst: "NEW" } }));
  });

  it("is absent when no product needs anything", async () => {
    mockApi({ [`GET ${API}/setup`]: [ok({ results: [] })] });
    const { container } = render(<CustomerSetupPanel slug="acme" />);
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("the help library", () => {
  it("asks for customer content and shows article text as text, never markup", async () => {
    const calls = mockApi({
      "GET /api/v1/public/help": [ok({ results: [{ slug: "a", kind: "faq", title: "How do I export?", body: "<b>Open</b> Reports." }] })],
    });
    const { container } = render(<HelpLibrary />);
    expect(await screen.findByText("How do I export?")).toBeInTheDocument();
    expect(calls[0].url).toContain("audience=customer");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>Open</b> Reports.")).toBeInTheDocument();
  });
});
