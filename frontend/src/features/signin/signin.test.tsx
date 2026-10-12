import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expectNoViolations, mockApi, ok, type Reply } from "@/features/controlplane/testSupport";
import { ProductAccessChooser } from "./ProductAccessChooser";
import { SignOutConfirm } from "./SignOutConfirm";
import { SignInPreferencesScreen } from "./SignInPreferencesScreen";
import { describePreferenceChange, overrideLabel, whatHappens } from "./signInCopy";
import type { PendingAccess, SignInPreferences } from "@/lib/api/signIn";

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  useParams: () => ({ orgSlug: "acme" }),
  usePathname: () => "/product-access",
}));

const assign = vi.fn();
beforeEach(() => {
  assign.mockReset();
  vi.stubGlobal("location", { ...window.location, assign, reload: vi.fn() });
});
afterEach(() => {
  vi.unstubAllGlobals();
  query = "";
});

const SESSION: Reply = ok({ authenticated: true, user_id: "u1", email: "ana@example.com" });

function pending(overrides: Partial<PendingAccess> = {}): PendingAccess {
  return {
    product: { code: "crm", name: "Easy CRM", icon: "customers" },
    organization: { name: "ABC Ltd." },
    account: { email: "ana@example.com", name: "Ana" },
    choices: { workeasy360: true, product: true },
    remembered: "inherit",
    ...overrides,
  };
}

