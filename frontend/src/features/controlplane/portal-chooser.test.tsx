import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { PortalChooser } from "./PartnerScreens";
import { mockApi, ok } from "./testSupport";

/**
 * `/partner`: a member of exactly one partner account goes straight to its
 * panel; the list is shown only when there is something to choose.
 */

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  replace.mockReset();
});

const partner = (partner_id: string, display_name: string) => ({ partner_id, display_name, status: "active" });

describe("PortalChooser", () => {
  it("opens the only partner account directly", async () => {
    mockApi({ "GET /api/v1/partner/portal": [ok({ partners: [partner("p1", "Demo Partner")] })] });
    render(<PortalChooser />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/partner/p1"));
    expect(screen.queryByRole("link", { name: "Demo Partner" })).toBeNull();
  });

  it("lists the accounts when there are several", async () => {
    mockApi({ "GET /api/v1/partner/portal": [ok({ partners: [partner("p1", "One"), partner("p2", "Two")] })] });
    render(<PortalChooser />);
    expect(await screen.findByRole("link", { name: "Two" })).toHaveAttribute("href", "/partner/p2");
    expect(replace).not.toHaveBeenCalled();
  });

  it("says so when there are none", async () => {
    mockApi({ "GET /api/v1/partner/portal": [ok({ partners: [] })] });
    render(<PortalChooser />);
    expect(await screen.findByText(/aren.t a member of any partner account/)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
