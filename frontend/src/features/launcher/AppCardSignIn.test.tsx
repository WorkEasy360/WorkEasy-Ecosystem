import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppCard } from "./AppCard";
import type { AppDirectoryEntry, AppSignIn } from "@/lib/api/types";
import { expectNoViolations } from "@/features/controlplane/testSupport";

function app(signIn: AppSignIn | null, overrides: Partial<AppDirectoryEntry> = {}): AppDirectoryEntry {
  return {
    product_code: "easy_crm",
    display_name: "Easy CRM",
    description: "Sales pipeline.",
    licensing_mode: "per_seat",
    state: "active",
    can_launch: true,
    entitlement_status: "active",
    slug: "easy-crm",
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
    sign_in: signIn,
    ...overrides,
  };
}

const enabled = (effective: AppSignIn["effective"], extra: Partial<AppSignIn> = {}): AppSignIn => ({
  sso: "enabled",
  mode: "sso_optional",
  product_login_available: true,
  override: "inherit",
  preference: effective,
  effective,
  constraint: "",
  ...extra,
});

describe("a product card's sign-in (ADR-044)", () => {
  it("names the product on its Open button and says what opening it does", async () => {
    const { container } = render(<AppCard orgSlug="acme" app={app(enabled("auto_sso"))} />);
    expect(screen.getByRole("button", { name: "Open Easy CRM" })).toBeInTheDocument();
    expect(screen.getByText("Opens with your Workeasy360 account")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign-in preference for Easy CRM" })).toHaveAttribute(
      "href",
      "/o/acme/settings/sign-in-preferences",
    );
    await expectNoViolations(container);
  });

  it("follows the server's effective answer for ask and product sign-in", () => {
    const { unmount } = render(<AppCard orgSlug="acme" app={app(enabled("ask"))} />);
    expect(screen.getByText("Workeasy360 asks how you want to sign in")).toBeInTheDocument();
    unmount();
    render(<AppCard orgSlug="acme" app={app(enabled("product_login"))} />);
    expect(screen.getByText("Opens Easy CRM's own sign-in page")).toBeInTheDocument();
  });

  it("never pretends single sign-on works when it does not", () => {
    render(
      <AppCard
        orgSlug="acme"
        app={app({
          sso: "disabled",
          mode: "local_only",
          product_login_available: true,
          override: "inherit",
          preference: "ask",
          effective: "product_login",
          constraint: "sso_unavailable",
        })}
      />,
    );
    expect(screen.getByText("Separate product sign-in required")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Sign-in preference/ })).not.toBeInTheDocument();
  });

  it("says nothing about sign-in on a card that cannot be opened", () => {
    render(<AppCard orgSlug="acme" app={app(enabled("auto_sso"), { state: "no_entitlement", can_launch: false })} />);
    expect(screen.queryByText("Opens with your Workeasy360 account")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open/ })).not.toBeInTheDocument();
  });
});
