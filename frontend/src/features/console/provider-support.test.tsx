import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { ProviderSupport } from "./ProviderSupport";

/**
 * The organization console's tickets to its partner (ADR-057): drawn only when
 * the server names a support provider; a member raises a ticket and follows it.
 */

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
  it("draws nothing for an organization without a provider", async () => {
    const calls = mockApi({ [`GET ${API}`]: [ok({ ...EMPTY, provider: null })] });
    const { container } = render(<ProviderSupport slug="acme" />);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container).toBeEmptyDOMElement();
  });

  it("raises a ticket to the partner and opens it", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [ok({ ...EMPTY, provider: { name: "Acme Reseller" } })],
      [`POST ${API}`]: [ok(TICKET)],
      [`GET ${API}/t1`]: [ok(TICKET)],
    });
    const { container } = render(<ProviderSupport slug="acme" />);
    expect(await screen.findByText(/Acme Reseller looks after your Workeasy360 subscription/)).toBeInTheDocument();
    await expectNoViolations(container);
    // The ticket is written in a popup.
    fireEvent.click(screen.getByRole("button", { name: "Raise a ticket" }));
    const popup = screen.getByRole("dialog", { name: "Raise a ticket" });
    await expectNoViolations(popup);
    // A customer never files a commission or payout ticket.
    expect(screen.queryByRole("option", { name: "commission" })).toBeNull();
    const send = within(popup).getByRole("button", { name: "Send ticket" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Subject"), { target: { value: "Cannot log in" } });
    fireEvent.change(within(popup).getByLabelText("What do you need?"), { target: { value: "Since today." } });
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

  it("replies to a ticket in a small popup and keeps the thread open", async () => {
    const calls = mockApi({
      [`GET ${API}`]: [ok({ ...EMPTY, provider: { name: "Acme Reseller" }, items: [TICKET], total: 1 })],
      [`GET ${API}/t1`]: [ok(TICKET)],
      [`POST ${API}/t1`]: [ok(TICKET)],
    });
    render(<ProviderSupport slug="acme" />);
    fireEvent.click(await screen.findByRole("button", { name: "#1" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reply" }));
    const popup = screen.getByRole("dialog", { name: "Reply to #1" });
    const send = within(popup).getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Your reply"), { target: { value: "Still broken." } });
    fireEvent.click(send);
    expect(await screen.findByText("Reply sent.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key === `POST ${API}/t1`).map((c) => c.body)).toEqual([{ message: "Still broken." }]);
    expect(screen.getByRole("button", { name: "Close ticket" })).toBeInTheDocument();
  });
});
