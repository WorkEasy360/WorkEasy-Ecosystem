import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { configure, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AcceptOwnerInvitation } from "./AcceptOwnerInvitation";
import { expectNoViolations, mockApi, type Call, type Reply } from "../controlplane/testSupport";

/**
 * Phase 10e (D-020): a customer's owner activates the account a partner set up.
 * What the browser must get right that the backend cannot enforce for it: the
 * link never stays in the address bar, is only ever POSTed, the password is
 * chosen here and sent nowhere else, an existing account signs in rather than
 * being replaced, and every state is reachable and named for a keyboard and a
 * screen reader.
 */

const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/owner-invitations/accept",
}));

configure({ asyncUtilTimeout: 4000 });

const TOKEN = "0123456789abcdef0123456789abcdef.secret-part-of-the-link";
const SIGNED_OUT: Reply = { status: 200, body: { authenticated: false } };
const PREVIEW = {
  organization_name: "Acme Ltd",
  partner_name: "Reseller One",
  email: "owner@acme.test",
  expires_at: "2026-10-12T00:00:00Z",
  account_exists: false,
  signed_in_as_invitee: false,
  needs_name: true,
};
const PREVIEW_URL = "POST /api/v1/public/owner-invitations/preview";
const ACCEPT_URL = "POST /api/v1/public/owner-invitations/accept";
const sent = (calls: Call[], key: string) => calls.filter((c) => c.key === key);

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  window.history.replaceState(null, "", `/owner-invitations/accept#token=${TOKEN}`);
});
afterEach(() => {
  window.history.replaceState(null, "", "/");
});

function fillPassword(password = "correct horse battery", confirm = password) {
  fireEvent.change(screen.getByLabelText("Choose a password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm the password"), { target: { value: confirm } });
}

describe("activating as a new person", () => {
  it("takes the link out of the address bar, previews it, and sets the password", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: PREVIEW }],
      [ACCEPT_URL]: [
        { status: 400, body: { code: "password_policy_violation", detail: "x", problems: ["This password is too common."] } },
        { status: 200, body: { organization_slug: "acme", organization_id: "o1", credentials_set: true } },
      ],
    });
    const { container } = render(<AcceptOwnerInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "Activate Acme Ltd" })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/owner-invitations/accept");
    expect(container).toHaveTextContent("Reseller One");
    expect(container).toHaveTextContent("owner@acme.test");
    expect(container.textContent).not.toContain(TOKEN);
    expect(sent(calls, PREVIEW_URL)[0].body).toEqual({ token: TOKEN });
    await expectNoViolations(container);

    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Nadia Owner" } });
    fillPassword();
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This password is too common.");
    await expectNoViolations(container);

    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(sent(calls, ACCEPT_URL)[1].body).toEqual({
      token: TOKEN,
      full_name: "Nadia Owner",
      password: "correct horse battery",
    });
  });

  it("does not ask for a name the account already has, and sends none", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: { ...PREVIEW, needs_name: false } }],
      [ACCEPT_URL]: [{ status: 200, body: { organization_slug: "acme", organization_id: "o1", credentials_set: true } }],
    });
    render(<AcceptOwnerInvitation />);
    await screen.findByLabelText("Choose a password");
    expect(screen.queryByLabelText("Your name")).not.toBeInTheDocument();
    fillPassword();
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(sent(calls, ACCEPT_URL)[0].body).toEqual({ token: TOKEN, password: "correct horse battery" });
  });

  it("checks the passwords match before anything is sent", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: PREVIEW }],
    });
    render(<AcceptOwnerInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nadia" } });
    fillPassword("one long passphrase", "another passphrase");
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The passwords don't match.");
    expect(sent(calls, ACCEPT_URL)).toHaveLength(0);
  });

  it("never puts the password anywhere but the one accept request", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: PREVIEW }],
      [ACCEPT_URL]: [{ status: 200, body: { organization_slug: "acme", organization_id: "o1", credentials_set: true } }],
    });
    render(<AcceptOwnerInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nadia" } });
    fillPassword("a private passphrase 7331");
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalled());
    for (const call of calls) {
      expect(call.url).not.toContain("passphrase");
      expect(call.url).not.toContain(TOKEN);
      if (call.key !== ACCEPT_URL) expect(JSON.stringify(call.body ?? {})).not.toContain("passphrase");
    }
    expect(window.location.href).not.toContain("passphrase");
  });
});

