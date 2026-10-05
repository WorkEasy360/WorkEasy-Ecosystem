import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { SidebarShell } from "./SidebarShell";

vi.mock("next/navigation", () => ({ usePathname: () => "/o/acme/dashboard" }));

/** The wordmark a white-label organization sees instead of "Workeasy360"
 * (Phase 11, Step 30) - and the default every other console keeps. */
describe("SidebarShell branding", () => {
  it("shows Workeasy360 by default", () => {
    render(
      <SidebarShell area="Organization" groups={[]}>
        <p>Page</p>
      </SidebarShell>,
    );
    expect(screen.getByText("Workeasy360")).toBeInTheDocument();
  });

  it("shows a white-label customer's own brand name and logo instead, when given one", () => {
    render(
      <SidebarShell area="Organization" groups={[]} brandName="ABC Business Cloud" brandLogoUrl="/api/v1/orgs/acme/brand/logo">
        <p>Page</p>
      </SidebarShell>,
    );
    expect(screen.queryByText("Workeasy360")).not.toBeInTheDocument();
    expect(screen.getByText("ABC Business Cloud")).toBeInTheDocument();
    // Decorative (empty alt): the brand name right next to it already says
    // this in text, so a screen reader must not announce the image too.
    const logo = document.querySelector("img");
    expect(logo).toHaveAttribute("src", "/api/v1/orgs/acme/brand/logo");
    expect(logo).toHaveAttribute("alt", "");
  });

  it("shows the brand name with no logo image when none is set", () => {
    render(
      <SidebarShell area="Organization" groups={[]} brandName="ABC Business Cloud" brandLogoUrl={null}>
        <p>Page</p>
      </SidebarShell>,
    );
    expect(screen.getByText("ABC Business Cloud")).toBeInTheDocument();
    expect(document.querySelector("img")).not.toBeInTheDocument();
  });
});
