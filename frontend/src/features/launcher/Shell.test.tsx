import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { Shell } from "@/features/launcher/Shell";

const replace = vi.fn();
let pathname = "/o/acme/dashboard";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => pathname,
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

/** A fetch whose session reply is held back until `releaseSession()`. `brand` is
 * what the organization's own brand lookup answers (none, by default). */
function heldSessionFetch(session: unknown, access: { status: number; body: unknown }, brand: unknown = null) {
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
    if (path === "/api/v1/orgs/acme/brand") return json(200, { brand });
    throw new Error(`unexpected request ${path}`);
  }) as typeof fetch;
  return { seen, releaseSession: () => releaseSession() };
}

describe("the organization shell (2026-10 audit: no session -> access waterfall)", () => {
  beforeEach(() => {
    replace.mockReset();
    pathname = "/o/acme/dashboard";
  });

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

describe("a white-label customer's console", () => {
  const SESSION = { authenticated: true, user_id: "u1", email: "ada@example.com", name: "Ada Lovelace" };
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

  const ownBrand = (portal: Record<string, unknown> = {}, over: Record<string, unknown> = {}) => ({
    brand_name: "ABC Business Cloud",
    accent_color: "#336699",
    support_email: "help@abc.example",
    support_url: "https://abc.example/support",
    logo_url: null,
    portal: {
      short_name: "ABC",
      support_name: "Sam from ABC",
      support_phone: "+44 20 7946 0000",
      website_url: "https://abc.example",
      welcome_message: "Welcome to ABC",
      footer_text: "ABC Ltd, 1 High Street",
      login_show_welcome: true,
      login_show_support: true,
      ...portal,
    },
    ...over,
  });

  async function openShell(brand: unknown) {
    const { releaseSession } = heldSessionFetch(SESSION, { status: 200, body: ACCESS }, brand);
    const view = render(
      <Shell>
        <h1>Dashboard</h1>
      </Shell>,
    );
    releaseSession();
    await screen.findByRole("heading", { name: "Dashboard" });
    return view;
  }

  beforeEach(() => {
    replace.mockReset();
    pathname = "/o/acme/dashboard";
  });

  it("names the header by the short name when the partner set one", async () => {
    await openShell(ownBrand());
    const header = screen.getByRole("banner");
    await waitFor(() => expect(within(header).getByText("ABC")).toBeInTheDocument());
    expect(within(header).queryByText("ABC Business Cloud")).not.toBeInTheDocument();
  });

  it("names the header by the brand name when there is no short name", async () => {
    await openShell(ownBrand({ short_name: "" }));
    await waitFor(() => expect(within(screen.getByRole("banner")).getByText("ABC Business Cloud")).toBeInTheDocument());
  });

  it("shows the footer text, the support details and the website, as text and safe links", async () => {
    await openShell(ownBrand());
    expect(await screen.findByText("ABC Ltd, 1 High Street")).toBeInTheDocument();
    expect(screen.getByText("Sam from ABC")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "help@abc.example" })).toHaveAttribute("href", "mailto:help@abc.example");
    expect(screen.getByRole("link", { name: "+44 20 7946 0000" })).toHaveAttribute("href", "tel:+442079460000");
    const support = screen.getByRole("link", { name: "Support page" });
    expect(support).toHaveAttribute("href", "https://abc.example/support");
    expect(support).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("greets the customer on the dashboard, even when the sign-in page does not show the message", async () => {
    await openShell(ownBrand({ login_show_welcome: false }));
    expect(await screen.findByText("Welcome to ABC")).toBeInTheDocument();
  });

  it("keeps the welcome message to the dashboard, and the footer on every page", async () => {
    pathname = "/o/acme/apps";
    await openShell(ownBrand());
    expect(await screen.findByText("ABC Ltd, 1 High Street")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to ABC")).not.toBeInTheDocument();
  });

  it("draws nothing extra for an organization with no white-label partner", async () => {
    await openShell(null);
    expect(within(screen.getByRole("banner")).getByText("Workeasy360")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Visit our website" })).not.toBeInTheDocument();
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
  });

  it("copes with a brand from before the portal existed", async () => {
    await openShell({ brand_name: "Old Brand", accent_color: "", support_email: "", support_url: "", logo_url: null });
    await waitFor(() => expect(within(screen.getByRole("banner")).getByText("Old Brand")).toBeInTheDocument());
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
  });

  it("draws hostile words as text, never as markup or an unsafe link", async () => {
    const { container } = await openShell(
      ownBrand({ footer_text: EVIL, welcome_message: EVIL, support_name: EVIL, website_url: "javascript:alert(1)" }, { support_url: "javascript:alert(1)" }),
    );
    await waitFor(() => expect(container.textContent).toContain("<img"));
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector("a[href^='javascript:']")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
