import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { AppCard } from "@/features/launcher/AppCard";
import { LoginForm } from "@/features/auth/LoginForm";
import { SeatTable } from "@/features/launcher/SeatTable";
import { StatusScreen } from "@/components/StatusScreen";
import { MfaSettings } from "@/features/security/MfaSettings";
import OnboardingPage from "@/app/onboarding/page";
import OrganizationSelectPage from "@/app/organizations/select/page";
import { Shell } from "@/features/launcher/Shell";
import type { AppDirectoryEntry } from "@/lib/api/types";

/**
 * The automated accessibility pass Phase 4 recorded as missing ("built to
 * WCAG patterns by inspection; no automated audit run" -
 * docs/phase-4/11-accessibility.md).
 *
 * **What this covers and what it cannot.** axe-core walks the rendered
 * accessibility tree: roles, accessible names, form labelling, `aria-*`
 * validity, table and list structure, duplicate ids. It cannot check
 * anything needing layout or paint - colour contrast, focus visibility,
 * reflow at 320px, motion - because jsdom computes no styles and paints
 * nothing. Those remain a manual check, and saying so is the point: an
 * automated pass reporting "0 violations" while silently testing none of
 * the visual success criteria is worse than no pass at all.
 *
 * Components rather than whole routes: every page in this app is a client
 * component that renders nothing but a loading state until its own `fetch`
 * resolves, so rendering a route in jsdom audits a spinner. These are what
 * a signed-in person actually looks at.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => "/o/acme/apps",
}));

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };

async function expectNoViolations(container: HTMLElement): Promise<void> {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations
    .map((v: Result) => `${v.id} (${v.impact}): ${v.help}\n    ${v.nodes[0]?.html ?? ""}`)
    .join("\n  ");
  expect(violations, detail).toEqual([]);
}

function app(overrides: Partial<AppDirectoryEntry> = {}): AppDirectoryEntry {
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
    sign_in: null,
    ...overrides,
  };
}

const ALL_STATES: AppDirectoryEntry["state"][] = [
  "active",
  "no_seat",
  "no_entitlement",
  "integration_suspended",
  "product_unavailable",
];

describe("accessibility (axe-core, WCAG 2.1 A + AA, non-visual rules)", () => {
  it.each(ALL_STATES)("an app card in the %s state", async (state) => {
    const { container } = render(
      <main>
        <h1>All apps</h1>
        <AppCard orgSlug="acme" app={app({ state, can_launch: state === "active" })} />
      </main>,
    );
    await expectNoViolations(container);
  });

  it("the sign-in form", async () => {
    const { container } = render(
      <main>
        <h1>Workeasy360</h1>
        <LoginForm />
      </main>,
    );
    await expectNoViolations(container);
  });

  it("the seat-assignment table", async () => {
    // The one table in the app, and the place a screen-reader user is most
    // dependent on structure: a row means nothing without its header.
    global.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          product_code: "crm",
          display_name: "CRM",
          licensing_mode: "per_seat",
          seats_total: 10,
          seats_assigned: 1,
          members: [
            { membership_id: "m1", email: "aman@example.com", held: true },
            { membership_id: "m2", email: "priya@example.com", held: false },
          ],
          next_cursor: null,
          has_more: false,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { container } = render(
      <main>
        <h1>Product access</h1>
        <SeatTable orgSlug="acme" productCode="crm" />
      </main>,
    );
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    await expectNoViolations(container);
  });

  it("a loading state, which a screen reader has to be able to announce", async () => {
    // Rendered bare, as every page renders it: the component supplies its
    // own <main>. A test wrapper used to supply it instead, which hid that
    // production had no main landmark and no skip-link target here.
    const { container } = render(<StatusScreen>Loading your apps…</StatusScreen>);
    await expectNoViolations(container);
  });

  it("an error state", async () => {
    const { container } = render(
      <StatusScreen role="alert">Workeasy360 could not be reached.</StatusScreen>,
    );
    await expectNoViolations(container);
  });
});

/** Routes `fetch` by "METHOD path" to a queue of JSON replies (last repeats). */
function mockApi(routes: Record<string, { status: number; body: unknown }[]>) {
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

const SIGNED_IN = { status: 200, body: { authenticated: true, user_id: "u1", email: "a@example.com" } };
const TOTP_FACTOR = {
  factor_id: "f1",
  kind: "totp",
  label: "Authenticator app",
  confirmed: true,
  created_at: null,
  last_used_at: null,
};

describe("accessibility, Phase 5 UI (axe-core, WCAG 2.1 A + AA, non-visual rules)", () => {
  it("onboarding (create your first organization)", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_IN] });
    const { container } = render(<OnboardingPage />);
    await screen.findByRole("heading", { level: 1, name: "Welcome to Workeasy360" });
    await expectNoViolations(container);
  });

  it("onboarding with a server error shown", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "POST /api/v1/organizations": [{ status: 400, body: { code: "organization_validation_error" } }],
    });
    const { container } = render(<OnboardingPage />);
    fireEvent.change(await screen.findByLabelText("Organization name"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create organization" }));
    const alert = await screen.findByRole("alert");
    expect(screen.getByLabelText("Organization name")).toHaveAttribute("aria-describedby", alert.id);
    await expectNoViolations(container);
  });

  it("the organization chooser", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/organizations": [
        {
          status: 200,
          body: {
            organizations: [
              { organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", membership_id: "m1", is_owner: true },
              { organization_id: "o2", slug: "globex", display_name: "Globex", status: "active", membership_id: "m2", is_owner: false },
            ],
            next_cursor: "c2",
            has_more: true,
          },
        },
      ],
    });
    const { container } = render(<OrganizationSelectPage />);
    await screen.findByRole("heading", { level: 1, name: "Choose an organization" });
    await expectNoViolations(container);
  });

  it("the platform shell: current page and account menu are named, not colour/initials only", async () => {
    mockApi({
      "GET /api/v1/auth/session": [
        { status: 200, body: { authenticated: true, user_id: "u1", email: "aman@example.com", name: "Aman Gupta" } },
      ],
      // The console shell loads who the member is here, and what they may do,
      // in one call (Phase 8 extension) - navigation is derived from it.
      "GET /api/v1/orgs/acme/access": [
        {
          status: 200,
          body: {
            organization: { organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", write_allowed: true },
            membership: { membership_id: "m1", is_owner: false, roles: ["member"] },
            permissions: ["platform.organization.view", "platform.member.view"],
            console: "member",
          },
        },
      ],
    });
    const { container } = render(
      <Shell>
        <h1>All apps</h1>
      </Shell>,
    );
    // Visible text is the initials "AG"; the accessible name says what it is.
    const account = await screen.findByRole("button", { name: "Account menu for Aman Gupta" });
    expect(account).toHaveTextContent("AG");
    expect(screen.getByRole("link", { name: "My products" })).toHaveAttribute("aria-current", "page");
    // The organization switcher is named for what it does, not only by the
    // organization's name.
    expect(
      screen.getByRole("button", { name: "Current organization: Acme. Switch organization." }),
    ).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("security settings with an enrolled factor", async () => {
    mockApi({ "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP_FACTOR] } }] });
    const { container } = render(
      <main>
        <h1>Security</h1>
        <MfaSettings />
      </main>,
    );
    await screen.findByRole("button", { name: "Remove Authenticator app" });
    await expectNoViolations(container);
  });

  it("MFA enrollment (secret + code entry), then the recovery codes shown once", async () => {
    mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [] } }],
      "POST /api/v1/auth/mfa/totp/enroll": [
        { status: 200, body: { factor_id: "f2", secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://totp/x" } },
      ],
      "POST /api/v1/auth/mfa/totp/confirm": [
        { status: 400, body: { code: "mfa_code_invalid" } },
        { status: 200, body: { recovery_codes: ["a1b2c-3d4e5", "f6a7b-8c9d0"] } },
      ],
    });
    const { container } = render(
      <main>
        <h1>Security</h1>
        <MfaSettings />
      </main>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Add an authenticator app" }));
    const input = await screen.findByLabelText("Enter the 6-digit code");
    await expectNoViolations(container);

    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("alert");
    await expectNoViolations(container); // the error state

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });
    await expectNoViolations(container);
  });

  it("the MFA step-up challenge, including its error state", async () => {
    mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP_FACTOR] } }],
      "DELETE /api/v1/auth/mfa/factors/f1": [{ status: 401, body: { code: "step_up_required" } }],
      "POST /api/v1/auth/mfa/step-up": [{ status: 400, body: { code: "mfa_code_invalid" } }],
    });
    const { container } = render(
      <main>
        <h1>Security</h1>
        <MfaSettings />
      </main>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Remove Authenticator app" }));
    const input = await screen.findByLabelText(/recovery code/);
    await expectNoViolations(container);

    fireEvent.change(input, { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByRole("alert");
    await expectNoViolations(container);
  });
});

describe("accessibility properties axe cannot see", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("every app-card state a person must act on says what to do, not only what is wrong", () => {
    // "No seat" alone is a state; "ask an administrator" is a next step.
    // axe checks that text is *reachable*, never that it is *useful*.
    for (const state of ["no_seat", "no_entitlement", "integration_suspended"] as const) {
      const { container, unmount } = render(
        <AppCard orgSlug="acme" app={app({ state, can_launch: false })} />,
      );
      expect(container.textContent ?? "").toMatch(/administrator|explore products|try again/i);
      unmount();
    }
  });

  it("the launch action is a real button, not a clickable div", () => {
    // Keyboard operability: a div with an onClick is unreachable by keyboard
    // and invisible to assistive technology, and `<button type="button">`
    // gets both for free. Phase 11: no longer inside a `<form>` - a real
    // form submission to the launch endpoint is what this app's own CSP
    // (`form-action 'self'`) refuses for every product, whose launch URL is
    // always a different origin (verified in Chrome; see LaunchButton).
    render(<AppCard orgSlug="acme" app={app({ state: "active" })} />);
    const button = screen.getByRole("button", { name: "Open CRM" });
    expect(button).toHaveAttribute("type", "button");
    expect(button.closest("form")).toBeNull();
  });
});
