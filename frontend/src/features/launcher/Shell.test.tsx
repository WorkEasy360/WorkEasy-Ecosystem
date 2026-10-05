import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Shell } from "@/features/launcher/Shell";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => "/o/acme/dashboard",
}));

const ACCESS = {
  organization: { organization_id: "o1", slug: "acme", display_name: "Acme", status: "active", write_allowed: true },
  membership: { membership_id: "m1", is_owner: false, roles: ["member"] },
  permissions: ["platform.organization.view"],
  console: "member",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** A fetch whose session reply is held back until `releaseSession()`. */
function heldSessionFetch(session: unknown, access: { status: number; body: unknown }) {
  const seen: string[] = [];
  let releaseSession: () => void = () => undefined;
  const sessionGate = new Promise<void>((resolve) => {
    releaseSession = resolve;
  });
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = (typeof input === "string" ? input : input.toString()).split("?")[0];
    seen.push(path);
    if (path === "/api/v1/auth/session") {
      await sessionGate;
      return json(200, session);
    }
    if (path === "/api/v1/orgs/acme/access") return json(access.status, access.body);
    if (path === "/api/v1/orgs/acme/brand") return json(200, { brand: null });
    throw new Error(`unexpected request ${path}`);
  }) as typeof fetch;
  return { seen, releaseSession: () => releaseSession() };
}

describe("the organization shell (2026-10 audit: no session -> access waterfall)", () => {
  beforeEach(() => replace.mockReset());

  it("asks for access while the session is still loading, then renders", async () => {
    const { seen, releaseSession } = heldSessionFetch(
      { authenticated: true, user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" },
      { status: 200, body: ACCESS },
    );
    render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );

    await waitFor(() => expect(seen).toContain("/api/v1/orgs/acme/access"));
    // Still waiting on the session: nothing below the shell has rendered.
    expect(screen.getByText("Loading Workeasy360…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).toBeNull();

    releaseSession();
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign-in, never showing the access answer", async () => {
    const { releaseSession } = heldSessionFetch(
      { authenticated: false },
      { status: 404, body: { code: "not_found", detail: "Not found." } },
    );
    render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );
    releaseSession();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText(/don.t have access to this organization/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Dashboard" })).toBeNull();
  });
});
