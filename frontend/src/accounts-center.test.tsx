import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { isPublicPath, routePortalRequest } from "./proxy";
import { pageOwner, type PortalOrigins } from "@/lib/portals";
import { accountsPage } from "@/features/auth/accountsSignIn";
import { CENTER_SECTIONS, MOVED_SECTIONS, centerOrigins } from "@/features/account/centerConfig";
import {
  assignmentLabel,
  connectionChips,
  holdingLabel,
  lastSeenLabel,
  productSignOutMessage,
  sessionLabel,
} from "@/features/account/centerCopy";
import { CenterDevices, CenterLoginSecurity, CenterProfile, CenterSettings } from "@/features/account/AccountsCenter";
import { ConnectedLogins } from "@/features/account/ConnectedLogins";
import { ActiveSessions, LoginHistory } from "@/features/account/CenterSessions";
import { photoScaler } from "@/features/account/ProfileForm";
import { SignInEmailCard } from "@/features/account/SignInEmailCard";
import { describeAccountEvent } from "@/features/console/MySecurityScreen";
import type { AccountProfile, ConnectedProduct } from "@/lib/api/console";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import { UserDetailScreen } from "@/features/controlplane/AdminUsers";
import { AccountSupportScreen } from "@/features/controlplane/AdminAccountSupport";
import { AccountHelpScreen } from "@/features/account/AccountHelp";
import ProductsPage from "@/app/account/products/page";
import OrganizationsPage from "@/app/account/organizations/page";
import ConnectedLoginsPage from "@/app/account/connected-logins/page";
import HistoryPage from "@/app/account/history/page";
import SignInPreferencesPage from "@/app/account/sign-in-preferences/page";

