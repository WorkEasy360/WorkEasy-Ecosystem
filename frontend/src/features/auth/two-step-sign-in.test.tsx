import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PortalsProvider } from "@/lib/portalContext";
import type { PortalOrigins } from "@/lib/portals";
import { mockApi, ok, type Call, type Reply } from "@/features/controlplane/testSupport";
import { StoreSignInForm } from "@/features/store/StoreAuth";
import { AcceptOwnerInvitation } from "@/features/console/AcceptOwnerInvitation";
import { LoginScreen } from "./LoginScreen";
import { EXPIRED_MESSAGE, normalizeCode, twoStepChallenge } from "./TwoStepSignIn";
import { ApiError } from "@/lib/api/client";

/**
 * Accounts V3: every form that posts `auth/login` handles 401 `two_step_required` -
 * one shared code step (`TwoStepSignIn`) that posts `auth/login/two-step` and then
 * continues exactly as that form's own successful sign-in does. A wrong code stays on
 * the step; an expired challenge goes back to the password. The challenge and the code
 * never appear in a URL.
 */

let query = "";
const nav = vi.hoisted(() => ({ router: { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/login",
}));

configure({ asyncUtilTimeout: 4000 });

const ORIGINS: PortalOrigins = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://accounts.workeasy360.com",
  store: "",
};
const SIGNED_OUT: Reply = ok({ authenticated: false });
const CHALLENGE = "ch-opaque-1";
const NEEDS_CODE: Reply = {
  status: 401,
  body: { code: "two_step_required", challenge: CHALLENGE, detail: "Enter the 6-digit code from your authenticator app." },
};
const SIGNED_IN: Reply = ok({ user_id: "u1", email: "owner@acme.test", name: "Nadia" });
const TOKEN = "0123456789abcdef0123456789abcdef.secret-part-of-the-link";
const OWNER_PREVIEW = {
  organization_name: "Acme Ltd",
  partner_name: "Reseller One",
  email: "owner@acme.test",
  expires_at: "2026-12-12T00:00:00Z",
  account_exists: true,
  signed_in_as_invitee: false,
  needs_name: false,
};

interface Driver {
  name: string;
  routes?: Record<string, Reply[]>;
  render: () => void;
  /** Fills the form's email (where it asks) and password, and submits. */
  submitPassword: () => Promise<void>;
  signedIn: () => Promise<void>;
  notSignedIn: () => void;
}

const storeSignedIn = vi.fn();

function fill(emailLabel: string | null, button: string | RegExp) {
  return async () => {
    if (emailLabel) fireEvent.change(await screen.findByLabelText(emailLabel), { target: { value: "owner@acme.test" } });
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "my passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: button }));
  };
}