describe("an address that already has an account", () => {
  it("signs in with it and accepts, so there is never a second account", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [
        { status: 200, body: { ...PREVIEW, account_exists: true } },
        { status: 200, body: { ...PREVIEW, account_exists: true, signed_in_as_invitee: true, needs_name: false } },
      ],
      "POST /api/v1/auth/login": [{ status: 200, body: { user_id: "u1", email: "owner@acme.test", name: "Nadia" } }],
      [ACCEPT_URL]: [{ status: 200, body: { organization_slug: "acme", organization_id: "o1", credentials_set: false } }],
    });
    const { container } = render(<AcceptOwnerInvitation />);
    expect(await screen.findByText(/You already have an account for owner@acme.test/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Choose a password")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Confirm the password")).not.toBeInTheDocument();
    await expectNoViolations(container);

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "my existing password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const accept = await screen.findByRole("button", { name: "Become the owner of Acme Ltd" });
    expect(sent(calls, "POST /api/v1/auth/login")[0].body).toEqual({ email: "owner@acme.test", password: "my existing password" });
    await expectNoViolations(container);

    fireEvent.click(accept);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
    expect(sent(calls, ACCEPT_URL)[0].body).toEqual({ token: TOKEN });
  });

  it("says so when the password is wrong, and stays signed out", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: { ...PREVIEW, account_exists: true } }],
      "POST /api/v1/auth/login": [{ status: 401, body: { code: "invalid_credentials", detail: "no" } }],
    });
    render(<AcceptOwnerInvitation />);
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "not it" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That email and password don't match.");
    expect(screen.queryByRole("button", { name: /Become the owner/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Forgot your password?" })).toHaveAttribute("href", "/forgot-password");
  });

  it("moves to sign-in when accepting finds the account already exists", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: PREVIEW }],
      [ACCEPT_URL]: [{ status: 409, body: { code: "sign_in_required", detail: "An account already exists." } }],
    });
    render(<AcceptOwnerInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nadia" } });
    fillPassword();
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    expect(await screen.findByText(/You already have an account for owner@acme.test/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Choose a password")).not.toBeInTheDocument();
  });

  it("the signed-in owner accepts with one deliberate button", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u1", email: "owner@acme.test" } }],
      [PREVIEW_URL]: [{ status: 200, body: { ...PREVIEW, account_exists: true, signed_in_as_invitee: true } }],
      [ACCEPT_URL]: [{ status: 200, body: { organization_slug: "acme", organization_id: "o1", credentials_set: false } }],
    });
    render(<AcceptOwnerInvitation />);
    const accept = await screen.findByRole("button", { name: "Become the owner of Acme Ltd" });
    expect(sent(calls, ACCEPT_URL)).toHaveLength(0);
    fireEvent.click(accept);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme"));
  });
});

describe("the wrong person, and links that cannot be used", () => {
  it("asks someone signed in as another account to sign out, then asks again as the new visitor", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [{ status: 200, body: { authenticated: true, user_id: "u9", email: "someone@else.test" } }],
      [PREVIEW_URL]: [
        { status: 200, body: PREVIEW },
        { status: 200, body: PREVIEW },
      ],
      "POST /api/v1/auth/logout": [{ status: 204 }],
    });
    const { container } = render(<AcceptOwnerInvitation />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You're signed in as someone@else.test, but this link was sent to owner@acme.test");
    expect(screen.queryByRole("button", { name: /Become the owner/ })).not.toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Choose a password")).toBeInTheDocument();
    expect(sent(calls, PREVIEW_URL)).toHaveLength(2);
  });

  it("explains a link that is expired, replaced, withdrawn or used - one message for all", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 404, body: { code: "invitation_invalid", detail: "This link is not valid." } }],
    });
    const { container } = render(<AcceptOwnerInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "This link can't be used" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/expired, been replaced.*withdrawn, or already been used/);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    await expectNoViolations(container);
  });

  it("shows the same page, and asks the server nothing, when there is no link at all", async () => {
    window.history.replaceState(null, "", "/owner-invitations/accept");
    const calls = mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    render(<AcceptOwnerInvitation />);
    expect(await screen.findByRole("heading", { level: 1, name: "This link can't be used" })).toBeInTheDocument();
    expect(sent(calls, PREVIEW_URL)).toHaveLength(0);
  });

  it("offers to try again when the server cannot be reached", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 500, body: {} }, { status: 200, body: PREVIEW }],
    });
    render(<AcceptOwnerInvitation />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Activate Acme Ltd" })).toBeInTheDocument();
  });

  it("says who to contact when the organization is not accepting changes", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      [PREVIEW_URL]: [{ status: 200, body: PREVIEW }],
      [ACCEPT_URL]: [{ status: 402, body: { code: "organization_not_writable", detail: "no" } }],
    });
    render(<AcceptOwnerInvitation />);
    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nadia" } });
    fillPassword();
    fireEvent.click(screen.getByRole("button", { name: "Set password and continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Contact Reseller One");
  });
});
