import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { SignInSetup } from "@/lib/api/controlPlane";
import { SignInSetupCard } from "./SignInSetupCard";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "../testSupport";

/**
 * Super Admin -> Products -> a product -> Integration -> Automatic sign-in
 * setup (ADR-074): the state the platform recorded - never one this page
 * decides - every check with its reason, the client id to configure the
 * product with, and Check now for managers only.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams("tab=integration"),
  usePathname: () => "/admin/products/id-orchard",
}));

const BASE = "/api/v1/admin/products/orchard_planner/integration/sign-in-setup";

function setup(overrides: Partial<SignInSetup> = {}): SignInSetup {
  return {
    product_code: "orchard_planner",
    automatic: true,
    state: "verification_required",
    state_label: "Verification Required",
    checks: [
      { key: "application_url", label: "Application URL", status: "passed", detail: "https://orchard.example.com" },
      { key: "proof", label: "Key possession proof", status: "passed", detail: "The application holds the key it publishes." },
      {
        key: "domain",
        label: "Domain ownership",
        status: "pending",
        detail: 'Publish TXT _w360-product-verify.orchard.example.com = "w360-product-verify=abc", then Check now.',
      },
    ],
    application_url: "https://orchard.example.com",
    document_url: "https://orchard.example.com/.well-known/workeasy360-sso",
    client_id: "cid-orchard",
    integration_status: "draft",
    connector: { name: "workeasy360-sso", version: "0.4.0" },
    attempts: 1,
    last_checked_at: "2026-11-19T10:00:00Z",
    next_check_at: "2026-11-19T10:10:00Z",
    activated_at: null,
    ...overrides,
  };
}

function renderCard(permissions: string[], manage: boolean) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <SignInSetupCard code="orchard_planner" manage={manage} />
    </AdminContextForTest>,
  );
}

describe("Automatic sign-in setup", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the recorded state, the client id and each check with its reason", async () => {
    mockApi({ [`GET ${BASE}`]: [ok(setup())] });
    const { container } = renderCard(["platform.staff_integration.manage"], true);
    expect(await screen.findByText("Verification Required")).toBeInTheDocument();
    expect(screen.getByText("cid-orchard")).toBeInTheDocument();
    expect(screen.getByText("https://orchard.example.com/.well-known/workeasy360-sso")).toBeInTheDocument();
    expect(screen.getByText("workeasy360-sso 0.4.0")).toBeInTheDocument();
    const checks = within(screen.getByRole("list", { name: "Sign-in setup checks" })).getAllByRole("listitem");
    expect(checks).toHaveLength(3);
    expect(checks[2]).toHaveTextContent("Domain ownership");
    expect(checks[2]).toHaveTextContent("TXT _w360-product-verify.orchard.example.com");
    expect(checks[2]).toHaveTextContent("Waiting");
    await expectNoViolations(container);
  });

  it.each([
    ["pending_configuration", "Pending Configuration"],
    ["configured", "Configured"],
    ["verification_required", "Verification Required"],
    ["active", "Active"],
    ["failed", "Failed"],
  ] as const)("labels %s as the server named it", async (state, label) => {
    mockApi({ [`GET ${BASE}`]: [ok(setup({ state, state_label: label }))] });
    renderCard([], false);
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("asks the server to check now, then shows its new answer", async () => {
    const calls = mockApi({
      [`GET ${BASE}`]: [ok(setup()), ok(setup({ state: "active", state_label: "Active", checks: [] }))],
      [`POST ${BASE}/check`]: [ok(setup({ state: "active", state_label: "Active", checks: [] }))],
    });
    renderCard(["platform.staff_integration.manage"], true);
    fireEvent.click(await screen.findByRole("button", { name: "Check the sign-in setup now" }));
    expect(await screen.findByText("Active")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === `POST ${BASE}/check`)).toHaveLength(1);
  });

  it("offers Check now to managers only", async () => {
    mockApi({ [`GET ${BASE}`]: [ok(setup())] });
    renderCard(["platform.staff_integration.view"], false);
    await screen.findByText("Verification Required");
    expect(screen.queryByRole("button", { name: /Check/ })).not.toBeInTheDocument();
  });

  it("says a product set up by hand is never switched off by the checks", async () => {
    mockApi({ [`GET ${BASE}`]: [ok(setup({ automatic: false, state: null, state_label: "Set up by hand", checks: [] }))] });
    renderCard(["platform.staff_integration.manage"], true);
    expect(await screen.findByText(/set up by hand/)).toHaveTextContent("never switched off");
  });

  it("shows a refused check as an alert and keeps the last answer", async () => {
    mockApi({
      [`GET ${BASE}`]: [ok(setup())],
      [`POST ${BASE}/check`]: [{ status: 429, body: { detail: "Too many checks. Wait a minute." } }],
    });
    renderCard(["platform.staff_integration.manage"], true);
    fireEvent.click(await screen.findByRole("button", { name: "Check the sign-in setup now" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByText("Verification Required")).toBeInTheDocument();
  });
});
