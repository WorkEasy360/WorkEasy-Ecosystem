import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AppCard } from "./AppCard";
import { mockApi } from "@/features/controlplane/testSupport";
import type { AppDirectoryEntry } from "@/lib/api/types";

function app(overrides: Partial<AppDirectoryEntry>): AppDirectoryEntry {
  return {
    product_code: "crm",
    display_name: "CRM",
    description: "Manage your customers.",
    licensing_mode: "per_seat",
    state: "active",
    can_launch: true,
    entitlement_status: null,
    slug: "crm",
    tagline: "",
    category: "",
    icon: "",
    sso: "connected",
    health: { status: "unknown", checked_at: null },
    trial: null,
    plan: null,
    seats: null,
    renews_at: null,
    cancel_at: null,
    ...overrides,
  };
}

describe("AppCard", () => {
  it("renders an Open action for an active app", () => {
    render(<AppCard orgSlug="acme" app={app({ state: "active", can_launch: true })} />);
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
  });

  it("never renders an Open action for a no_seat app (Task 8/16: state drives the UI, not a guess)", () => {
    render(<AppCard orgSlug="acme" app={app({ state: "no_seat", can_launch: false })} />);
    expect(screen.queryByRole("button", { name: "Open" })).not.toBeInTheDocument();
    expect(screen.getByText("No seat")).toBeInTheDocument();
    expect(screen.getByText("Ask an admin")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Assign a seat/ })).not.toBeInTheDocument();
  });

  it("points someone who assigns seats at Product access instead of telling them to ask an admin (Phase 12)", () => {
    render(
      <AppCard
        orgSlug="acme"
        app={app({ state: "no_seat", can_launch: false })}
        seatsHref="/o/acme/settings/products"
      />,
    );
    expect(screen.queryByRole("button", { name: "Open" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Assign a seat: / })).toHaveAttribute("href", "/o/acme/settings/products");
    expect(screen.queryByText("Ask an admin")).not.toBeInTheDocument();
    expect(screen.queryByText(/Ask an administrator/)).not.toBeInTheDocument();
  });

  it("renders a Coming soon badge with no action for an unavailable product", () => {
    render(
      <AppCard orgSlug="acme" app={app({ state: "product_unavailable", can_launch: false })} />,
    );
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("never puts a destination into the static markup - Open carries no href or action naming a URL (Task 6/55)", () => {
    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    expect(document.querySelector("form")).not.toBeInTheDocument();
    expect(document.querySelector("[href]")).toBeNull();
  });

  it("opens exactly the destination the server answers with, as a real navigation", async () => {
    const calls = mockApi({
      "POST /api/v1/orgs/acme/apps/crm/launch": [
        { status: 200, body: { redirect_url: "https://crm.example.com/launch?organization=org-1", state: "active" } },
      ],
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });

    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://crm.example.com/launch?organization=org-1"),
    );
    // Marked replayable, so the edge can retry it across a worker recycle
    // (2026-10 audit); a launch writes nothing.
    expect(calls[0].headers["idempotency-key"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("shows the denial in words, never the raw code, and does not navigate", async () => {
    mockApi({
      "POST /api/v1/orgs/acme/apps/crm/launch": [
        { status: 409, body: { code: "launch_denied", state: "no_seat", product_code: "crm" } },
      ],
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });

    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You don't have a seat for that product yet. Ask an administrator to assign you one.",
    );
    expect(screen.queryByText("no_seat")).not.toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("says a read-only (suspended) organization is why, instead of a generic failure (Phase 12)", async () => {
    mockApi({
      "POST /api/v1/orgs/acme/apps/crm/launch": [{ status: 402, body: { code: "organization_not_writable" } }],
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });

    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/organization is read-only/);
    expect(screen.queryByText("organization_not_writable")).not.toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps the generic sentence for a code it does not know", async () => {
    mockApi({
      "POST /api/v1/orgs/acme/apps/crm/launch": [{ status: 500, body: { code: "internal_error" } }],
    });
    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That app couldn't be opened right now.");
  });
});

describe("AppCard - Phase 11 facts and status", () => {
  it("shows the product's icon, category, plan, seats and renewal date, all from the server", () => {
    render(
      <AppCard
        orgSlug="acme"
        app={app({
          category: "Sales / CRM",
          icon: "customers",
          entitlement_status: "active",
          plan: { code: "crm_pro", name: "CRM Pro" },
          seats: { total: 10, used: 8 },
          renews_at: "2026-11-01T00:00:00Z",
        })}
      />,
    );
    expect(screen.getByText("Sales / CRM")).toBeInTheDocument();
    expect(screen.getByText("Active", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByText("CRM Pro")).toBeInTheDocument();
    expect(screen.getByText("8 of 10")).toBeInTheDocument();
    expect(screen.getByText(/1 Nov 2026/)).toBeInTheDocument();
    // An unknown icon key never falls through to a monogram silently mismatched.
    expect(document.querySelector("svg")).toBeInTheDocument();
  });

  it("falls back to a monogram when no icon key is set - never an image", () => {
    render(<AppCard orgSlug="acme" app={app({ display_name: "Easy CRM", icon: "" })} />);
    expect(screen.getByText("EC")).toBeInTheDocument();
    expect(document.querySelector("svg")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows trial days remaining, and no renewal date for a trial", () => {
    render(
      <AppCard
        orgSlug="acme"
        app={app({
          entitlement_status: "trial",
          trial: { id: "t1", ends_at: "2026-10-05T00:00:00Z", days_left: 6 },
        })}
      />,
    );
    expect(screen.getByText("Trial", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByText("6 days left")).toBeInTheDocument();
    expect(screen.queryByText("Renews")).not.toBeInTheDocument();
  });

  it("says 1 day left, not 1 days left", () => {
    render(<AppCard orgSlug="acme" app={app({ trial: { id: "t1", ends_at: "x", days_left: 1 } })} />);
    expect(screen.getByText("1 day left")).toBeInTheDocument();
  });

  it("a suspended subscription still opens - read-only, not blocked (the platform's own policy)", () => {
    render(<AppCard orgSlug="acme" app={app({ entitlement_status: "suspended", state: "active" })} />);
    expect(screen.getByText("Suspended", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByText(/Read-only until payment resumes/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeEnabled();
  });

  it("a cancelled subscription still shows Active, with an ending note - it has not lost access yet", () => {
    render(<AppCard orgSlug="acme" app={app({ entitlement_status: "cancelled", cancel_at: "2026-12-01T00:00:00Z" })} />);
    expect(screen.getByText("Active", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByText(/Ending/)).toBeInTheDocument();
    expect(screen.queryByText("Renews")).not.toBeInTheDocument();
  });

  it("shows no plan, seats or renewal to a member who may not see subscriptions - the server never sent them", () => {
    // The server omits these fields for such a caller (launcher_views._card_extras);
    // the frontend renders exactly what it was given, never a guess.
    render(<AppCard orgSlug="acme" app={app({ entitlement_status: "active" })} />);
    expect(screen.queryByText("Plan")).not.toBeInTheDocument();
    expect(screen.queryByText("Seats")).not.toBeInTheDocument();
    expect(screen.queryByText("Renews")).not.toBeInTheDocument();
  });

  it("never renders an unknown icon key as a URL or markup", () => {
    render(<AppCard orgSlug="acme" app={app({ icon: "javascript:alert(1)" })} />);
    expect(document.querySelector("svg")).toBeNull();
    expect(document.body.innerHTML).not.toContain("javascript:");
  });
});
