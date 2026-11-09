import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CustomerSetupPanel, HelpLibrary, PlatformSupport } from "./CustomerDesk";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";

const API = "/api/v1/orgs/acme";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("tickets to Workeasy360", () => {
  it("lists tickets, opens a new one with what was typed, and shows the answer; passes axe", async () => {
    const calls = mockApi({
      [`GET ${API}/support-tickets`]: [ok({ items: [{ id: "t1", reference: "#1", subject: "Old question", status: "open", updated_at: "2026-11-01T10:00:00Z" }] })],
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
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "New ticket" }));
    fireEvent.change(screen.getByLabelText("Subject"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("How can we help?"), { target: { value: "Please help" } });
    fireEvent.click(screen.getByRole("button", { name: "Send ticket" }));
    expect(await screen.findByRole("heading", { name: "#2: New" })).toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${API}/support-tickets`)?.body).toEqual({ subject: "New", message: "Please help" });
  });

  it("says nothing when the desk cannot be reached rather than showing an empty list", async () => {
    mockApi({ [`GET ${API}/support-tickets`]: [{ status: 500, body: { code: "boom" } }] });
    const { container } = render(<PlatformSupport slug="acme" />);
    await waitFor(() => expect(container.textContent).toBe(""));
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