const nav = vi.hoisted(() => ({
  redirect: vi.fn((to: string) => {
    throw new Error(`redirect:${to}`);
  }),
  notFound: vi.fn(() => {
    throw new Error("not-found");
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/users/u2",
  redirect: nav.redirect,
  notFound: nav.notFound,
}));

/**
 * ADR-072 + Accounts V3: the Accounts Control Center on WorkEasy360 Accounts. It exists
 * only while Accounts hosts identity (`W360_ACCOUNTS_IDENTITY`, ADR-071); it has exactly
 * four sections - My Profile, Login & Security, Devices & Sessions, Settings - and the
 * earlier pages redirect into them; every section reads its own person-level endpoint;
 * and nothing it says about a product session claims more than the platform knows.
 */

const BASE = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://accounts.workeasy360.com",
  store: "",
};
const ON: PortalOrigins = { ...BASE, accountsIdentity: true };
const ENV = {
  W360_ADMIN_ORIGIN: BASE.admin,
  W360_PARTNER_ORIGIN: BASE.partner,
  W360_APP_ORIGIN: BASE.app,
  W360_AUTH_ORIGIN: BASE.auth,
};
const ORG_ID = "11111111-1111-4111-8111-111111111111";
const USER_ID = "33333333-3333-4333-8333-333333333333";

function connected(extra: Partial<ConnectedProduct> = {}): ConnectedProduct {
  return {
    product_code: "easy_crm",
    name: "Easy CRM",
    icon: "",
    domain: { host: "crm.workeasy360.com", verified: true },
    subject: USER_ID,
    integration_status: "active",
    sso_connected: true,
    last_sign_in_at: "2026-11-19T09:00:00Z",
    last_activity_at: null,
    session: { platform_sign_in: "open", since: "2026-11-19T08:59:00Z", ended_at: null },
    last_sign_out: null,
    can_sign_out: true,
    organizations: [
      {
        organization_id: ORG_ID,
        name: "Acme Traders",
        slug: "acme",
        member: true,
        access: { state: "active", can_launch: true, entitlement_status: "active", licensing_mode: "per_seat", trial_ends_at: null },
        connection: { first_sign_in_at: "2026-11-01T09:00:00Z", last_sign_in_at: "2026-11-19T09:00:00Z", sign_in_count: 4, last_activity_at: null },
      },
    ],
    ...extra,
  };
}

function profile(extra: Partial<AccountProfile> = {}): AccountProfile {
  return {
    user_id: USER_ID,
    email: "ravi@example.com",
    name: "Ravi",
    email_verified: true,
    created_at: "2026-01-01T00:00:00Z",
    last_sign_in_at: null,
    country: "",
    language: "",
    time_zone: "",
    photo_url: null,
    pending_email: null,
    two_step_enabled: false,
    ...extra,
  };
}

const OPTIONS = { countries: ["IN", "GB", "US"], languages: ["en", "hi"], time_zones: ["Asia/Kolkata", "Europe/London"] };
const SESSION = { authenticated: true, user_id: USER_ID, email: "ravi@example.com", name: "Ravi Kumar" };
const PENDING = { new_email: "ravi.new@example.com", requested_at: "2026-11-19T09:00:00Z", expires_at: "2026-11-20T09:00:00Z" };
const TWO_STEP_OFF = { available: true, enabled: false, method: null, enabled_at: null, recovery_codes_remaining: 0 };
const PREFS = { default: "ask_once", default_is_set: false, default_choices: ["ask_once", "auto_sso"], products: [] };

let assign: ReturnType<typeof vi.fn>;
beforeEach(() => {
  assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign, replace: vi.fn() });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("routing", () => {
  it("exists only while Accounts hosts identity", () => {
    expect(centerOrigins(ENV)).toBeNull();
    expect(centerOrigins({ ...ENV, W360_ACCOUNTS_IDENTITY: "off" })).toBeNull();
    expect(centerOrigins({ ...ENV, W360_ACCOUNTS_IDENTITY: "on" })).toEqual({ appOrigin: BASE.app });
  });

  it("has exactly four sections, My Profile first; each is an Accounts page that signs in on Accounts", () => {
    expect(CENTER_SECTIONS.map((s) => [s.label, s.href])).toEqual([
      ["My Profile", "/account"],
      ["Login & Security", "/account/security"],
      ["Devices & Sessions", "/account/sessions"],
      ["Settings", "/account/settings"],
    ]);
    for (const { href } of CENTER_SECTIONS) {
      expect(pageOwner(href, ON), href).toBe("auth");
      expect(accountsPage(href), href).toBe(href);
      const signedOut = routePortalRequest({ origins: ON, host: "accounts.workeasy360.com", pathname: href, search: "", hasSession: false });
      expect(signedOut).toEqual({ kind: "redirect", location: `${BASE.auth}/login?next=${encodeURIComponent(href)}` });
      // On the customer portal it opens on Accounts.
      const elsewhere = routePortalRequest({ origins: ON, host: "app.workeasy360.com", pathname: href, search: "", hasSession: true });
      expect(elsewhere).toEqual({ kind: "redirect", location: `${BASE.auth}${href}` });
    }
  });

  it("the earlier pages redirect to their new home while the Control Center is on", () => {
    for (const [key, value] of Object.entries({ ...ENV, W360_ACCOUNTS_IDENTITY: "on" })) vi.stubEnv(key, value);
    const pages: [string, () => unknown, string][] = [
      ["/account/products", ProductsPage, "/account/settings"],
      ["/account/organizations", OrganizationsPage, "/account/settings"],
      ["/account/connected-logins", ConnectedLoginsPage, "/account/sessions"],
      ["/account/history", HistoryPage, "/account/sessions"],
      ["/account/sign-in-preferences", SignInPreferencesPage, "/account/security"],
    ];
    for (const [path, page, home] of pages) {
      expect(MOVED_SECTIONS[path]).toBe(home);
      expect(page, path).toThrow(`redirect:${home}`);
    }
  });

  it("off, the moved pages stay not found (sign-in preferences stays ADR-071's page)", () => {
    for (const [key, value] of Object.entries(ENV)) vi.stubEnv(key, value);
    vi.stubEnv("W360_ACCOUNTS_IDENTITY", "");
    for (const page of [ProductsPage, OrganizationsPage, ConnectedLoginsPage, HistoryPage]) expect(page).toThrow("not-found");
    expect(() => SignInPreferencesPage()).not.toThrow();
  });
});

describe("the words", () => {
  it("never calls a product session active, whatever is recorded", () => {
    for (const state of ["open", "ended", "untracked", "none"] as const) {
      for (const sso of [true, false]) {
        for (const status of [null, "delivered", "pending", "failed"] as const) {
          const product = connected({
            sso_connected: sso,
            session: { platform_sign_in: state, since: null, ended_at: null },
            last_sign_out: status ? { status, requested_at: "2026-11-19T09:00:00Z", finished_at: null, by_this_product_sign_out: true } : null,
          });
          const said = [sessionLabel(product).label, sessionLabel(product).detail, ...connectionChips(product).map((c) => c.label)];
          for (const text of said) expect(text, text).not.toMatch(/\bactive\b/i);
        }
      }
    }
  });

  it("says last seen or unknown, never invents a time", () => {
    expect(lastSeenLabel(null)).toBe("Last seen: unknown");
    expect(lastSeenLabel("2026-11-19T09:00:00Z", Date.parse("2026-11-19T09:05:00Z"))).toBe("Last seen 5 min ago");
  });

  it("tells purchased, assigned, connected and signed out apart", () => {
    expect(holdingLabel("active")?.label).toBe("Purchased");
    expect(holdingLabel("trial")?.label).toBe("Trial");
    expect(holdingLabel(null)).toBeNull();
    expect(assignmentLabel({ state: "active", licensing_mode: "per_seat" }).label).toBe("Seat assigned to you");
    expect(assignmentLabel({ state: "active", licensing_mode: "per_organization" }).label).toBe("Included for every member");
    expect(assignmentLabel({ state: "no_seat", licensing_mode: "per_seat" }).label).toBe("Not assigned to you");
    expect(connectionChips(connected()).map((c) => c.label)).toEqual([
      "Purchased",
      "Assigned to you",
      "SSO connection established",
      expect.stringMatching(/^Last signed in /),
      "WorkEasy360 sign-in still open",
    ]);
    expect(productSignOutMessage("Easy CRM", "pending")).toMatch(/has not confirmed yet/);
    expect(describeAccountEvent({ kind: "product_sign_in", reason: "easy_crm" })).toBe("Signed in to a product with WorkEasy360");
  });
});

describe("the sections", () => {
  it("Connected Logins: what is known, and signing out of one product", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/connections": [ok({ subject: USER_ID, products: [connected()] })],
      "POST /api/v1/auth/account/connections/easy_crm/sign-out": [ok({ product_code: "easy_crm", status: "pending" })],
    });
    const { container } = render(<ConnectedLogins />);
    const card = await screen.findByRole("region", { name: "Easy CRM" });
    expect(within(card).getByText("Verified domain")).toBeInTheDocument();
    expect(within(card).getAllByText("WorkEasy360 sign-in still open").length).toBeGreaterThan(0);
    expect(within(card).getAllByText("Last seen: unknown").length).toBeGreaterThan(0);
    expect(within(card).getByText("SSO connection established")).toBeInTheDocument();
    expect(within(card).getAllByText(USER_ID).length).toBeGreaterThan(0);
    expect(card.textContent).not.toMatch(/\bActive\b/);
    await expectNoViolations(container);
    fireEvent.click(within(card).getByRole("button", { name: "Sign out of this product" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sign out of Easy CRM" }));
    expect(await screen.findByText(/has been told to sign you out and has not confirmed yet/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/auth/account/connections/easy_crm/sign-out")).toBe(true);
  });

  it("Active Sessions: one row per sign-in; signing one out ends that sign-in", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/sessions": [
        ok({
          sessions: [
            { session_id: "here", current: true, signed_in_at: "2026-11-19T08:00:00Z", last_active_at: "2026-11-19T09:00:00Z", expires_at: "2026-12-19T08:00:00Z", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", portal_sessions: 2 },
            { session_id: "phone", current: false, signed_in_at: "2026-11-18T08:00:00Z", last_active_at: "2026-11-18T09:00:00Z", expires_at: "2026-12-18T08:00:00Z", ip_prefix: "198.51.100.0/24", device_label: "Safari on iPhone", portal_sessions: 1 },
          ],
        }),
      ],
      "POST /api/v1/auth/sessions/phone/revoke": [ok({ ok: true, ended: 1 })],
    });
    const { container } = render(<ActiveSessions />);
    expect(await screen.findByText("Safari on iPhone")).toBeInTheDocument();
    expect(screen.getByText("This device")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3); // header + one per sign-in
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: /Sign out Safari on iPhone/ }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /api/v1/auth/sessions/phone/revoke")).toBe(true));
  });

  it("Login History: product names, and pages past the first", async () => {
    const row = (i: number, extra = {}) => ({
      occurred_at: `2026-11-19T09:${String(59 - i).padStart(2, "0")}:00Z`,
      kind: "sign_in",
      reason: "",
      ip_prefix: "203.0.113.0/24",
      device_label: "Chrome on Windows",
      product_name: null,
      ...extra,
    });
    const calls = mockApi({
      "GET /api/v1/auth/account/history": [
        ok({ results: [row(0, { kind: "product_sign_in", reason: "easy_crm", product_name: "Easy CRM" }), row(1, { kind: "sign_in_failed", reason: "invalid_credentials" })], next_cursor: "next", has_more: true }),
        ok({ results: [row(2)], next_cursor: null, has_more: false }),
      ],
    });
    const { container } = render(<LoginHistory />);
    expect(await screen.findByText("Easy CRM")).toBeInTheDocument();
    expect(screen.getByText("Failed sign-in attempt (wrong password)")).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByText("Signed in")).toHaveLength(1));
    expect(calls[1].url).toContain("cursor=next");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

});

