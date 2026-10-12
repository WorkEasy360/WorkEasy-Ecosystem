import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { LoginScreen } from "./LoginScreen";
import { BrandedLanding } from "./BrandedLanding";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";

/**
 * The fields a partner sets in the white-label wizard, as a customer sees them
 * on the way in: the welcome message and the support details (each on its own
 * sign-in switch) and the website. Plain text and safe links only, and still no
 * credential field on a partner's custom domain (D-035).
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

const SIGNED_OUT: Reply = ok({ authenticated: false });
const HANDOFF = "https://app.workeasy360.com/login?brand=app.wl-brand.com";
const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

const portal = (over: Record<string, unknown> = {}) => ({
  short_name: "WL",
  support_name: "Sam from WL",
  support_phone: "+44 20 7946 0000",
  website_url: "https://wl-brand.com",
  welcome_message: "Welcome to the WL customer portal",
  footer_text: "WL Ltd",
  login_show_welcome: true,
  login_show_support: true,
  ...over,
});

const hostBrand = (over: Record<string, unknown> = {}, portalOver: Record<string, unknown> = {}) => ({
  brand_name: "WL Brand",
  accent_color: "#0f766e",
  support_email: "help@wl-brand.com",
  support_url: "https://wl-brand.com/support",
  logo_url: null,
  portal: portal(portalOver),
  ...over,
});

function stubBrand(brand: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ brand }), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  query = "";
});

describe("a partner's custom domain (the landing page)", () => {
  it("shows the welcome message, the support details and the website, as text and safe links", async () => {
    stubBrand(hostBrand());
    const { container } = render(<BrandedLanding handoffUrl={HANDOFF} />);
    expect(await screen.findByText("Welcome to the WL customer portal")).toBeInTheDocument();
    expect(screen.getByText("Sam from WL")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "help@wl-brand.com" })).toHaveAttribute("href", "mailto:help@wl-brand.com");
    expect(screen.getByRole("link", { name: "+44 20 7946 0000" })).toHaveAttribute("href", "tel:+442079460000");
    const support = screen.getByRole("link", { name: "Support page" });
    expect(support).toHaveAttribute("href", "https://wl-brand.com/support");
    expect(support).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("rel", "noopener noreferrer");
    await expectNoViolations(container);
  });

  it("still has no credential field and no form: the hand-over link is the only way on", async () => {
    stubBrand(hostBrand());
    const { container } = render(<BrandedLanding handoffUrl={HANDOFF} />);
    await screen.findByText("Welcome to the WL customer portal");
    expect(container.querySelector("input")).toBeNull();
    expect(container.querySelector("form")).toBeNull();
    expect(container.querySelector("textarea, select")).toBeNull();
    expect(screen.getByRole("link", { name: "Continue to sign in" })).toHaveAttribute("href", HANDOFF);
  });

  it("leaves out the welcome message when the partner turned it off for sign-in", async () => {
    stubBrand(hostBrand({}, { login_show_welcome: false }));
    render(<BrandedLanding handoffUrl={HANDOFF} />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WL Brand"));
    await screen.findByRole("link", { name: "help@wl-brand.com" });
    expect(screen.queryByText("Welcome to the WL customer portal")).not.toBeInTheDocument();
  });

  it("leaves out the support details, but not the website, when the partner turned them off", async () => {
    stubBrand(hostBrand({}, { login_show_support: false }));
    render(<BrandedLanding handoffUrl={HANDOFF} />);
    await screen.findByText("Welcome to the WL customer portal");
    await screen.findByRole("link", { name: "Visit our website" });
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "help@wl-brand.com" })).not.toBeInTheDocument();
    expect(screen.queryByText("Sam from WL")).not.toBeInTheDocument();
  });

  it("draws hostile words as text and never links an unsafe address", async () => {
    stubBrand(
      hostBrand({ support_url: "javascript:alert(1)", support_email: "x@y.example?subject=1" }, { welcome_message: EVIL, support_name: EVIL, website_url: "http://insecure.example", support_phone: "call me" }),
    );
    const { container } = render(<BrandedLanding handoffUrl={HANDOFF} />);
    await waitFor(() => expect(container.textContent).toContain("<img"));
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelector("a[href^='javascript:']")).toBeNull();
    expect(container.querySelector("a[href^='http:']")).toBeNull();
    expect(container.querySelector("a[href^='mailto:']")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("copes with a brand that carries no portal at all", async () => {
    stubBrand({ brand_name: "Old Brand", accent_color: "", support_email: "", support_url: "", logo_url: null });
    render(<BrandedLanding handoffUrl={HANDOFF} />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Old Brand"));
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
  });
});

describe("the canonical sign-in page, branded for a partner", () => {
  const BRANDING = "GET /api/v1/public/branding";

  function open(brand: unknown) {
    query = "brand=app.wl-brand.com";
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], [BRANDING]: [ok({ brand })] });
    return render(<LoginScreen />);
  }

  it("puts the partner's welcome message where the platform's tagline was, and shows support and website under the card", async () => {
    const { container } = open(hostBrand());
    expect(await screen.findByText("Welcome to the WL customer portal")).toBeInTheDocument();
    expect(screen.queryByText("One ecosystem to run your entire business.")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WL Brand");
    expect(screen.getByRole("link", { name: "help@wl-brand.com" })).toHaveAttribute("href", "mailto:help@wl-brand.com");
    expect(screen.getByRole("link", { name: "+44 20 7946 0000" })).toHaveAttribute("href", "tel:+442079460000");
    expect(screen.getByRole("link", { name: "Support page" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("href", "https://wl-brand.com/");
    // Authentication is the platform's own: the one sign-in form is still here.
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("keeps the platform's tagline when the partner turned its welcome message off", async () => {
    open(hostBrand({}, { login_show_welcome: false }));
    await screen.findByRole("link", { name: "help@wl-brand.com" });
    expect(screen.queryByText("Welcome to the WL customer portal")).not.toBeInTheDocument();
    expect(screen.getByText("One ecosystem to run your entire business.")).toBeInTheDocument();
  });

  it("hides the support details when the partner turned them off, and keeps the website", async () => {
    open(hostBrand({}, { login_show_support: false }));
    await screen.findByRole("link", { name: "Visit our website" });
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "help@wl-brand.com" })).not.toBeInTheDocument();
  });

  it("shows no support block, and no website, for an unbranded sign-in", async () => {
    query = "mode=user";
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT], [BRANDING]: [{ status: 200, body: { brand: null } }] });
    render(<LoginScreen />);
    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Visit our website" })).not.toBeInTheDocument();
    expect(screen.getByText("One ecosystem to run your entire business.")).toBeInTheDocument();
  });

  it("draws hostile words as text", async () => {
    const { container } = open(hostBrand({ brand_name: EVIL }, { welcome_message: EVIL, support_name: EVIL }));
    await waitFor(() => expect(container.textContent).toContain("<img"));
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