describe("the access chooser", () => {
  it("names the product, the signed-in account and the organization, and offers both ways in", async () => {
    query = "resume=tok123";
    mockApi({ "GET /api/v1/auth/session": [SESSION], "GET /api/v1/integrations/oauth/pending": [ok(pending())] });
    const { container } = render(<ProductAccessChooser />);
    expect(await screen.findByRole("heading", { level: 1, name: "How would you like to access Easy CRM?" })).toBeInTheDocument();
    expect(screen.getByText("ana@example.com")).toBeInTheDocument();
    expect(screen.getByText("ABC Ltd.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue with Workeasy360/ })).toHaveAccessibleDescription("Use your existing Workeasy360 account.");
    expect(screen.getByRole("button", { name: /Use Easy CRM sign-in/ })).toHaveAccessibleDescription(
      "Use the product's separate sign-in page.",
    );
    expect(screen.getByRole("checkbox", { name: "Remember my choice for Easy CRM" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Use another Workeasy360 account" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("continues with Workeasy360, remembering the choice, and goes where the server says", async () => {
    query = "resume=tok123";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/pending": [ok(pending())],
      "POST /api/v1/integrations/oauth/pending/choose": [ok({ redirect_url: "/api/v1/integrations/oauth/resume?resume=tok123" })],
    });
    render(<ProductAccessChooser />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Remember my choice for Easy CRM" }));
    fireEvent.click(screen.getByRole("button", { name: /Continue with Workeasy360/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/api/v1/integrations/oauth/resume?resume=tok123"));
    const answer = calls.find((c) => c.key === "POST /api/v1/integrations/oauth/pending/choose");
    expect(answer?.body).toEqual({ resume: "tok123", choice: "workeasy360", remember: true });
    // The page asked for the CSRF cookie first.
    expect(calls[0].key).toBe("GET /api/v1/auth/session");
  });

  it("sends a person who prefers the product's sign-in to the page the server names", async () => {
    query = "resume=tok123";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/pending": [ok(pending())],
      "POST /api/v1/integrations/oauth/pending/choose": [ok({ redirect_url: "https://crm.example.com/login" })],
    });
    render(<ProductAccessChooser />);
    fireEvent.click(await screen.findByRole("button", { name: /Use Easy CRM sign-in/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://crm.example.com/login"));
    expect(calls.at(-1)?.body).toEqual({ resume: "tok123", choice: "product", remember: false });
  });

  it("offers only Workeasy360 for a product with no sign-in of its own, in the first-time wording", async () => {
    query = "resume=tok123";
    mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/pending": [ok(pending({ product: { code: "book", name: "EasyBook", icon: "" }, choices: { workeasy360: true, product: false } }))],
    });
    render(<ProductAccessChooser />);
    expect(await screen.findByRole("heading", { level: 1, name: "Continue to EasyBook" })).toBeInTheDocument();
    expect(screen.getByText("Use your Workeasy360 account to access EasyBook.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue to EasyBook/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /EasyBook sign-in/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Automatically use Workeasy360 next time" })).toBeInTheDocument();
  });

  it("switches account and cancels through the server, never by itself", async () => {
    query = "resume=tok123";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/pending": [ok(pending())],
      "POST /api/v1/integrations/oauth/pending/choose": [
        ok({ redirect_url: "/login?resume=fresh" }),
        ok({ redirect_url: "https://crm.example.com/cb?error=access_denied" }),
      ],
    });
    render(<ProductAccessChooser />);
    fireEvent.click(await screen.findByRole("button", { name: "Use another Workeasy360 account" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?resume=fresh"));
    expect(calls.at(-1)?.body).toMatchObject({ choice: "switch_account" });
  });

  it("says an expired or spent link is expired, and sends a signed-out person to sign in", async () => {
    query = "resume=gone";
    mockApi({ "GET /api/v1/auth/session": [SESSION], "GET /api/v1/integrations/oauth/pending": [{ status: 404, body: { code: "pending_invalid" } }] });
    const { unmount } = render(<ProductAccessChooser />);
    expect(await screen.findByText("This sign-in link has expired or was already used.")).toBeInTheDocument();
    unmount();

    query = "resume=tok123";
    mockApi({ "GET /api/v1/auth/session": [SESSION], "GET /api/v1/integrations/oauth/pending": [{ status: 401, body: { code: "login_required" } }] });
    render(<ProductAccessChooser />);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login?resume=tok123"));
  });
});

describe("the sign-out confirmation", () => {
  it("asks before signing out of Workeasy360, then returns to the product", async () => {
    query = "request=sealed";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/logout/request": [ok({ product: { name: "Easy CRM" }, signed_in: true, account: { email: "ana@example.com" } })],
      "POST /api/v1/integrations/oauth/logout/confirm": [ok({ redirect_url: "https://crm.example.com/login?state=s" })],
    });
    const { container } = render(<SignOutConfirm />);
    expect(await screen.findByRole("heading", { name: "Sign out of Workeasy360 too?" })).toBeInTheDocument();
    await expectNoViolations(container);
    // Opening the page signed no one out.
    expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Sign out of Workeasy360/ }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://crm.example.com/login?state=s"));
    expect(calls.at(-1)?.body).toEqual({ request: "sealed", sign_out: true });
  });

  it("stays signed in when asked, and only offers a way back when already signed out", async () => {
    query = "request=sealed";
    const calls = mockApi({
      "GET /api/v1/auth/session": [SESSION],
      "GET /api/v1/integrations/oauth/logout/request": [ok({ product: { name: "Easy CRM" }, signed_in: true, account: { email: "a@x.io" } })],
      "POST /api/v1/integrations/oauth/logout/confirm": [ok({ redirect_url: "https://crm.example.com/login" })],
    });
    const { unmount } = render(<SignOutConfirm />);
    fireEvent.click(await screen.findByRole("button", { name: /Stay signed in to Workeasy360/ }));
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(calls.at(-1)?.body).toEqual({ request: "sealed", sign_out: false });
    unmount();

    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "GET /api/v1/integrations/oauth/logout/request": [ok({ product: { name: "Easy CRM" }, signed_in: false, account: null })],
    });
    render(<SignOutConfirm />);
    expect(await screen.findByRole("heading", { name: "You're signed out of Workeasy360" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Return to Easy CRM" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sign out of Workeasy360/ })).not.toBeInTheDocument();
  });
});

function prefs(overrides: Partial<SignInPreferences> = {}): SignInPreferences {
  return {
    default: "ask",
    default_is_set: false,
    default_choices: ["auto_sso", "ask", "product_login"],
    products: [
      {
        product_code: "easy_crm",
        display_name: "Easy CRM",
        name: "Easy CRM",
        icon: "customers",
        mode: "sso_optional",
        sso: "enabled",
        product_login_available: true,
        override: "inherit",
        preference: "ask",
        effective: "ask",
        constraint: "",
        choices: ["inherit", "auto_sso", "ask", "product_login"],
      },
      {
        product_code: "easybook",
        display_name: "EasyBook",
        name: "EasyBook",
        icon: "book",
        mode: "sso_only",
        sso: "enabled",
        product_login_available: false,
        override: "auto_sso",
        preference: "auto_sso",
        effective: "auto_sso",
        constraint: "",
        choices: ["inherit", "auto_sso", "ask"],
      },
      {
        product_code: "easy_project",
        display_name: "Easy Project",
        name: "Easy Project",
        icon: "board",
        mode: "local_only",
        sso: "disabled",
        product_login_available: true,
        override: "inherit",
        preference: "ask",
        effective: "product_login",
        constraint: "sso_unavailable",
        choices: [],
      },
    ],
    ...overrides,
  };
}

describe("sign-in preferences", () => {
  it("shows the default and each product's override, offering only what the server allows", async () => {
    mockApi({ "GET /api/v1/auth/sign-in-preferences": [ok(prefs())] });
    const { container } = render(<SignInPreferencesScreen />);
    expect(await screen.findByRole("heading", { level: 1, name: "Sign-in preferences" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Ask me each time/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Automatically sign me in/ })).not.toBeChecked();

    const crm = screen.getByRole("combobox", { name: "Easy CRM" });
    expect(within(crm).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Use my default (ask each time)",
      "Automatically use Workeasy360",
      "Ask each time",
      "Prefer Easy CRM sign-in",
    ]);
    // An SSO-only product is never offered its own sign-in.
    const book = screen.getByRole("combobox", { name: "EasyBook" });
    expect(within(book).queryByRole("option", { name: /EasyBook sign-in/ })).not.toBeInTheDocument();
    expect(book).toHaveValue("auto_sso");
    // A product Workeasy360 cannot sign anyone in to has nothing to choose.
    expect(screen.queryByRole("combobox", { name: "Easy Project" })).not.toBeInTheDocument();
    expect(screen.getByText("Separate product sign-in required.")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("saves only what changed and says when it takes effect", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/sign-in-preferences": [ok(prefs())],
      "PUT /api/v1/auth/sign-in-preferences": [ok(prefs({ default: "auto_sso", default_is_set: true }))],
    });
    render(<SignInPreferencesScreen />);
    const save = await screen.findByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Automatically sign me in/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "Easy CRM" }), { target: { value: "product_login" } });
    fireEvent.click(save);
    expect(await screen.findByText("Saved. This applies the next time you open a product.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "PUT /api/v1/auth/sign-in-preferences")?.body).toEqual({
      default: "auto_sso",
      products: { easy_crm: "product_login" },
    });
  });

  it("resets everything to the default", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/sign-in-preferences": [ok(prefs())],
      "DELETE /api/v1/auth/sign-in-preferences": [ok(prefs())],
    });
    render(<SignInPreferencesScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Reset all to default" }));
    expect(await screen.findByText(/Reset\. Workeasy360 will ask you each time/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === "DELETE /api/v1/auth/sign-in-preferences")).toBe(true);
  });

  it("shows the server's refusal in words", async () => {
    mockApi({
      "GET /api/v1/auth/sign-in-preferences": [ok(prefs())],
      "PUT /api/v1/auth/sign-in-preferences": [
        { status: 400, body: { code: "sign_in_preference_invalid", detail: "That product does not offer this way of signing in.", field: "products.easybook" } },
      ],
    });
    render(<SignInPreferencesScreen />);
    fireEvent.change(await screen.findByRole("combobox", { name: "EasyBook" }), { target: { value: "ask" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("That product does not offer this way of signing in.")).toBeInTheDocument();
  });
});

