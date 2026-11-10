import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { expectNoViolations } from "@/features/controlplane/testSupport";
import { GuestPreviewScreen } from "./GuestPreview";

/**
 * The guest preview (2026-11-10): a static page with example figures. It asks the
 * server for nothing, so a guest reaches no record and no API, and it says plainly
 * that a guest is not a partner.
 */

afterEach(() => vi.restoreAllMocks());

describe("the guest preview", () => {
  it("makes no request at all", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<GuestPreviewScreen />);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("labels everything as example data and separates guest, partner and active partner", async () => {
    const { container } = render(<GuestPreviewScreen />);
    expect(screen.getByText("Guest preview · example data only")).toBeInTheDocument();
    for (const word of ["Guest preview", "Partner login", "Active partner"]) {
      expect(screen.getByText(word, { selector: "strong" })).toBeInTheDocument();
    }
    for (const example of screen.getAllByText(/^Example:/)) expect(example).toBeInTheDocument();
    expect(screen.getByText(/never shown to guests/)).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("offers applying, signing in and leaving - and nothing that acts", () => {
    render(<GuestPreviewScreen />);
    expect(screen.getByRole("link", { name: "Apply to become a partner" })).toHaveAttribute("href", "/partner/apply");
    expect(screen.getByRole("link", { name: "Sign in as a partner" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "Exit guest preview" })).toHaveAttribute("href", "/login");
    const main = screen.getByRole("main");
    expect(within(main).queryAllByRole("button")).toHaveLength(0);
    expect(within(main).queryAllByRole("textbox")).toHaveLength(0);
  });
});