describe("My Profile", () => {
  it("opens first: photo or initials, name, verified email and WorkEasy360 ID; four menus only; Help & Support; passes axe", async () => {
    mockApi({ "GET /api/v1/auth/account/profile": [ok(profile({ name: "Ravi Kumar" }))] });
    const { container } = render(<CenterProfile />);
    expect(screen.getByRole("heading", { level: 1, name: "My Profile" })).toBeInTheDocument();
    expect(await screen.findByText(USER_ID)).toBeInTheDocument();
    const header = screen.getByRole("region", { name: "Your WorkEasy360 account" });
    expect(within(header).getByText("Ravi Kumar")).toBeInTheDocument();
    expect(within(header).getByText("ravi@example.com")).toBeInTheDocument();
    expect(within(header).getByText("Verified")).toBeInTheDocument();
    expect(within(header).getByText("RK")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Your account" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["My Profile", "Login & Security", "Devices & Sessions", "Settings"]);
    expect(within(nav).getByRole("link", { name: "My Profile" })).toHaveAttribute("aria-current", "page");
    expect(container.textContent).not.toMatch(/My Products|Connected Logins|Wallet|Product Catalog/);
    expect(screen.getByRole("link", { name: "Help & Support" })).toHaveAttribute("href", "/help/account");
    expect(screen.getByRole("button", { name: "Switch account" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows an unverified email and a pending change as such, and the photo when there is one", async () => {
    mockApi({
      "GET /api/v1/auth/account/profile": [
        ok(profile({ email_verified: false, pending_email: PENDING, photo_url: "/api/v1/public/profile-photos/tok-1" })),
      ],
    });
    render(<CenterProfile />);
    const header = await screen.findByRole("region", { name: "Your WorkEasy360 account" });
    expect(await within(header).findByText("Not verified")).toBeInTheDocument();
    expect(within(header).getByText("Pending verification")).toBeInTheDocument();
    expect(header.querySelector("img")).toHaveAttribute("src", "/api/v1/public/profile-photos/tok-1");
  });

  it("edits in place: Cancel restores the saved values and sends nothing; Save sends only what changed", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/profile": [ok(profile())],
      "GET /api/v1/auth/account/profile/options": [ok(OPTIONS)],
      "PATCH /api/v1/auth/account/profile": [ok(profile({ name: "Ravi Kumar", country: "IN", language: "hi", time_zone: "Asia/Kolkata" }))],
    });
    const { container } = render(<CenterProfile />);
    const card = await screen.findByRole("region", { name: "Profile" });
    fireEvent.click(await within(card).findByRole("button", { name: "Edit" }));
    fireEvent.change(await within(card).findByLabelText("Full name"), { target: { value: "Someone Else" } });
    fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(within(card).queryByLabelText("Full name")).not.toBeInTheDocument();
    expect(within(card).getByText("Ravi")).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    expect(await within(card).findByLabelText("Full name")).toHaveValue("Ravi");
    // Names come from the browser for the server's codes.
    expect(within(card).getByRole("option", { name: "India" })).toBeInTheDocument();
    expect(within(card).getByRole("option", { name: "Hindi" })).toBeInTheDocument();
    await expectNoViolations(container);
    expect(calls.some((c) => c.key === "PATCH /api/v1/auth/account/profile")).toBe(false);

    fireEvent.change(within(card).getByLabelText("Full name"), { target: { value: "  Ravi   Kumar " } });
    fireEvent.change(within(card).getByLabelText("Country"), { target: { value: "IN" } });
    fireEvent.change(within(card).getByLabelText("Language"), { target: { value: "hi" } });
    fireEvent.change(within(card).getByLabelText("Time zone"), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("Saved. Products show your new details the next time you open them.");
    expect(calls.filter((c) => c.key === "PATCH /api/v1/auth/account/profile").map((c) => c.body)).toEqual([
      { full_name: "Ravi Kumar", country: "IN", language: "hi", time_zone: "Asia/Kolkata" },
    ]);
    expect(within(card).getByText("India")).toBeInTheDocument();
    expect(within(card).getByText("Hindi")).toBeInTheDocument();
    expect(within(card).getByText("Asia / Kolkata")).toBeInTheDocument();
    // The header follows what was saved.
    expect(screen.getByRole("region", { name: "Your WorkEasy360 account" })).toHaveTextContent("Ravi Kumar");
  });

  it("says what the server refused, marks the field and stays in edit mode", async () => {
    mockApi({
      "GET /api/v1/auth/account/profile": [ok(profile())],
      "GET /api/v1/auth/account/profile/options": [ok(OPTIONS)],
      "PATCH /api/v1/auth/account/profile": [
        { status: 400, body: { code: "profile_invalid", field: "time_zone", detail: "Choose a time zone from the list." } },
      ],
    });
    render(<CenterProfile />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(await screen.findByLabelText("Time zone"), { target: { value: "Europe/London" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a time zone from the list.");
    expect(screen.getByLabelText("Time zone")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Full name")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("scales the photo in the browser and sends the picture itself as the body; Remove photo deletes it", async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: "image/webp" });
    const scale = vi.spyOn(photoScaler, "scale").mockResolvedValue({ blob, preview: "data:image/webp;base64,AQID" });
    const withPhoto = profile({ photo_url: "/api/v1/public/profile-photos/tok-1" });
    const calls = mockApi({
      "GET /api/v1/auth/account/profile": [ok(profile())],
      "GET /api/v1/auth/account/profile/options": [ok(OPTIONS)],
      "PUT /api/v1/auth/account/profile/photo": [ok(withPhoto)],
      "DELETE /api/v1/auth/account/profile/photo": [ok(profile())],
    });
    render(<CenterProfile />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const file = new File(["raw camera bytes"], "me.jpg", { type: "image/jpeg" });
    fireEvent.change(await screen.findByLabelText("Choose a photo"), { target: { files: [file] } });
    expect(await screen.findByText(/New photo chosen/)).toBeInTheDocument();
    expect(scale).toHaveBeenCalledWith(file);
    // Nothing is sent before Save.
    expect(calls.some((c) => c.key.startsWith("PUT "))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    const put = (global.fetch as Mock).mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(put?.[0]).toBe("/api/v1/auth/account/profile/photo");
    expect((put?.[1] as RequestInit).body).toBe(blob);
    expect(calls.find((c) => c.key === "PUT /api/v1/auth/account/profile/photo")?.headers["content-type"]).toBe("image/webp");
    expect(calls.some((c) => c.key === "PATCH /api/v1/auth/account/profile")).toBe(false);
    expect(screen.getByRole("region", { name: "Your WorkEasy360 account" }).querySelector("img")).toHaveAttribute("src", withPhoto.photo_url);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove photo" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.key === "DELETE /api/v1/auth/account/profile/photo")).toBe(true));
    expect(await screen.findByText("None")).toBeInTheDocument();
  });
});

describe("Login & Security", () => {
  it("the sign-in email with its pending change (Resend, Cancel), no mobile sign-in, password, two-step and product sign-in", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/account/profile": [ok(profile({ pending_email: PENDING }))],
      "GET /api/v1/auth/email/pending": [ok({ pending: PENDING }), ok({ pending: PENDING }), ok({ pending: null })],
      "POST /api/v1/auth/email/pending/resend": [{ status: 202, body: { pending: PENDING } }],
      "DELETE /api/v1/auth/email/pending": [ok({ pending: null })],
      "GET /api/v1/auth/two-step": [ok(TWO_STEP_OFF)],
      "GET /api/v1/auth/sign-in-preferences": [ok(PREFS)],
    });
    const { container } = render(<CenterLoginSecurity />);
    expect(screen.getByRole("heading", { level: 1, name: "Login & Security" })).toBeInTheDocument();
    const email = await screen.findByRole("region", { name: "Sign-in email" });
    expect(await within(email).findByText("ravi.new@example.com")).toBeInTheDocument();
    expect(within(email).getByText("Verified")).toBeInTheDocument();
    expect(within(email).getByText("Pending verification")).toBeInTheDocument();
    expect(within(email).getByText("Sign-in with a mobile number is not available.")).toBeInTheDocument();
    expect(container.querySelector('input[type="tel"]')).toBeNull();
    expect(screen.getByRole("link", { name: "Reset it by email" })).toHaveAttribute("href", "/forgot-password");
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Set up two-step verification" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Product sign-in preferences" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(within(screen.getByRole("navigation", { name: "Your account" })).getByRole("link", { name: "Login & Security" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expectNoViolations(container);

    fireEvent.click(within(email).getByRole("button", { name: "Resend link" }));
    expect(await within(email).findByRole("status")).toHaveTextContent("Sent again to ravi.new@example.com");
    fireEvent.click(within(email).getByRole("button", { name: "Cancel change" }));
    expect(await within(email).findByText(/Change cancelled\. You keep signing in with ravi@example.com/)).toBeInTheDocument();
    await waitFor(() => expect(within(email).queryByText("Pending verification")).not.toBeInTheDocument());
    expect(calls.filter((c) => c.key === "POST /api/v1/auth/email/pending/resend")).toHaveLength(1);
    expect(calls.filter((c) => c.key === "DELETE /api/v1/auth/email/pending")).toHaveLength(1);
  });

  it("a resend too soon says to wait", async () => {
    mockApi({
      "GET /api/v1/auth/email/pending": [ok({ pending: PENDING })],
      "POST /api/v1/auth/email/pending/resend": [{ status: 429, body: { code: "throttled" } }],
    });
    render(<SignInEmailCard email="ravi@example.com" verified />);
    fireEvent.click(await screen.findByRole("button", { name: "Resend link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A link was sent a moment ago");
  });
});

describe("Devices & Sessions", () => {
  it("one page: where you're signed in, the products you're signed in to, and login history", async () => {
    mockApi({
      "GET /api/v1/auth/account/sessions": [
        ok({
          sessions: [
            { session_id: "here", current: true, signed_in_at: "2026-11-19T08:00:00Z", last_active_at: "2026-11-19T09:00:00Z", expires_at: "2026-12-19T08:00:00Z", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", portal_sessions: 1 },
          ],
        }),
      ],
      "GET /api/v1/auth/account/connections": [ok({ subject: USER_ID, products: [connected()] })],
      "GET /api/v1/auth/account/history": [ok({ results: [], next_cursor: null, has_more: false })],
    });
    const { container } = render(<CenterDevices />);
    expect(screen.getByRole("heading", { level: 1, name: "Devices & Sessions" })).toBeInTheDocument();
    expect(await screen.findByText("This device")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Products you're signed in to" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Sign out of this product" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Login History" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out everywhere" })).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Your account" })).getByRole("link", { name: "Devices & Sessions" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expectNoViolations(container);
  });
});

const NOTIFICATIONS = [
  { key: "security", label: "Security notices", description: "New sign-ins, password and email changes.", enabled: true, required: true },
  { key: "product_news", label: "Product news", description: "New features in the products you use.", enabled: false, required: false },
];

function settingsRoutes(closure: unknown, extra: Record<string, Reply[]> = {}): Record<string, Reply[]> {
  return {
    "GET /api/v1/auth/account/notifications": [ok({ preferences: NOTIFICATIONS })],
    "GET /api/v1/auth/activity": [
      ok({ events: [{ occurred_at: "2026-11-19T09:00:00Z", kind: "two_step_enabled", reason: "", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows" }] }),
    ],
    "GET /api/v1/auth/account/organizations": [
      ok({
        organizations: [
          { organization_id: ORG_ID, name: "Acme Traders", slug: "acme", status: "active", is_owner: true, roles: ["owner"], can_view_billing: true },
          { organization_id: "o2", name: "Beta Labs", slug: "beta", status: "active", is_owner: false, roles: ["member"], can_view_billing: false },
        ],
      }),
    ],
    "GET /api/v1/auth/account/closure": [ok(closure)],
    ...extra,
  };
}

describe("Settings", () => {
  it("notifications (security notices locked on), security activity, links to User Admin, and a closure blocked by sole ownership", async () => {
    const calls = mockApi(
      settingsRoutes(
        { can_close: false, blocking_organizations: [{ name: "Acme Traders", slug: "acme" }], two_step_required: false },
        {
          "PATCH /api/v1/auth/account/notifications": [ok({ preferences: [NOTIFICATIONS[0], { ...NOTIFICATIONS[1], enabled: true }] })],
        },
      ),
    );
    const { container } = render(<CenterSettings appOrigin={BASE.app} />);
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    const security = await screen.findByRole("switch", { name: "Security notices" });
    expect(security).toBeChecked();
    expect(security).toBeDisabled();
    expect(screen.getByText("Always sent")).toBeInTheDocument();
    expect(await screen.findByText("Two-step verification turned on")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Open Acme Traders in User Admin" })).toHaveAttribute("href", `${BASE.app}/o/acme`);
    expect(screen.getByRole("link", { name: "Subscriptions and billing of Acme Traders" })).toHaveAttribute("href", `${BASE.app}/o/acme/settings/billing`);
    expect(screen.getByRole("link", { name: "Open Beta Labs in User Admin" })).toHaveAttribute("href", `${BASE.app}/o/beta`);
    expect(screen.queryByRole("link", { name: "Subscriptions and billing of Beta Labs" })).not.toBeInTheDocument();
    expect(await screen.findByText("You are the only owner of an organization")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Acme Traders" })).toHaveAttribute("href", `${BASE.app}/o/acme/settings/members`);
    expect(screen.queryByRole("button", { name: "Close my account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Get help with your account" })).toHaveAttribute("href", "/help/account");
    await expectNoViolations(container);

    const news = screen.getByRole("switch", { name: "Product news" });
    expect(news).not.toBeChecked();
    fireEvent.click(news);
    expect(await screen.findByText("Saved. Product news: on.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Product news" })).toBeChecked();
    expect(calls.find((c) => c.key === "PATCH /api/v1/auth/account/notifications")?.body).toEqual({ product_news: true });
  });

  it("a refused notification change says so and keeps the saved choice", async () => {
    mockApi(
      settingsRoutes(
        { can_close: true, blocking_organizations: [], two_step_required: false },
        { "PATCH /api/v1/auth/account/notifications": [{ status: 400, body: { code: "preference_invalid", detail: "That setting cannot be changed." } }] },
      ),
    );
    render(<CenterSettings appOrigin={BASE.app} />);
    fireEvent.click(await screen.findByRole("switch", { name: "Product news" }));
    expect(await screen.findByText(/Not saved\. That setting cannot be changed\./)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Product news" })).not.toBeChecked();
  });

  it("closing the account asks for the password, the code and CLOSE, then goes to the sign-in page", async () => {
    const calls = mockApi(
      settingsRoutes(
        { can_close: true, blocking_organizations: [], two_step_required: true },
        {
          "POST /api/v1/auth/account/closure": [
            { status: 400, body: { code: "password_invalid", field: "password" } },
            ok({ closed: true }),
          ],
        },
      ),
    );
    render(<CenterSettings appOrigin={BASE.app} />);
    fireEvent.click(await screen.findByRole("button", { name: "Close my account" }));
    const close = screen.getByRole("button", { name: "Close account permanently" });
    expect(close).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.change(screen.getByLabelText("6-digit code or a recovery code"), { target: { value: "123 456" } });
    fireEvent.change(screen.getByLabelText("Type CLOSE to confirm"), { target: { value: "close" } });
    expect(close).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Type CLOSE to confirm"), { target: { value: "CLOSE" } });
    fireEvent.click(close);
    expect(await screen.findByRole("alert")).toHaveTextContent("The password is not correct.");
    expect(assign).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
    fireEvent.click(screen.getByRole("button", { name: "Close account permanently" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?closed=1"));
    expect(calls.filter((c) => c.key === "POST /api/v1/auth/account/closure").map((c) => c.body)).toEqual([
      { password: "wrong", code: "123456", confirmation: "CLOSE" }, // pragma: allowlist secret
      { password: "right", code: "123456", confirmation: "CLOSE" }, // pragma: allowlist secret
    ]);
  });

  it("a closure refused for sole ownership lists the organizations to resolve first", async () => {
    mockApi(
      settingsRoutes(
        { can_close: true, blocking_organizations: [], two_step_required: false },
        { "POST /api/v1/auth/account/closure": [{ status: 409, body: { code: "sole_owner", blocking_organizations: [{ name: "Acme Traders", slug: "acme" }] } }] },
      ),
    );
    render(<CenterSettings appOrigin={BASE.app} />);
    fireEvent.click(await screen.findByRole("button", { name: "Close my account" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.change(screen.getByLabelText("Type CLOSE to confirm"), { target: { value: "CLOSE" } });
    fireEvent.click(screen.getByRole("button", { name: "Close account permanently" }));
    expect(await screen.findByText("You are the only owner of an organization")).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("Help & Support", () => {
  it("anyone can ask account support and gets a reference; it says a request proves nothing and partners never see it", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/account-support": [{ status: 202, body: { reference: "AS-ABCD1234" } }],
    });
    const { container } = render(<AccountHelpScreen />);
    expect(screen.getByRole("heading", { level: 1, name: "Help with your account" })).toBeInTheDocument();
    expect(screen.getByText(/Partners and organization administrators do not see it/)).toBeInTheDocument();
    expect(screen.getByText(/does not prove who owns an account by itself/)).toBeInTheDocument();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the email address of the account.");
    expect(calls.some((c) => c.key === "POST /api/v1/public/account-support")).toBe(false);
    fireEvent.change(screen.getByLabelText("Email address of the account"), { target: { value: " lost@example.com " } });
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Lost Person" } });
    fireEvent.change(screen.getByLabelText("What do you need help with?"), { target: { value: "lost_two_step" } });
    fireEvent.change(screen.getByLabelText("What happened?"), { target: { value: "My phone was stolen." } });
    fireEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByText("AS-ABCD1234")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/public/account-support")?.body).toEqual({
      email: "lost@example.com",
      name: "Lost Person",
      topic: "lost_two_step",
      message: "My phone was stolen.",
    });
  });

  it("fills in a signed-in person's own details and is open signed out", async () => {
    mockApi({ "GET /api/v1/auth/session": [ok(SESSION)] });
    render(<AccountHelpScreen />);
    expect(await screen.findByDisplayValue("ravi@example.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Ravi Kumar")).toBeInTheDocument();
    expect(isPublicPath("/help/account")).toBe(true);
  });
});

describe("the Super Admin's view of a person", () => {
  const T = "2026-11-19T09:00:00Z";
  const detail = {
    user_id: "u2",
    email: "u2@example.com",
    name: "Person Two",
    status: "active",
    is_staff: false,
    locked: false,
    organizations: 1,
    email_verified: true,
    acquisition_source: "self_signup",
    created_at: T,
    last_sign_in_at: T,
    lifecycle: "customer",
    orders: [],
    memberships: [
      { organization_id: ORG_ID, slug: "acme", name: "Acme Ltd", is_owner: true, access_class: "customer", subscription_status: "active", products: ["easy_crm"], seats: ["easy_crm"] },
    ],
    sessions: [],
    activity: [],
    central_user_id: "u2",
    connections: [
      {
        product_code: "easy_crm",
        name: "Easy CRM",
        domain: { host: "crm.workeasy360.com", verified: true },
        takes_backchannel_logout: true,
        platform_sign_in: "open",
        platform_sign_in_since: T,
        platform_sign_in_ended_at: null,
        last_sign_out: null,
        organizations: [{ organization_id: ORG_ID, name: "Acme Ltd", first_sign_in_at: T, last_sign_in_at: T, sign_in_count: 3, last_activity_at: null }],
      },
    ],
    sign_in_history: [{ occurred_at: T, kind: "product_sign_in", reason: "easy_crm", ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", product_name: "Easy CRM" }],
    sign_ins: [{ session_id: "s1", signed_in_at: T, last_active_at: T, expires_at: T, ip_prefix: "203.0.113.0/24", device_label: "Chrome on Windows", portal_sessions: 2 }],
  };

  it("maps the person to organizations, seats, product sign-ins, history and devices", async () => {
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "s@example.com" })],
      "GET /api/v1/admin/users/u2": [ok(detail)],
    });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_user.view"]}>
        <main>
          <UserDetailScreen userId="u2" />
        </main>
      </AdminContextForTest>,
    );
    await screen.findAllByText("Acme Ltd");
    expect(screen.getAllByText(/Seats: /)[0]).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Product sign-ins/ }));
    const table = screen.getByRole("table", { name: "Product sign-ins with WorkEasy360, per organization" });
    expect(table).toHaveTextContent("Easy CRM");
    expect(table).toHaveTextContent("WorkEasy360 sign-in still open");
    expect(table).toHaveTextContent("Unknown");
    expect(table.textContent).not.toMatch(/\bActive\b/);
    fireEvent.click(screen.getByRole("tab", { name: /Sessions/ }));
    expect(screen.getByRole("table", { name: "Signed-in devices, one row per sign-in" })).toHaveTextContent("Chrome on Windows");
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("table", { name: "Sign-in history: WorkEasy360 and product sign-ins" })).toHaveTextContent("Easy CRM");
    await expectNoViolations(container);
  });

  it("Account support: lists open requests and resolves one with a note", async () => {
    const CASE = {
      id: "c1",
      reference: "AS-12345678",
      email: "lost@example.com",
      name: "Lost Person",
      topic: "lost_two_step",
      message: "I lost my phone.",
      status: "open",
      created_at: T,
      resolved_at: null,
      note: "",
    };
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true, user_id: "u1", email: "s@example.com" })],
      "GET /api/v1/admin/account-support": [ok({ results: [CASE] }), ok({ results: [] })],
      "PATCH /api/v1/admin/account-support/c1": [ok({ ...CASE, status: "resolved", note: "Confirmed through the sign-in email." })],
    });
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_user.view", "platform.staff_user.manage"]}>
        <main>
          <AccountSupportScreen />
        </main>
      </AdminContextForTest>,
    );
    expect(await screen.findByText("AS-12345678")).toBeInTheDocument();
    expect(screen.getByText("Lost two-step verification")).toBeInTheDocument();
    expect(calls[calls.length - 1].url).toContain("status=open");
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Resolve AS-12345678" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("What was done"), { target: { value: "Confirmed through the sign-in email." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(calls.find((c) => c.key === "PATCH /api/v1/admin/account-support/c1")?.body).toEqual({
        status: "resolved",
        note: "Confirmed through the sign-in email.",
      }),
    );
    expect(await screen.findByText("No open requests.")).toBeInTheDocument();
  });

  it("Account support: a viewer without the manage grant sees no Resolve", async () => {
    mockApi({
      "GET /api/v1/admin/account-support": [
        ok({ results: [{ id: "c1", reference: "AS-1", email: "a@example.com", name: "", topic: "other", message: "Help", status: "open", created_at: T, resolved_at: null, note: "" }] }),
      ],
    });
    render(
      <AdminContextForTest permissions={["platform.staff_user.view"]}>
        <AccountSupportScreen />
      </AdminContextForTest>,
    );
    expect(await screen.findByText("AS-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Resolve AS/ })).not.toBeInTheDocument();
  });
});