describe("sign-in copy", () => {
  it("words what happens from the server's answer, never guessing", () => {
    expect(whatHappens({ sso: "disabled", effective: "product_login", constraint: "sso_unavailable" }, "Easy Project")).toBe(
      "Separate product sign-in required",
    );
    expect(whatHappens({ sso: "integration_required", effective: "product_login", constraint: "sso_unavailable" }, "X")).toBe(
      "Separate product sign-in required",
    );
    expect(whatHappens({ sso: "enabled", effective: "auto_sso", constraint: "" }, "Easy CRM")).toBe("Opens with your Workeasy360 account");
    expect(whatHappens({ sso: "enabled", effective: "product_login", constraint: "" }, "Easy CRM")).toBe("Opens Easy CRM's own sign-in page");
    expect(whatHappens({ sso: "enabled", effective: "ask", constraint: "product_requires_sso" }, "EasyBook")).toMatch(/only uses Workeasy360/);
  });

  it("describes a preference change in the account trail", () => {
    expect(describePreferenceChange("default=auto_sso")).toBe("Default product sign-in changed to automatic");
    expect(describePreferenceChange("easy_crm=product_login")).toBe("Product sign-in for easy crm changed to product sign-in");
    expect(describePreferenceChange("easy_crm=inherit")).toBe("Product sign-in for easy crm changed to my default");
    expect(describePreferenceChange("reset")).toBe("Product sign-in preferences reset to default");
    expect(overrideLabel("inherit", "Easy CRM", "auto_sso")).toBe("Use my default (automatic)");
  });
});
