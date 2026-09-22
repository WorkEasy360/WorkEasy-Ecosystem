import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppCard } from "./AppCard";
import type { AppDirectoryEntry } from "@/lib/api/types";

function app(overrides: Partial<AppDirectoryEntry>): AppDirectoryEntry {
  return {
    product_code: "crm",
    display_name: "CRM",
    description: "Manage your customers.",
    licensing_mode: "per_seat",
    state: "active",
    can_launch: true,
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
  });

  it("renders a Coming soon badge with no action for an unavailable product", () => {
    render(
      <AppCard orgSlug="acme" app={app({ state: "product_unavailable", can_launch: false })} />,
    );
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("never exposes a raw launch_url - only ever a same-origin form action (Task 6/55)", () => {
    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    const form = document.querySelector("form");
    expect(form?.getAttribute("action")).toBe("/api/v1/orgs/acme/apps/crm/launch");
  });
});
