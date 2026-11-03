import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    fireEvent.click(screen.getByRole("button", { name: "Raise a ticket" }));
    // A customer never files a commission or payout ticket.
    expect(screen.queryByRole("option", { name: "commission" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "Cannot log in" } });
    fireEvent.change(screen.getByLabelText("What do you need?"), { target: { value: "Since today." } });
    fireEvent.click(screen.getByRole("button", { name: "Send ticket" }));
    await waitFor(() => expect(calls.map((c) => c.key)).toContain(`GET ${API}/t1`));
    expect(await screen.findByText("Since today.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${API}`)!.body).toEqual({
      subject: "Cannot log in",
      message: "Since today.",
      category: "general",
      priority: "normal",
    });
  });
});
