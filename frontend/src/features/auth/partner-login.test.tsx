import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { expectNoViolations, mockApi, ok, type Call } from "@/features/controlplane/testSupport";
import { LoginScreen } from "./LoginScreen";
import { ForgotPasswordScreen, ResetPasswordScreen } from "./SelfService";

/**
 * The Partner sign-in (2026-11-11): the owner's reference image on the existing
 * sign-in endpoint. The server decides everything; these check what the page shows,
 * what it sends, and what it says when refused.
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

const SESSION = "GET /api/v1/auth/session";
const LOGIN = "POST /api/v1/auth/login";
const SIGNED_OUT = ok({ authenticated: false });
const SIGNED_IN = ok({ user_id: "u1", email: "a@example.com", name: "A" });

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  query = "";
});

const loginBody = (calls: Call[]) => calls.find((c) => c.key === LOGIN)?.body;

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email Address"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Login to Partner Portal" }));

describe("the Partner sign-in page", () => {
  it("has the reference image's parts and passes axe", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT] });
    const { container } = render(<LoginScreen portal="partner" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Partner Login" })).toBeInTheDocument();
    expect(screen.getByText(/promote, resell, manage customers and track earnings/)).toBeInTheDocument();
    expect(screen.getByText("For affiliates, resellers and white-label partners")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Remember me" })).not.toBeChecked();
    expect(screen.getByRole("link", { name: "Forgot Password?" })).toHaveAttribute("href", "/forgot-password?mode=partner");
    expect(screen.getByText("New to WorkEasy360?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create Partner Account" })).toHaveAttribute("href", "/partner/apply");
    expect(screen.getByRole("link", { name: "Continue as Guest" })).toHaveAttribute("href", "/partner/preview");

    const benefits = screen.getByRole("region", { name: "Partner Benefits" });
    expect(within(benefits).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Promote Products",
      "Resell & Activate",
      "Earn Commission",
    ]);

    // The story: its four features and the product family.
    const story = container.querySelector("aside") as HTMLElement;
    expect(story).toHaveTextContent("Grow Your Business");
    for (const text of ["Promote Products", "Resell & Activate", "Manage Customers", "Earn Commissions", "Our Product Ecosystem"]) {
      expect(story).toHaveTextContent(text);
    }
    for (const name of ["OneClickAPI", "SailoAI", "VyniAI", "RCS / SMS", "Website", "and More"]) {
      expect(within(story).getByText(name)).toBeInTheDocument();
    }
    await expectNoViolations(container);
  });

  it("shows no figure anywhere: the laptop is decoration, never a number", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT] });
    const { container } = render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    const story = (container.querySelector("aside") as HTMLElement).textContent ?? "";
    expect(story.replaceAll("360", "")).not.toMatch(/\d/);
    expect(container.querySelector("aside svg[aria-hidden='true']")).not.toBeNull();
  });

  it("sends the email, the password and Remember me, then opens the partner portal", async () => {
    const calls = mockApi({ [SESSION]: [SIGNED_OUT], [LOGIN]: [SIGNED_IN] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    fill(" asha@acme.example ", "a long pass phrase");
    fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner"));
    expect(loginBody(calls)).toEqual({ email: "asha@acme.example", password: "a long pass phrase", remember: true });  // pragma: allowlist secret
  });

  it("left unticked, says so to the server", async () => {
    const calls = mockApi({ [SESSION]: [SIGNED_OUT], [LOGIN]: [SIGNED_IN] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    fill("asha@acme.example", "a long pass phrase");
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalled());
    expect(loginBody(calls)).toMatchObject({ remember: false });
  });

  it("opens the partner page it was sent from, never another portal's", async () => {
    query = `next=${encodeURIComponent("/partner/abc/customers")}`;
    mockApi({ [SESSION]: [SIGNED_OUT], [LOGIN]: [SIGNED_IN] });
    const { unmount } = render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    fill("asha@acme.example", "a long pass phrase");
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner/abc/customers"));
    unmount();

    nav.router.replace.mockReset();
    query = `next=${encodeURIComponent("/admin/partners")}`;
    mockApi({ [SESSION]: [SIGNED_OUT], [LOGIN]: [SIGNED_IN] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    fill("asha@acme.example", "a long pass phrase");
    submit();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner"));
  });

  it("asks for what is missing before asking the server", async () => {
    const calls = mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your email address.");
    fill("not-an-address", "x");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a valid email address.");
    expect(calls.some((c) => c.key === LOGIN)).toBe(false);
  });

  it("says what the server refused, and offers the confirmation link again", async () => {
    const calls = mockApi({
      [SESSION]: [SIGNED_OUT],
      [LOGIN]: [
        { status: 401, body: { code: "invalid_credentials" } },
        { status: 403, body: { code: "email_not_verified" } },
      ],
      "POST /api/v1/public/signup/resend": [ok({ status: "accepted" })],
    });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    fill("asha@acme.example", "a long pass phrase");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("That email or password isn't right.");
    expect(screen.getByRole("button", { name: "Login to Partner Portal" })).toBeEnabled();

    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Confirm your email address first");
    fireEvent.click(screen.getByRole("button", { name: "Email me the link again" }));
    expect(await screen.findByRole("status")).toHaveTextContent("only the newest link works");
    expect(calls.find((c) => c.key === "POST /api/v1/public/signup/resend")?.body).toEqual({ email: "asha@acme.example" });
  });

  it("shows and hides the password", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("heading", { name: "Partner Login" });
    const field = screen.getByLabelText("Password") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(field.type).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(field.type).toBe("password");
  });

  it("is the single-host sign-in page when the address says partner", async () => {
    query = "mode=partner";
    mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<LoginScreen />);
    expect(await screen.findByRole("heading", { name: "Partner Login" })).toBeInTheDocument();
  });

  it("keeps the standard page for a product's or a partner's branded sign-in on that host", async () => {
    query = "resume=tok";
    mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<LoginScreen portal="partner" />);
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Partner Login")).toBeNull();
    expect(screen.queryByText("Our Product Ecosystem")).toBeNull();
  });

  it("already signed in, goes straight on to the partner portal", async () => {
    mockApi({ [SESSION]: [ok({ authenticated: true, user_id: "u1", email: "a@example.com", name: "A", consoles: { staff: false, partner: true } })] });
    render(<LoginScreen portal="partner" />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner"));
  });
});

describe("the password pages on the Partner portal", () => {
  it("wear the Partner frame and lead back to the partner sign-in", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT], "POST /api/v1/public/password/forgot": [ok({ status: "accepted" })] });
    const { container } = render(<ForgotPasswordScreen partner />);
    expect(await screen.findByRole("heading", { level: 1, name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByText("Grow Your Business", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login?mode=partner");
    await expectNoViolations(container);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Email me a reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/If an account uses/);
  });

  it("a partner's invitation wears it too", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<ResetPasswordScreen invitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "Accept your invitation" })).toBeInTheDocument();
    expect(screen.getByText("Grow Your Business", { exact: false })).toBeInTheDocument();
  });

  it("the customer portal's stay as they were", async () => {
    mockApi({ [SESSION]: [SIGNED_OUT] });
    render(<ForgotPasswordScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "Reset your password" })).toBeInTheDocument();
    expect(screen.queryByText("Grow Your Business", { exact: false })).toBeNull();
  });
});
