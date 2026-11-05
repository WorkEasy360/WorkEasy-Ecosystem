import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { PartnerDetail } from "@/lib/api/controlPlane";
import { ExperiencePanel } from "./ExperiencePanel";
import { expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * A partner's experience (ADR-047), changed in a popup (2026-11-05): the panel
 * shows the primary experience as text, and "Change experience" opens the
 * change. What is sent is the action, the experience and a recorded reason -
 * nothing typed as a reason; a refusal stays in the popup.
 */

const ID = "p1";
const CHANGE = `POST /api/v1/admin/partners/${ID}/experience`;

const PARTNER = {
  id: ID,
  status: "active",
  landing: "affiliate",
  workspaces: [{ key: "affiliate", label: "Affiliate" }],
} as unknown as PartnerDetail;

const CATALOG = {
  results: [],
  groups: [],
  presets: [],
  experiences: [
    { key: "affiliate", label: "Affiliate", description: "", capabilities: [], sells: false },
    { key: "reseller", label: "Reseller", description: "", capabilities: [], sells: true },
  ],
};

const PRODUCTS = { results: [{ code: "crm", name: "CRM", status: "available", plans: [], trial: null }] };

function routes(change = [ok({})]) {
  return {
    "GET /api/v1/admin/partners/capabilities": [ok(CATALOG)],
    "GET /api/v1/admin/partners/products": [ok(PRODUCTS)],
    [CHANGE]: change,
  };
}

const onChanged = vi.fn();

beforeEach(() => {
  vi.restoreAllMocks();
  onChanged.mockReset();
});

describe("the experience panel", () => {
  it("adds an experience from a popup, with the products it may sell", async () => {
    const calls = mockApi(routes());
    const { container } = render(<ExperiencePanel partner={PARTNER} manage onChanged={onChanged} />);
    expect(screen.getByText("Affiliate", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Change experience" }));
    const popup = screen.getByRole("dialog", { name: "Change the experience" });
    const apply = within(popup).getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    await within(popup).findByRole("option", { name: "Reseller" });
    fireEvent.change(within(popup).getByLabelText("Experience"), { target: { value: "reseller" } });
    fireEvent.click(await within(popup).findByRole("checkbox", { name: "CRM" }));
    fireEvent.click(apply);

    expect(await screen.findByText(/Experience added\./)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === CHANGE)?.body).toEqual({
      action: "grant",
      experience: "reseller",
      reason: "Add an experience - Super Admin console",
      make_default: true,
      products: ["crm"],
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("keeps a refusal in the popup and changes nothing", async () => {
    mockApi(routes([{ status: 400, body: { detail: "This partner is not active." } }]));
    render(<ExperiencePanel partner={PARTNER} manage onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Change experience" }));
    const popup = screen.getByRole("dialog", { name: "Change the experience" });
    await within(popup).findByRole("option", { name: "Reseller" });
    fireEvent.change(within(popup).getByLabelText("Experience"), { target: { value: "reseller" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Apply" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("This partner is not active.");
    expect(within(popup).getByLabelText("Experience")).toHaveValue("reseller");
    await waitFor(() => expect(onChanged).not.toHaveBeenCalled());
  });

  it("offers no change to a viewer or for an ended partner", () => {
    mockApi(routes());
    const { unmount } = render(<ExperiencePanel partner={PARTNER} manage={false} onChanged={onChanged} />);
    expect(screen.queryByRole("button", { name: "Change experience" })).not.toBeInTheDocument();
    unmount();
    render(<ExperiencePanel partner={{ ...PARTNER, status: "terminated" } as PartnerDetail} manage onChanged={onChanged} />);
    expect(screen.queryByRole("button", { name: "Change experience" })).not.toBeInTheDocument();
  });
});