const DRIVERS: Driver[] = [
  {
    name: "the Admin sign-in",
    render: () => {
      query = `next=${encodeURIComponent("/admin/partners")}`;
      render(
        <PortalsProvider origins={ORIGINS}>
          <LoginScreen portal="admin" />
        </PortalsProvider>,
      );
    },
    submitPassword: fill("Email Address", /Sign In to Admin/),
    signedIn: () => waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/admin/partners")),
    notSignedIn: () => expect(nav.router.replace).not.toHaveBeenCalled(),
  },
  {
    name: "the Partner sign-in",
    render: () =>
      render(
        <PortalsProvider origins={ORIGINS}>
          <LoginScreen portal="partner" />
        </PortalsProvider>,
      ),
    submitPassword: fill("Email Address", /Login to Partner Portal/),
    signedIn: () => waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/partner")),
    notSignedIn: () => expect(nav.router.replace).not.toHaveBeenCalled(),
  },
  {
    name: "the customer (User Admin) sign-in",
    render: () => {
      query = `next=${encodeURIComponent("/o/acme/members")}`;
      render(
        <PortalsProvider origins={ORIGINS}>
          <LoginScreen portal="app" />
        </PortalsProvider>,
      );
    },
    submitPassword: fill("Email Address", /Login to User Admin Panel/),
    signedIn: () => waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/o/acme/members")),
    notSignedIn: () => expect(nav.router.replace).not.toHaveBeenCalled(),
  },
  {
    name: "the product sign-in on Accounts (LoginForm, ?resume=)",
    render: () => {
      query = "resume=abc123";
      vi.stubGlobal("location", { ...window.location, assign: vi.fn(), replace: vi.fn(), href: "http://localhost/login?resume=abc123" });
      render(
        <PortalsProvider origins={ORIGINS}>
          <LoginScreen portal="auth" />
        </PortalsProvider>,
      );
    },
    submitPassword: fill("Email", /^Sign in$/),
    signedIn: () => waitFor(() => expect(window.location.href).toBe("/api/v1/integrations/oauth/resume?resume=abc123")),
    notSignedIn: () => expect(window.location.href).toBe("http://localhost/login?resume=abc123"),
  },
  {
    name: "the Store's own sign-in",
    render: () => render(<StoreSignInForm onSignedIn={storeSignedIn} />),
    submitPassword: fill("Email address", /^Sign in$/),
    signedIn: () => waitFor(() => expect(storeSignedIn).toHaveBeenCalledTimes(1)),
    notSignedIn: () => expect(storeSignedIn).not.toHaveBeenCalled(),
  },
  {
    name: "a customer owner's activation, for an existing account",
    routes: {
      "POST /api/v1/public/owner-invitations/preview": [
        ok(OWNER_PREVIEW),
        ok({ ...OWNER_PREVIEW, signed_in_as_invitee: true }),
      ],
    },
    render: () => {
      window.history.replaceState(null, "", `/owner-invitations/accept#token=${TOKEN}`);
      render(<AcceptOwnerInvitation />);
    },
    submitPassword: fill(null, /^Sign in$/),
    signedIn: async () => {
      expect(await screen.findByRole("button", { name: "Become the owner of Acme Ltd" })).toBeInTheDocument();
    },
    notSignedIn: () => expect(screen.queryByRole("button", { name: "Become the owner of Acme Ltd" })).not.toBeInTheDocument(),
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
  nav.router.replace.mockReset();
  storeSignedIn.mockReset();
  query = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const CODE_LABEL = "6-digit code or a recovery code";
const noSecretInUrls = (calls: Call[]) => {
  for (const c of calls) {
    expect(c.url).not.toContain(CHALLENGE);
    expect(c.url).not.toContain("123456");
  }
};

describe.each(DRIVERS)("$name with two-step verification on", (driver) => {
  it("asks for the code after the password, then continues as a sign-in does", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/login": [NEEDS_CODE],
      "POST /api/v1/auth/login/two-step": [SIGNED_IN],
      ...driver.routes,
    });
    driver.render();
    await driver.submitPassword();
    const code = await screen.findByLabelText(CODE_LABEL);
    expect(screen.getByRole("heading", { name: "Two-step verification" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    driver.notSignedIn();
    fireEvent.change(code, { target: { value: " 123 456 " } });
    fireEvent.click(screen.getByRole("button", { name: /Verify and sign in/ }));
    await driver.signedIn();
    expect(calls.find((c) => c.key === "POST /api/v1/auth/login/two-step")?.body).toEqual({ challenge: CHALLENGE, code: "123456" });
    noSecretInUrls(calls);
  });

  it("a wrong code stays on the code step and says so", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/login": [NEEDS_CODE],
      "POST /api/v1/auth/login/two-step": [{ status: 401, body: { code: "two_step_code_invalid", detail: "That code is not right." } }],
      ...driver.routes,
    });
    driver.render();
    await driver.submitPassword();
    fireEvent.change(await screen.findByLabelText(CODE_LABEL), { target: { value: "999999" } });
    fireEvent.click(screen.getByRole("button", { name: /Verify and sign in/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code isn't right");
    expect(screen.getByLabelText(CODE_LABEL)).toHaveValue("");
    driver.notSignedIn();
  });

  it("an expired challenge goes back to the password step", async () => {
    mockApi({
      "GET /api/v1/auth/session": [SIGNED_OUT],
      "POST /api/v1/auth/login": [NEEDS_CODE],
      "POST /api/v1/auth/login/two-step": [{ status: 401, body: { code: "two_step_challenge_expired" } }],
      ...driver.routes,
    });
    driver.render();
    await driver.submitPassword();
    fireEvent.change(await screen.findByLabelText(CODE_LABEL), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /Verify and sign in/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(EXPIRED_MESSAGE);
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByLabelText(CODE_LABEL)).not.toBeInTheDocument();
    driver.notSignedIn();
  });
});

describe("the shared pieces", () => {
  it("reads a challenge only from a 401 two_step_required", () => {
    expect(twoStepChallenge(new ApiError(401, { code: "two_step_required", challenge: "c" }))).toBe("c");
    expect(twoStepChallenge(new ApiError(401, { code: "invalid_credentials" }))).toBeNull();
    expect(twoStepChallenge(new ApiError(400, { code: "two_step_required", challenge: "c" }))).toBeNull();
    expect(twoStepChallenge(new ApiError(401, { code: "two_step_required" }))).toBeNull();
    expect(twoStepChallenge(new Error("x"))).toBeNull();
    expect(normalizeCode(" ABCDE-12345 ")).toBe("abcde-12345");
    expect(normalizeCode("123 456")).toBe("123456");
  });

  it("a closed account's sign-in page says so", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    query = "closed=1";
    render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    expect(await screen.findByText("Your account is closed.")).toBeInTheDocument();
  });

  it("after confirming the address of a two-step account, the sign-in page says the code comes next", async () => {
    mockApi({ "GET /api/v1/auth/session": [SIGNED_OUT] });
    query = "verified=1";
    render(
      <PortalsProvider origins={ORIGINS}>
        <LoginScreen portal="auth" />
      </PortalsProvider>,
    );
    expect(await screen.findByText(/Your email address is confirmed\./)).toBeInTheDocument();
  });
});
