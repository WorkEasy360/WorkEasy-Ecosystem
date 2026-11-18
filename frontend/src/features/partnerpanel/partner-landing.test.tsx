import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import Page from "@/app/partner/[partnerId]/page";
import { PartnerContext } from "@/features/controlplane/ControlShell";
import type { PortalPartner } from "@/lib/api/controlPlane";

const replace = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/partner/p1",
}));

vi.mock("@/features/partnerpanel/Dashboard", () => ({
  PartnerDashboard: () => <h1>The dashboard</h1>,
}));

const partner = (status: string) => ({ partner_id: "p1", status, permissions: [] }) as unknown as PortalPartner;

beforeEach(() => {
  replace.mockReset();
});

describe("the partner portal's landing page", () => {
  it("takes an account waiting for approval to its workspace's onboarding dashboard (ADR-063)", async () => {
    render(
      <PartnerContext.Provider value={{ partner: partner("draft") }}>
        <Page />
      </PartnerContext.Provider>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/partner/affiliate/dashboard"));
    expect(screen.queryByRole("heading", { name: "The dashboard" })).toBeNull();
  });

  it.each(["active", "suspended", "terminated"])("shows a %s account its dashboard", (status) => {
    render(
      <PartnerContext.Provider value={{ partner: partner(status) }}>
        <Page />
      </PartnerContext.Provider>,
    );
    expect(screen.getByRole("heading", { name: "The dashboard" })).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
