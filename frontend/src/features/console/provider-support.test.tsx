import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { SupportDeskPanel } from "./CustomerDesk";

/**
 * The organization console's tickets to its partner (ADR-057): when the server names a
 * support provider, the one desk (2026-11-11) sends every ticket there; a member raises
 * a ticket and follows it. Without one, tickets go to WorkEasy360 (customer-desk.test).
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/o/acme/help",
  useParams: () => ({ orgSlug: "acme" }),
}));

const API = "/api/v1/orgs/acme/provider-tickets";
const EMPTY = { items: [], counts: {}, total: 0, priorities: ["low", "normal", "high", "urgent"], categories: ["general", "technical", "commission"] };
const SLA = { first_response_due_at: "2026-11-03T10:00:00Z", resolve_due_at: "2026-11-04T10:00:00Z", first_response: "on_track", resolution: "on_track", state: "on_track" };
const TICKET = {
  id: "t1",
  number: 1,
  reference: "#1",
  direction: "customer",
  subject: "Cannot log in",
  category: "technical",
  priority: "normal",
  status: "open",
  related_ref: "",
  opened_at: "2026-11-03T08:00:00Z",
  updated_at: "2026-11-03T08:00:00Z",
  first_response_at: null,
  resolved_at: null,
  closed_at: null,
  sla: SLA,
  messages: [{ id: "m1", side: "customer", author: "Acme", mine: true, body: "Since today.", internal: false, created_at: "2026-11-03T08:00:00Z" }],
};

describe("tickets to the organization's provider", () => {
  it("asks Workeasy360 instead when the organization has no provider", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [ok({ ...EMPTY, provider: null })],
      ["GET /api/v1/orgs/acme/support-tickets"]: [ok({ items: [], counts: {} })],
    });
    render(<SupportDeskPanel slug="acme" />);
    expect(await screen.findByText("Tickets to WorkEasy360, newest first.")).toBeInTheDocument();
    expect(calls.map((c) => c.key)).toEqual([`GET ${API}`, "GET /api/v1/orgs/acme/support-tickets"]);
  });

  it("raises a ticket to the partner and opens it", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [ok({ ...EMPTY, provider: { name: "Acme Reseller" } })],
      [`POST ${API}`]: [ok(TICKET)],
      [`GET ${API}/t1`]: [ok(TICKET)],
    });
    const { container } = render(<SupportDeskPanel slug="acme" />);
    expect(await screen.findByText("Tickets to Acme Reseller, newest first.")).toBeInTheDocument();
    await expectNoViolations(container);
    // The ticket is written in a popup.
    fireEvent.click(screen.getByRole("button", { name: "Create support ticket" }));
    const popup = screen.getByRole("dialog", { name: "Create support ticket" });
    expect(within(popup).getByText(/Acme Reseller answers here/)).toBeInTheDocument();
    await expectNoViolations(popup);
    // A customer never files a commission or payout ticket.
    expect(screen.queryByRole("option", { name: "Commission" })).toBeNull();
    const send = within(popup).getByRole("button", { name: "Send ticket" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Subject"), { target: { value: "Cannot log in" } });
    fireEvent.change(within(popup).getByLabelText("How can we help?"), { target: { value: "Since today." } });
    fireEvent.click(send);
    await waitFor(() => expect(calls.map((c) => c.key)).toContain(`GET ${API}/t1`));
    expect(await screen.findByText("Since today.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${API}`)!.body).toEqual({
      subject: "Cannot log in",
      message: "Since today.",
      category: "general",
      priority: "normal",
    });
  });

  it("replies to a ticket in its conversation, and the partner's desk can be closed", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [ok({ ...EMPTY, provider: { name: "Acme Reseller" }, items: [TICKET], total: 1, counts: { open: 1 } })],
      [`GET ${API}/t1`]: [ok(TICKET)],
      [`POST ${API}/t1`]: [ok(TICKET)],
    });
    render(<SupportDeskPanel slug="acme" />);
    fireEvent.click(await screen.findByRole("button", { name: "Open ticket #1: Cannot log in" }));
    const send = await screen.findByRole("button", { name: "Send reply" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Your reply"), { target: { value: "Still broken." } });
    fireEvent.click(send);
    await waitFor(() => expect(calls.filter((c) => c.key === `POST ${API}/t1`).map((c) => c.body)).toEqual([{ message: "Still broken." }]));
    expect(screen.getByRole("button", { name: "Close ticket" })).toBeInTheDocument();
    // "Since today." is the customer's own message: drawn as theirs.
    expect(screen.getByRole("list", { name: "Conversation" })).toHaveTextContent("You");
  });
});
