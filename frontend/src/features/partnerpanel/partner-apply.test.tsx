import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { LoginScreen } from "@/features/auth/LoginScreen";
import { expectNoViolations, mockApi, ok, type Call } from "@/features/controlplane/testSupport";
import { PartnerApplyScreen } from "./PartnerApply";

/**
 * The public partner application (2026-11-09). It grants nothing and never says whether
 * an address is registered; the partner sign-in page is the way to it.
 */

const countries = vi.hoisted(() => [
  { value: "", label: "Choose a country" },
  { value: "IN", label: "India" },
  { value: "US", label: "United States" },
]);
// Every role query walks the real list's 250 options; which countries exist is pinned elsewhere.
vi.mock("@/features/controlplane/countries", async (original) => ({
  ...(await original<typeof import("@/features/controlplane/countries")>()),
  countryOptions: () => countries,
}));

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/partner/apply",
}));

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

const APPLY = "/api/v1/public/partner-applications";
const SESSION = "/api/v1/auth/session";
const SIGNED_OUT = ok({ authenticated: false });

const bodyOf = (calls: Call[]) => calls.find((c) => c.key === `POST ${APPLY}`)?.body;

function fill(overrides: Partial<Record<"business" | "password", string>> = {}) {
  fireEvent.change(screen.getByLabelText("Business name"), { target: { value: overrides.business ?? " Acme Partners " } });
  fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Asha" } });
  fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Rao" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: " asha@acme.example " } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: overrides.password ?? "correct horse battery" } });
  fireEvent.change(screen.getByLabelText("Country"), { target: { value: "IN" } });
  fireEvent.click(screen.getByRole("radio", { name: "White-label" }));
}

const send = () => fireEvent.click(screen.getByRole("button", { name: "Send application" }));

describe("the partner application", () => {
  it("says applying grants nothing, offers the three ways to partner, and passes axe", async () => {
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    const { container } = render(<PartnerApplyScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Apply to become a partner" })).toBeInTheDocument();
    // The real logo, not a letter and a word in plain text.
    expect(container.querySelector("[data-w360-logo]")).not.toBeNull();
    expect(screen.queryByText("W")).toBeNull();
    expect(screen.getByText(/does not grant any partner rights/)).toHaveTextContent(/only once your KYC is approved/);
    expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).value)).toEqual(["affiliate", "reseller", "white_label"]);
    expect(screen.getByRole("radio", { name: "Affiliate" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Reseller" })).toBeInTheDocument();
    expect(screen.getByText(/At least 12 characters/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "India" })).toBeInTheDocument();
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    await expectNoViolations(container);
  });

  it("sends the exact body and then says only to check the email", async () => {
    const calls = mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT], [`POST ${APPLY}`]: [{ status: 202, body: { status: "accepted" } }] });
    const { container } = render(<PartnerApplyScreen />);
    fill();
    fireEvent.change(screen.getByLabelText("Phone (optional)"), { target: { value: "+91 98765 43210" } });
    send();
    expect(await screen.findByRole("heading", { level: 1, name: "Check your email" })).toBeInTheDocument();
    expect(bodyOf(calls)).toEqual({
      business_name: "Acme Partners",
      first_name: "Asha",
      last_name: "Rao",
      email: "asha@acme.example",
      password: "correct horse battery",  // pragma: allowlist secret
      country: "IN",
      phone: "+91 98765 43210",
      interest: "white_label",
    });
    expect(screen.getByRole("status")).toHaveTextContent("asha@acme.example");
    expect(document.body).not.toHaveTextContent(/already|exists|registered/i);
    expect(screen.getByText(/does not grant any partner rights/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to partner sign-in" })).toHaveAttribute("href", "/login?mode=partner");
    await expectNoViolations(container);
  });

  it("leaves a blank phone out", async () => {
    const calls = mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT], [`POST ${APPLY}`]: [{ status: 202, body: { status: "accepted" } }] });
    render(<PartnerApplyScreen />);
    fill();
    send();
    await screen.findByRole("heading", { level: 1, name: "Check your email" });
    expect(bodyOf(calls)).not.toHaveProperty("phone");
  });

  it("validates before sending", async () => {
    const calls = mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    render(<PartnerApplyScreen />);
    send();
    const alert = await screen.findByRole("alert");
    for (const text of ["Enter your business name.", "Enter your email address.", "at least 12 characters", "Choose your country.", "Choose how you want to partner."]) {
      expect(alert).toHaveTextContent(text);
    }
    fill({ password: "too short" });  // pragma: allowlist secret
    send();
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a password of at least 12 characters.");
    expect(calls.some((c) => c.key === `POST ${APPLY}`)).toBe(false);
  });

  it("shows the server's refusal and keeps what was typed", async () => {
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT], [`POST ${APPLY}`]: [{ status: 400, body: { detail: "Partner applications are closed at the moment." } }] });
    render(<PartnerApplyScreen />);
    fill();
    send();
    expect(await screen.findByRole("alert")).toHaveTextContent("Partner applications are closed at the moment.");
    expect(screen.getByLabelText("First name")).toHaveValue("Asha");
    expect(screen.getByRole("button", { name: "Send application" })).toBeEnabled();
  });

  it("lists every password problem the server names", async () => {
    mockApi({
      [`GET ${SESSION}`]: [SIGNED_OUT],
      [`POST ${APPLY}`]: [{ status: 400, body: { code: "password_policy_violation", problems: ["This password is too common.", "It is too similar to your email."] } }],
    });
    render(<PartnerApplyScreen />);
    fill();
    send();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This password is too common.");
    expect(alert).toHaveTextContent("It is too similar to your email.");
  });

  it("says to wait when there were too many attempts", async () => {
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT], [`POST ${APPLY}`]: [{ status: 429, body: {} }] });
    render(<PartnerApplyScreen />);
    fill();
    send();
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts.");
  });
});

describe("the link from the sign-in page", () => {
  const LINK = { name: "Apply to become a partner" };

  it("is on the partner sign-in page", async () => {
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    const { container } = render(<LoginScreen portal="partner" />);
    expect(await screen.findByRole("link", LINK)).toHaveAttribute("href", "/partner/apply");
    await expectNoViolations(container);
  });

  it("is on the single-host sign-in page only when it says partner", async () => {
    query = "mode=partner";
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    const { unmount } = render(<LoginScreen />);
    expect(await screen.findByRole("link", LINK)).toBeInTheDocument();
    unmount();

    query = "mode=user";
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    render(<LoginScreen />);
    await screen.findByRole("heading", { name: "Sign in as User" });
    expect(screen.queryByRole("link", LINK)).not.toBeInTheDocument();
  });

  it("is on no other portal's sign-in page, and not on a partner's branded one", async () => {
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    const { unmount } = render(<LoginScreen portal="app" />);
    await screen.findByRole("heading", { name: "Sign in as User" });
    expect(screen.queryByRole("link", LINK)).not.toBeInTheDocument();
    unmount();

    query = "resume=tok";
    mockApi({ [`GET ${SESSION}`]: [SIGNED_OUT] });
    render(<LoginScreen portal="partner" />);
    await screen.findByRole("button", { name: "Sign in" });
    expect(screen.queryByRole("link", LINK)).not.toBeInTheDocument();
  });
});
