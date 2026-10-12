import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { WhiteLabelSetup } from "./WhiteLabelSetup";
import { PortalPreview, type PreviewData } from "./BrandPreview";
import { AdminContextForTest, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";

/**
 * The staff white-label setup wizard: ten steps that configure an existing
 * partner. What these tests pin is what the backend cannot enforce for the
 * browser: nothing is created, each step is its own action (so a second-factor
 * prompt lands on the step that needs one), a logo is checked before it leaves
 * the machine, visibility never reads as access, activation is never automatic,
 * the sign-in statement is honest, and nothing typed is ever drawn as markup.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/admin/partners/white-label/new",
}));

const ID = "p1";
const ADMIN = "/api/v1/admin/partners";
const BASE = `${ADMIN}/${ID}`;
const SETUP = `GET ${BASE}/white-label`;
const BRAND_PUT = `PUT ${BASE}/brand`;
const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage"];
const STEP_UP: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };

const portal = (over: Record<string, unknown> = {}) => ({
  short_name: "",
  support_name: "",
  support_phone: "",
  website_url: "",
  welcome_message: "",
  footer_text: "",
  sender_name: "",
  reply_to_email: "",
  login_show_welcome: true,
  login_show_support: true,
  use_for_invitations: false,
  ...over,
});

const brand = (over: Record<string, unknown> = {}, portalOver: Record<string, unknown> = {}) => ({
  brand_name: "Acme Brand",
  logo_file_id: null,
  accent_color: "#1a56db",
  theme: {},
  support_email: "help@acme.example",
  support_url: "",
  portal: portal(portalOver),
  version: 3,
  updated_at: "2026-02-01T00:00:00Z",
  ...over,
});

const product = (code: string, name: string, over: Record<string, unknown> = {}) => ({
  product_code: code,
  product_name: name,
  status: "available",
  grant_id: `g-${code}`,
  available_to_partner: true,
  visible_in_portal: true,
  sell_allowed: true,
  platform_trial_enabled: true,
  partner_allows_trial: true,
  trial_available: true,
  ...over,
});

const PRODUCTS = [
  product("easy_crm", "Easy CRM"),
  product("easy_helpdesk", "Easy Helpdesk", { visible_in_portal: false, partner_allows_trial: false, trial_available: false }),
  product("easy_project", "Easy Project", { platform_trial_enabled: false, trial_available: false }),
  product("easybook", "EasyBook", {
    grant_id: null,
    available_to_partner: false,
    visible_in_portal: false,
    sell_allowed: false,
    partner_allows_trial: false,
    trial_available: false,
  }),
  // A product added to the catalog tomorrow: nothing in the wizard names it.
  product("future_suite", "Future Suite", { visible_in_portal: false }),
];

const AUTH = {
  mode: "workeasy360_secure_sign_in",
  custom_domain_sign_in: false,
  summary:
    "Customers sign in on Workeasy360's own secure sign-in page, which shows this brand. A custom domain shows the brand and its sign-in link hands over to that page: no password is ever typed on a partner's domain.",
};

function domain(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: "d1",
    hostname: "app.acme.example",
    status,
    verification_record: { type: "TXT", name: "_w360-verify.app.acme.example", value: "w360-verify=abc123" },
    failure_code: "",
    version: 1,
    ...extra,
  };
}

function setup(over: Record<string, unknown> = {}) {
  return {
    partner: { id: ID, code: "acme", display_name: "Acme Partners", status: "active", version: 4 },
    experience: [{ key: "reseller", label: "Reseller" }],
    capabilities: ["customer.provision"],
    enabled: false,
    missing_capabilities: ["branding.manage", "domain.manage"],
    customers: 3,
    brand: brand(),
    products: PRODUCTS,
    domain: null,
    branding_on_custom_domain: "none",
    authentication: AUTH,
    checks: { brand_saved: true, logo_saved: false, domain_requested: false },
    ...over,
  };
}

const partnerRow = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  code: name.toLowerCase().replace(/\W+/g, "-"),
  display_name: name,
  status: "active",
  payout_currency: "GBP",
  created_at: "2026-01-01T00:00:00Z",
  experience: [{ key: "reseller", label: "Reseller" }],
  capability_count: 4,
  customers: 7,
  products: [{ product_code: "easy_crm", plan_code: null }],
  active_subscriptions: 0,
  commission: [],
  payouts: { open: 0, latest: null, paid: [] },
  referrals: { codes: [], active_codes: 0, captured: 0, converted: 0 },
  last_activity_at: "2026-02-01T00:00:00Z",
  ...over,
});

const page = (rows: unknown[]) => ok({ results: rows, next_cursor: null, has_more: false });

const writes = (calls: Call[]) => calls.filter((c) => /^(POST|PUT|PATCH|DELETE) /.test(c.key));
const putBody = (calls: Call[]) => calls.filter((c) => c.key === BRAND_PUT).at(-1)!.body as Record<string, unknown>;

function renderSetup(props: { partnerId?: string; permissions?: string[] } = {}) {
  const { partnerId = ID, permissions = MANAGE } = props;
  return render(
    <AdminContextForTest permissions={permissions}>
      <WhiteLabelSetup partnerId={partnerId} />
    </AdminContextForTest>,
  );
}

const heading = (n: number) => screen.findByRole("heading", { name: new RegExp(`Step ${n} of 10`) });
const advance = () => fireEvent.click(screen.getByRole("button", { name: /^(Save & continue|Continue)$/ }));
const back = () => fireEvent.click(screen.getByRole("button", { name: "Back" }));
const field = (label: RegExp | string) => screen.getByLabelText(label) as HTMLInputElement;
const type = (label: RegExp | string, value: string) => fireEvent.change(field(label), { target: { value } });

/** From step 2, press Continue until step `n` (no step on the way has anything unsaved). */
async function toStep(n: number, from = 2) {
  for (let i = from; i < n; i++) {
    await heading(i);
    advance();
  }
  await heading(n);
}

const png = (name = "logo.png", extra = 32) =>
  new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, ...new Array(extra).fill(1)])], name, { type: "image/png" });

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "clipboard");
});

// --- the frame ----------------------------------------------------------------------------------------

describe("the wizard frame", () => {
  it("lists the ten steps in an ordered list with the current one marked, and opens at the brand for a chosen partner", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    const { container } = renderSetup();
    const steps = await screen.findByRole("list", { name: "Setup progress" });
    const items = within(steps).getAllByRole("listitem");
    expect(steps.tagName).toBe("OL");
    expect(items.map((li) => li.textContent?.replace(/^\d+\.\s*/, ""))).toEqual([
      "Select partner",
      "Brand identity",
      "Portal appearance",
      "Products",
      "Customer portal",
      "Communication",
      "Custom domain",
      "Login experience",
      "Reseller connection",
      "Review",
    ]);
    expect(items.filter((li) => li.getAttribute("aria-current") === "step")).toHaveLength(1);
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(await heading(2)).toHaveTextContent("Step 2 of 10: Brand identity");
    expect(screen.getByText("Acme Partners")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("does not take focus on first load, then focuses the step heading each time the step changes", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    renderSetup();
    const first = await heading(2);
    expect(first).not.toHaveFocus();
    advance();
    const second = await heading(3);
    await waitFor(() => expect(second).toHaveFocus());
    advance();
    await waitFor(() => expect(screen.getByRole("heading", { name: /Step 4 of 10/ })).toHaveFocus());
    back();
    await waitFor(() => expect(screen.getByRole("heading", { name: /Step 3 of 10/ })).toHaveFocus());
  });

  it("walks forward and back through every step, and offers no Back on the first", async () => {
    mockApi({ [SETUP]: [ok(setup())], [`GET ${ADMIN}/resellers`]: [page([partnerRow(ID, "Acme Partners")])] });
    renderSetup();
    await toStep(10);
    expect(screen.queryByRole("button", { name: /^(Save & continue|Continue)$/ })).not.toBeInTheDocument(); // the last step
    for (let n = 9; n >= 1; n--) {
      back();
      await heading(n);
    }
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  it("says plainly when the person may not change partners, or the partner is terminated", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    const first = renderSetup({ permissions: ["platform.staff_partner.view"] });
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission to change partners/);
    first.unmount();

    mockApi({ [SETUP]: [ok(setup({ partner: { id: ID, code: "acme", display_name: "Acme Partners", status: "terminated", version: 4 } }))] });
    renderSetup();
    expect(await screen.findByRole("alert")).toHaveTextContent(/terminated partner cannot take a new brand or domain/);
    expect(screen.getByRole("button", { name: "Choose a different partner" })).toBeInTheDocument();
  });

  it("reports a partner that cannot be read instead of showing an empty wizard", async () => {
    mockApi({ [SETUP]: [{ status: 404, body: { code: "partner_record_not_found" } }] });
    renderSetup();
    expect(await screen.findByRole("alert")).toHaveTextContent(/don.t have access to this partner/);
  });
});

// --- step 1 -----------------------------------------------------------------------------------------------------

describe("step 1: select partner", () => {
  const RESELLERS = `GET ${ADMIN}/resellers`;

  function renderNew() {
    return render(
      <AdminContextForTest permissions={MANAGE}>
        <WhiteLabelSetup />
      </AdminContextForTest>,
    );
  }

  it("searches every partner, not just resellers, and shows name, status, derived labels, products and customers", async () => {
    const calls = mockApi({
      [RESELLERS]: [
        page([
          partnerRow("p1", "Acme Partners"),
          partnerRow("p2", "Referral Co", { experience: [{ key: "affiliate", label: "Affiliate" }], products: [], customers: 0, status: "draft" }),
          partnerRow("p3", "Old Co", { status: "terminated" }),
        ]),
      ],
    });
    const { container } = renderNew();
    expect(await heading(1)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Partners" });
    expect(calls.find((c) => c.key === RESELLERS)!.url).toContain("scope=all");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Partner", "Status", "Derived labels", "Products", "Customers"]);
    const acme = within(table).getByRole("row", { name: /Acme Partners/ });
    expect(within(acme).getByText("Reseller")).toBeInTheDocument();
    expect(within(acme).getByText("easy_crm")).toBeInTheDocument();
    expect(within(acme).getByText("7")).toBeInTheDocument();
    const referral = within(table).getByRole("row", { name: /Referral Co/ });
    expect(within(referral).getByText("Affiliate")).toBeInTheDocument();
    expect(within(referral).getByText("draft")).toBeInTheDocument();
    expect(within(referral).getByText("None")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("cannot choose a terminated partner, and cannot continue before choosing one", async () => {
    mockApi({ [RESELLERS]: [page([partnerRow("p3", "Old Co", { status: "terminated" }), partnerRow("p1", "Acme Partners")])] });
    renderNew();
    await screen.findByRole("table", { name: "Partners" });
    expect(screen.getByRole("radio", { name: "Old Co" })).toBeDisabled();
    expect(screen.getByText("A terminated partner cannot be chosen.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Acme Partners" }));
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("searches by what was typed", async () => {
    const calls = mockApi({ [RESELLERS]: [page([partnerRow("p1", "Acme Partners")])] });
    renderNew();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.change(screen.getByLabelText("Search by partner name or code"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(calls.filter((c) => c.key === RESELLERS).at(-1)!.url).toContain("q=acme"));
  });

  it("creates nothing: choosing a partner only reads its setup, then opens the brand step", async () => {
    const calls = mockApi({
      [RESELLERS]: [page([partnerRow("p1", "Acme Partners"), partnerRow("p2", "Referral Co", { experience: [{ key: "affiliate", label: "Affiliate" }] })])],
      [SETUP]: [ok(setup())],
      [`GET ${ADMIN}/p2/white-label`]: [ok(setup({ partner: { id: "p2", code: "referral-co", display_name: "Referral Co", status: "active", version: 1 }, experience: [{ key: "affiliate", label: "Affiliate" }] }))],
    });
    renderNew();
    await screen.findByRole("table", { name: "Partners" });
    fireEvent.click(screen.getByRole("radio", { name: "Referral Co" })); // an affiliate: chosen like any other
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await heading(2)).toBeInTheDocument();
    expect(screen.getByText("Referral Co")).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
    expect(calls.some((c) => c.key === `GET ${ADMIN}/p2/white-label`)).toBe(true);
  });

  it("is reachable again from a pre-selected partner, keeping what was typed", async () => {
    mockApi({ [SETUP]: [ok(setup())], [RESELLERS]: [page([partnerRow("p1", "Acme Partners")])] });
    renderSetup();
    await heading(2);
    type(/^Short name/, "Acme");
    back();
    await heading(1);
    expect(screen.getByText(/A partner is already chosen/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await heading(2);
    expect(field(/^Short name/).value).toBe("Acme");
  });
});

// --- step 2 -----------------------------------------------------------------------------------------------------

describe("step 2: brand identity", () => {
  it("marks the one required field and says (optional) on the rest", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    const { container } = renderSetup();
    await heading(2);
    const name = screen.getByText(/^Brand name/, { selector: "label" });
    expect(name.textContent).toContain("*");
    expect(field(/^Brand name/)).toBeRequired();
    for (const label of [/^Short name/, /^Support contact name/, /^Support email/, /^Support phone/, /^Support page/, /^Company website/, /^Primary colour/]) {
      expect(screen.getByText(label, { selector: "label" })).toHaveTextContent(/optional/);
    }
    expect(screen.getByText(/^Logo/, { selector: "label" })).toHaveTextContent(/optional/);
    await expectNoViolations(container);
  });

  it("offers one primary colour and no secondary colour or favicon, because nothing would show them", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    const { container } = renderSetup();
    await heading(2);
    expect(container.textContent).not.toMatch(/secondary colour|favicon|secondary color/i);
    expect(container.querySelectorAll("input[type=color]")).toHaveLength(1);
    expect(container.querySelectorAll("input[type=file]")).toHaveLength(1);
  });

  it("refuses an empty name, a bad colour, a bad address and a non-https page before anything is sent", async () => {
    const calls = mockApi({ [SETUP]: [ok(setup({ brand: null, checks: { brand_saved: false, logo_saved: false, domain_requested: false } }))] });
    renderSetup();
    await heading(2);
    advance();
    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Enter the brand name.");
    await waitFor(() => expect(summary).toHaveFocus());

    type(/^Brand name/, "Acme");
    type(/^Primary colour/, "red");
    type(/^Support email/, "not-an-address");
    type(/^Support page/, "http://insecure.example.com");
    type(/^Company website/, "https://x");
    type(/^Support phone/, "12");
    type(/^Short name/, "<b>x</b>");
    advance();
    const alert = await screen.findByRole("alert");
    for (const text of [/six-digit hex colour/, /valid support email/, /support page must be a plain https/, /company website must be a plain https/, /phone number/, /plain text only/]) {
      expect(alert).toHaveTextContent(text);
    }
    // ...and each message is also on its own field, linked for assistive technology.
    expect(field(/^Primary colour/)).toHaveAccessibleDescription(/six-digit hex colour/);
    expect(field(/^Primary colour/)).toBeInvalid();
    expect(screen.getByRole("heading", { name: /Step 2 of 10/ })).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });

  it("keeps the text colour and the native colour picker in step, as a six-digit hex", async () => {
    mockApi({ [SETUP]: [ok(setup())] });
    renderSetup();
    await heading(2);
    const picker = screen.getByLabelText("Pick a colour") as HTMLInputElement;
    expect(picker.value).toBe("#1a56db");
    fireEvent.change(picker, { target: { value: "#ff8800" } });
    expect(field(/^Primary colour/).value).toBe("#ff8800");
    type(/^Primary colour/, "#00AA55");
    expect(picker.value).toBe("#00aa55");
    type(/^Primary colour/, "nope");
    expect(picker.value).toMatch(/^#[0-9a-f]{6}$/); // never anything but a six-digit hex
  });

  it("saves the identity with the stored version, then re-reads the setup and moves on", async () => {
    const saved = brand({ brand_name: "New Name", version: 4 }, { short_name: "New", support_name: "Sam", support_phone: "+44 20 7946 0000", website_url: "https://acme.example" });
    const calls = mockApi({
      [SETUP]: [ok(setup()), ok(setup({ brand: saved }))],
      [BRAND_PUT]: [ok(saved)],
    });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "New Name");
    type(/^Short name/, "New");
    type(/^Support contact name/, "Sam");
    type(/^Support phone/, "+44 20 7946 0000");
    type(/^Company website/, "https://acme.example");
    advance();
    await heading(3);
    expect(await screen.findByText("Brand identity saved.")).toBeInTheDocument();
    expect(putBody(calls)).toEqual({
      brand_name: "New Name",
      accent_color: "#1a56db",
      support_email: "help@acme.example",
      support_url: "",
      theme: {},
      portal: { short_name: "New", support_name: "Sam", support_phone: "+44 20 7946 0000", website_url: "https://acme.example" },
      expected_version: 3,
    });
    expect(calls.filter((c) => c.key === SETUP)).toHaveLength(2); // read, save, read again for the new version
    expect(writes(calls).map((c) => c.key)).toEqual([BRAND_PUT]);
  });

  it("sends no version for the very first brand, and sends the next save the version the re-read returned", async () => {
    const first = brand({ version: 1 });
    const calls = mockApi({
      [SETUP]: [ok(setup({ brand: null })), ok(setup({ brand: first })), ok(setup({ brand: first }))],
      [BRAND_PUT]: [ok(first)],
    });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Acme Brand");
    advance();
    await heading(3);
    expect((calls.find((c) => c.key === BRAND_PUT)!.body as { expected_version?: number }).expected_version).toBeUndefined();
    await toStep(5, 3);
    type(/^Welcome message/, "Hello");
    advance();
    await heading(6);
    expect((putBody(calls) as { expected_version: number }).expected_version).toBe(1);
  });

  it("Save draft stores the step and stays on it", async () => {
    const calls = mockApi({ [SETUP]: [ok(setup())], [BRAND_PUT]: [ok(brand({ brand_name: "Draft", version: 4 }))] });
    renderSetup();
    await heading(2);
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled(); // nothing changed yet
    type(/^Brand name/, "Draft");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Step 2 of 10/ })).toBeInTheDocument();
    expect(writes(calls).map((c) => c.key)).toEqual([BRAND_PUT]);
  });

  it("shows the server's problem on the field it names, in the server's own words", async () => {
    mockApi({
      [SETUP]: [ok(setup())],
      [BRAND_PUT]: [{ status: 400, body: { code: "partner_validation_error", detail: "Enter a plain https:// address.", field: "portal.website_url" } }],
    });
    renderSetup();
    await heading(2);
    type(/^Company website/, "https://site.example");
    advance();
    await waitFor(() => expect(field(/^Company website/)).toBeInvalid());
    expect(field(/^Company website/)).toHaveAccessibleDescription("Enter a plain https:// address.");
    expect(screen.getByRole("heading", { name: /Step 2 of 10/ })).toBeInTheDocument(); // not moved on
    // Typing again clears it.
    type(/^Company website/, "https://other.example");
    expect(field(/^Company website/)).not.toBeInvalid();
  });

  it("refuses a stale save with the server's message instead of overwriting", async () => {
    mockApi({
      [SETUP]: [ok(setup())],
      [BRAND_PUT]: [{ status: 409, body: { code: "concurrent_modification", detail: "The brand changed since you read it. Re-read it and retry." } }],
    });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Renamed");
    advance();
    expect(await screen.findByText(/The brand changed since you read it/)).toBeInTheDocument();
  });

  it("says so when the save went through but the re-read failed, instead of reporting a failed save", async () => {
    mockApi({
      [SETUP]: [ok(setup()), { status: 500, body: { code: "boom" } }],
      [BRAND_PUT]: [ok(brand({ brand_name: "Renamed", version: 4 }))],
    });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Renamed");
    advance();
    await heading(3);
    expect(await screen.findByText(/The change was saved, but this page could not be refreshed/)).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong. Please try again.")).not.toBeInTheDocument();
  });

  it("asks for a second factor at this step and then completes the same save once", async () => {
    const saved = brand({ brand_name: "Renamed", version: 4 });
    const calls = mockApi({
      [SETUP]: [ok(setup()), ok(setup({ brand: saved }))],
      [BRAND_PUT]: [STEP_UP, ok(saved)],
      "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
    });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Renamed");
    advance();
    fireEvent.change(await screen.findByLabelText(/needs a recent verification/), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await heading(3);
    expect(calls.filter((c) => c.key === BRAND_PUT)).toHaveLength(2); // refused once, then done
    expect((calls.filter((c) => c.key === BRAND_PUT)[1].body as { expected_version: number }).expected_version).toBe(3);
  });

  it("does not move on when the save is refused for a missing second factor", async () => {
    mockApi({ [SETUP]: [ok(setup())], [BRAND_PUT]: [STEP_UP] });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Renamed");
    advance();
    await screen.findByLabelText(/needs a recent verification/);
    expect(screen.getByRole("heading", { name: /Step 2 of 10/ })).toBeInTheDocument();
  });
});

// --- the logo ---------------------------------------------------------------------------------------------------

describe("the logo", () => {
  const pick = (file: File) => fireEvent.change(screen.getByLabelText(/^Logo/), { target: { files: [file] } });

  async function open() {
    const calls = mockApi({
      [SETUP]: [ok(setup()), ok(setup({ brand: brand({ logo_file_id: "f1", version: 4 }) }))],
      [`POST ${BASE}/brand/logo`]: [{ status: 201, body: { logo_file_id: "f1" } }],
      [BRAND_PUT]: [ok(brand({ logo_file_id: "f1", version: 4 }))],
    });
    renderSetup();
    await heading(2);
    return calls;
  }

  it("accepts only PNG or JPEG", async () => {
    await open();
    expect((screen.getByLabelText(/^Logo/) as HTMLInputElement).accept).toBe("image/png,image/jpeg");
    pick(new File(["<svg xmlns='http://www.w3.org/2000/svg'/>"], "logo.svg", { type: "image/svg+xml" }));
    expect(await screen.findByText(/Use a PNG or JPEG image\. SVG, HTML and other files are not accepted/)).toBeInTheDocument();
    pick(new File(["<html></html>"], "logo.html", { type: "text/html" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not accepted/));
  });

  it("refuses a file over 512 KB", async () => {
    await open();
    pick(new File([new Uint8Array(513 * 1024)], "big.png", { type: "image/png" }));
    expect(await screen.findByText("The logo must be 512 KB or smaller.")).toBeInTheDocument();
  });

  it("refuses a PNG-named page of HTML, by its first bytes", async () => {
    const calls = await open();
    pick(new File(["<html><script>alert(1)</script></html>"], "logo.png", { type: "image/png" }));
    expect(await screen.findByText(/not really a PNG or JPEG image/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Logo/)).toBeInvalid();
    expect(document.querySelector("img")).toBeNull(); // nothing was drawn from it
    expect(writes(calls)).toEqual([]);
  });

  it("refuses a JPEG that is really a PNG, and an empty file", async () => {
    await open();
    pick(new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2])], "logo.jpg", { type: "image/jpeg" }));
    expect(await screen.findByText(/not really a PNG or JPEG image/)).toBeInTheDocument();
    pick(new File([], "empty.png", { type: "image/png" }));
    expect(await screen.findByText("That file is empty.")).toBeInTheDocument();
  });

  it("previews a real image locally, uploads it before saving the brand, and replaces the saved logo", async () => {
    const calls = await open();
    pick(png());
    await waitFor(() => expect(document.querySelector("img")?.getAttribute("src")).toMatch(/^data:image\/png;base64,/));
    expect(screen.getByText(/logo\.png will be saved with the brand and replaces the saved logo/)).toBeInTheDocument();
    advance();
    await heading(3);
    expect(writes(calls).map((c) => c.key)).toEqual([`POST ${BASE}/brand/logo`, BRAND_PUT]);
    expect(calls.find((c) => c.key === `POST ${BASE}/brand/logo`)!.headers["content-type"]).toBe("image/png");
    expect((putBody(calls) as { logo_file_id: string }).logo_file_id).toBe("f1");
  });

  it("accepts a JPEG with a JPEG signature", async () => {
    await open();
    pick(new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3])], "photo.jpg", { type: "image/jpeg" }));
    await waitFor(() => expect(document.querySelector("img")?.getAttribute("src")).toMatch(/^data:image\/jpeg;base64,/));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not upload the same file twice when the save has to be retried", async () => {
    const calls = mockApi({
      [SETUP]: [ok(setup()), ok(setup({ brand: brand({ logo_file_id: "f1", version: 4 }) }))],
      [`POST ${BASE}/brand/logo`]: [{ status: 201, body: { logo_file_id: "f1" } }],
      [BRAND_PUT]: [{ status: 500, body: { code: "boom" } }, ok(brand({ logo_file_id: "f1", version: 4 }))],
    });
    renderSetup();
    await heading(2);
    pick(png());
    await waitFor(() => expect(document.querySelector("img")).not.toBeNull());
    advance();
    await screen.findByRole("alert");
    advance();
    await heading(3);
    expect(calls.filter((c) => c.key === `POST ${BASE}/brand/logo`)).toHaveLength(1);
    expect(calls.filter((c) => c.key === BRAND_PUT)).toHaveLength(2);
  });

  it("keeps the saved logo when another step saves, and draws it from the same-origin address", async () => {
    const withLogo = brand({ logo_file_id: "f0", version: 5 });
    const calls = mockApi({ [SETUP]: [ok(setup({ brand: withLogo }))], [BRAND_PUT]: [ok(withLogo)] });
    renderSetup();
    await heading(2);
    expect(document.querySelector("img")?.getAttribute("src")).toBe(`${BASE}/brand/logo/view?v=5`);
    await toStep(5, 2);
    type(/^Welcome message/, "Hi");
    advance();
    await heading(6);
    expect((putBody(calls) as { logo_file_id: string }).logo_file_id).toBe("f0"); // not dropped
  });
});

// --- step 3 -----------------------------------------------------------------------------------------------------

describe("step 3: portal appearance", () => {
  const FILLED = brand({ brand_name: "Acme Brand", version: 3 }, { short_name: "Acme", welcome_message: "Welcome aboard", footer_text: "Acme Ltd, London", support_name: "Sam", support_phone: "+44 20 7946 0000", website_url: "https://acme.example" });

  it("offers four modes as tabs, moved with the arrow keys", async () => {
    mockApi({ [SETUP]: [ok(setup({ brand: FILLED }))] });
    renderSetup();
    await toStep(3);
    const tabs = screen.getByRole("tablist", { name: "Preview of" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Login", "Dashboard", "Product launcher", "Header"]);
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Login" }), { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Dashboard" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Dashboard preview" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Dashboard" }), { key: "ArrowRight" });
    expect(screen.getByRole("region", { name: "Product launcher preview" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Product launcher" }), { key: "ArrowRight" });
    expect(screen.getByRole("region", { name: "Header preview" })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Header" }), { key: "Home" });
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toBeInTheDocument();
  });

  it("draws the portal from the draft: welcome on login, short name and footer on the dashboard, visible products on the launcher", async () => {
    mockApi({ [SETUP]: [ok(setup({ brand: FILLED }))] });
    const { container } = renderSetup();
    await toStep(3);
    const login = screen.getByRole("region", { name: "Sign-in page preview" });
    expect(login).toHaveTextContent("Welcome aboard");
    expect(login).toHaveTextContent("Need help? Sam");
    fireEvent.click(screen.getByRole("tab", { name: "Dashboard" }));
    const dashboard = screen.getByRole("region", { name: "Dashboard preview" });
    expect(dashboard).toHaveTextContent("Acme"); // the short name in the header
    expect(dashboard).toHaveTextContent("Welcome aboard");
    expect(dashboard).toHaveTextContent("Acme Ltd, London");
    fireEvent.click(screen.getByRole("tab", { name: "Product launcher" }));
    const launcher = screen.getByRole("region", { name: "Product launcher preview" });
    expect(launcher).toHaveTextContent("Easy CRM");
    expect(launcher).toHaveTextContent("Easy Project");
    expect(launcher).not.toHaveTextContent("Easy Helpdesk"); // hidden in the portal
    expect(launcher).not.toHaveTextContent("EasyBook"); // not the partner's to show
    await expectNoViolations(container);
  });

  it("says the layout and every security prompt stay Workeasy360's, and stores nothing by being looked at", async () => {
    const calls = mockApi({ [SETUP]: [ok(setup({ brand: FILLED }))] });
    renderSetup();
    await toStep(3);
    expect(screen.getAllByText(/layout and every security prompt stay Workeasy360/).length).toBeGreaterThan(0);
    for (const tab of ["Dashboard", "Product launcher", "Header", "Login"]) fireEvent.click(screen.getByRole("tab", { name: tab }));
    expect(writes(calls)).toEqual([]);
  });

  it("shows changes that are not saved yet, and says so", async () => {
    mockApi({ [SETUP]: [ok(setup({ brand: FILLED }))] });
    renderSetup();
    await toStep(5);
    type(/^Welcome message/, "Typed but not saved");
    back();
    await heading(4);
    back();
    await heading(3);
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toHaveTextContent("Typed but not saved");
    expect(screen.getByText(/changes that are not saved yet/)).toBeInTheDocument();
  });

  it("shows no welcome message on login when it is turned off, and says why", async () => {
    mockApi({ [SETUP]: [ok(setup({ brand: brand({}, { welcome_message: "Welcome aboard", login_show_welcome: false }) }))] });
    renderSetup();
    await toStep(3);
    const login = screen.getByRole("region", { name: "Sign-in page preview" });
    expect(login).not.toHaveTextContent("Welcome aboard");
    expect(login).toHaveTextContent(/turned off for the sign-in page/);
  });
});

// --- step 4 -----------------------------------------------------------------------------------------------------

describe("step 4: products", () => {
  async function open(extra: Record<string, Reply[]> = {}, over: Record<string, unknown> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup(over))], ...extra });
    const view = renderSetup();
    await toStep(4);
    return { calls, ...view };
  }

  it("lists every product the server returns, with its four facts, never a list of its own", async () => {
    const { container } = await open();
    const table = screen.getByRole("table", { name: "Products in the catalog" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Product",
      "Available to partner",
      "Visible in portal",
      "Sell allowed",
      "Trial available",
    ]);
    expect(within(table).getAllByRole("row")).toHaveLength(1 + PRODUCTS.length);
    expect(within(table).getByRole("row", { name: /Future Suite/ })).toBeInTheDocument(); // a code this file never names
    await expectNoViolations(container);
  });

  it("shows the server's derived trial and sell facts, and why a trial is not available", async () => {
    await open();
    const row = (name: string) => screen.getByRole("row", { name: new RegExp(name) });
    expect(within(row("Easy CRM")).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Yes", "Visible in portal: Easy CRM", "Yes", "Yes"]);
    expect(within(row("Easy Helpdesk")).getAllByRole("cell")[3]).toHaveTextContent("No: the partner has turned trials off");
    expect(within(row("Easy Project")).getAllByRole("cell")[3]).toHaveTextContent("No: no platform trial is set up for this product");
    expect(within(row("EasyBook")).getAllByRole("cell").map((c) => c.textContent?.split("Not allowed")[0])).toEqual(["No", "No", "No", "No"]);
  });

  it("says a product the partner has no grant for is not allowed, and links to the partner's Products tab instead of granting", async () => {
    const { calls } = await open();
    const row = screen.getByRole("row", { name: /EasyBook/ });
    expect(row).toHaveTextContent("Not allowed for this partner");
    expect(within(row).getByRole("link", { name: /Products tab/ })).toHaveAttribute("href", `/admin/partners/${ID}?tab=products`);
    expect(within(row).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });

  it("states that visibility never grants access", async () => {
    await open();
    const note = screen.getByText(/Visibility never grants access/).closest("div")!;
    expect(note).toHaveTextContent(/still needs a subscription or a trial to launch a product/);
    expect(note).toHaveTextContent(/already holds still launches even if it is hidden here/);
  });

  it("saves a visibility change through the grant, and shows what the server then reports", async () => {
    const after = PRODUCTS.map((p) => (p.product_code === "easy_helpdesk" ? { ...p, visible_in_portal: true } : p));
    const { calls } = await open({
      [`PATCH ${BASE}/product-grants/g-easy_helpdesk`]: [ok({ id: "g-easy_helpdesk", trial_allowed: false, portal_visible: true })],
      [SETUP]: [ok(setup()), ok(setup({ products: after }))],
    });
    const box = screen.getByRole("checkbox", { name: "Visible in portal: Easy Helpdesk" }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(await screen.findByText("Easy Helpdesk is now shown in the portal.")).toBeInTheDocument();
    expect(calls.find((c) => c.key.startsWith("PATCH "))!.body).toEqual({ portal_visible: true });
    await waitFor(() => expect((screen.getByRole("checkbox", { name: "Visible in portal: Easy Helpdesk" }) as HTMLInputElement).checked).toBe(true));
    expect(writes(calls).map((c) => c.key)).toEqual([`PATCH ${BASE}/product-grants/g-easy_helpdesk`]); // no product grant was created
  });

  it("hides a visible product the same way", async () => {
    const { calls } = await open({ [`PATCH ${BASE}/product-grants/g-easy_crm`]: [ok({ id: "g-easy_crm", trial_allowed: true, portal_visible: false })] });
    fireEvent.click(screen.getByRole("checkbox", { name: "Visible in portal: Easy CRM" }));
    await screen.findByText("Easy CRM is now hidden from the portal.");
    expect(calls.find((c) => c.key.startsWith("PATCH "))!.body).toEqual({ portal_visible: false });
  });

  it("asks for a second factor here, and puts the box back if it is not completed", async () => {
    await open({ [`PATCH ${BASE}/product-grants/g-easy_crm`]: [STEP_UP] });
    const box = screen.getByRole("checkbox", { name: "Visible in portal: Easy CRM" }) as HTMLInputElement;
    fireEvent.click(box);
    await screen.findByLabelText(/needs a recent verification/);
    await waitFor(() => expect((screen.getByRole("checkbox", { name: "Visible in portal: Easy CRM" }) as HTMLInputElement).checked).toBe(true));
  });

  it("says so when the catalog has no products", async () => {
    await open({}, { products: [] });
    expect(screen.getByText(/no products in the catalog yet/)).toBeInTheDocument();
  });
});

// --- step 5 -----------------------------------------------------------------------------------------------------

describe("step 5: customer portal", () => {
  async function open(extra: Record<string, Reply[]> = {}, over: Record<string, unknown> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup(over))], [BRAND_PUT]: [ok(brand({ version: 4 }))], ...extra });
    const view = renderSetup();
    await toStep(5);
    return { calls, ...view };
  }

  it("names the portal after the brand, and says plain text only", async () => {
    const { container } = await open();
    expect(screen.getByText(/The portal name is the brand name/)).toHaveTextContent("Acme Brand");
    expect(screen.getByText(/Plain text only/)).toHaveTextContent(/Angle brackets \(< and >\) are refused/);
    expect(screen.getByLabelText(/^Welcome message/)).toHaveAttribute("maxLength", "280");
    expect(screen.getByLabelText(/^Footer text/)).toHaveAttribute("maxLength", "160");
    await expectNoViolations(container);
  });

  it("saves only the two fields, sending the stored brand fields back unchanged", async () => {
    const { calls } = await open({}, { brand: brand({ support_url: "https://help.acme.example", accent_color: "#0b6e4f", theme: { accent: "#0b6e4f" } }) });
    type(/^Welcome message/, "Welcome to Acme");
    type(/^Footer text/, "Acme Ltd");
    advance();
    await heading(6);
    expect(putBody(calls)).toEqual({
      brand_name: "Acme Brand",
      accent_color: "#0b6e4f",
      support_email: "help@acme.example",
      support_url: "https://help.acme.example",
      theme: { accent: "#0b6e4f" },
      portal: { welcome_message: "Welcome to Acme", footer_text: "Acme Ltd" },
      expected_version: 3,
    });
  });

  it("refuses angle brackets and line breaks locally, naming the field", async () => {
    const { calls } = await open();
    type(/^Welcome message/, "<script>alert(1)</script>");
    advance();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/welcome message: use plain text only/i);
    expect(screen.getByLabelText(/^Welcome message/)).toHaveAccessibleDescription(/plain text only/);
    type(/^Welcome message/, "line one\nline two");
    advance();
    expect(await screen.findByRole("alert")).toHaveTextContent(/single line|on one line/);
    expect(writes(calls)).toEqual([]);
  });

  it("shows the server's refusal on the field when it refuses what was typed", async () => {
    await open({ [BRAND_PUT]: [{ status: 400, body: { code: "partner_validation_error", detail: "Use plain text only: no < or > characters.", field: "portal.footer_text" } }] });
    type(/^Footer text/, "Looks fine locally");
    advance();
    await waitFor(() => expect(screen.getByLabelText(/^Footer text/)).toBeInvalid());
    expect(screen.getByLabelText(/^Footer text/)).toHaveAccessibleDescription(/Use plain text only: no < or > characters\./);
  });

});

describe("steps 5, 6 and 8 need a brand to save into", () => {
  // The brand is made on step 2, so these steps are normally reached with one. Each test here reaches one of
  // them the only way a person could without: a re-read that still reports no brand (the server has not
  // caught up, or another administrator removed it).
  const NONE = setup({ brand: null, checks: { brand_saved: false, logo_saved: false, domain_requested: false } });

  function expectBlocked() {
    expect(screen.getByText(/Save the brand identity first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save & continue" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument(); // no field to type into
  }

  it("step 5 (customer portal) is blocked after an identity save that the re-read does not show", async () => {
    const calls = mockApi({ [SETUP]: [ok(NONE)], [BRAND_PUT]: [ok(brand({ version: 1 }))] });
    renderSetup();
    await heading(2);
    type(/^Brand name/, "Acme");
    advance();
    await toStep(5, 3);
    expectBlocked();
    expect(calls.filter((c) => c.key === BRAND_PUT)).toHaveLength(1); // only the identity save
    fireEvent.click(screen.getByRole("button", { name: "Go to Brand identity" }));
    await heading(2);
  });

  it("step 6 (communication) is blocked when the re-read after step 5 shows no brand", async () => {
    mockApi({ [SETUP]: [ok(setup()), ok(NONE)], [BRAND_PUT]: [ok(brand({ version: 4 }))] });
    renderSetup();
    await toStep(5);
    type(/^Welcome message/, "Hello");
    advance();
    await heading(6);
    expectBlocked();
  });

  it("step 8 (login experience) is blocked when the re-read after a domain request shows no brand", async () => {
    mockApi({
      [SETUP]: [ok(setup()), ok({ ...NONE, domain: domain("requested"), branding_on_custom_domain: "pending" })],
      [`POST ${BASE}/domains/request`]: [{ status: 201, body: domain("requested") }],
    });
    renderSetup();
    await toStep(7);
    type(/^Host name to request/, "app.acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    await screen.findByText("Domain requested. Publish the DNS record, then check it.");
    advance();
    await heading(8);
    expectBlocked();
  });
});

// --- step 6 -----------------------------------------------------------------------------------------------------

describe("step 6: communication", () => {
  async function open(extra: Record<string, Reply[]> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup())], [BRAND_PUT]: [ok(brand({ version: 4 }))], ...extra });
    const view = renderSetup();
    await toStep(6);
    return { calls, ...view };
  }

  it("is honest: one email exists, its From address cannot change, and the rest are not available yet", async () => {
    const { container } = await open();
    expect(screen.getByText(/one email Workeasy360 sends in a partner.s name/)).toHaveTextContent(/always sent from Workeasy360.s own address, and that cannot be changed/);
    const notYet = screen.getByRole("region", { name: "Emails that do not exist yet" });
    const items = within(notYet).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "Customer welcome email: Not available yet",
      "Trial emails: Not available yet",
      "Subscription emails: Not available yet",
    ]);
    // Nothing is configurable for them, and there is no field for a From address.
    expect(within(notYet).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(notYet).queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/from address|from email/i)).not.toBeInTheDocument();
    expect(screen.getAllByRole("textbox").map((el) => (el as HTMLInputElement).labels?.[0]?.textContent)).toEqual([
      "Sender display name (optional)",
      "Reply-to address (optional)",
    ]);
    await expectNoViolations(container);
  });

  it("saves the sender name, the reply-to address and the invitation choice", async () => {
    const { calls } = await open();
    type(/^Sender display name/, "Acme Support");
    type(/^Reply-to address/, "reply@acme.example");
    fireEvent.click(screen.getByLabelText("Use my brand on invitation emails"));
    advance();
    await heading(7);
    expect((putBody(calls) as { portal: unknown }).portal).toEqual({ sender_name: "Acme Support", reply_to_email: "reply@acme.example", use_for_invitations: true });
  });

  it("refuses a bad reply-to address, and a sender name with angle brackets", async () => {
    await open();
    type(/^Reply-to address/, "nobody");
    type(/^Sender display name/, "A <b>");
    advance();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/valid reply-to address/);
    expect(alert).toHaveTextContent(/sender display name: use plain text only/i);
  });
});

// --- step 7 -----------------------------------------------------------------------------------------------------

describe("step 7: custom domain", () => {
  const REQUEST = `POST ${BASE}/domains/request`;

  async function open(routes: Record<string, Reply[]>, over: Record<string, unknown> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup(over))], ...routes });
    const view = renderSetup();
    await toStep(7);
    return { calls, ...view };
  }

  it("is optional: continue past it with no domain", async () => {
    const { calls } = await open({});
    expect(screen.getByText(/A custom domain is optional/)).toBeInTheDocument();
    advance();
    await heading(8);
    expect(writes(calls)).toEqual([]);
  });

  it("refuses a URL or an address that is not a host name before asking", async () => {
    const { calls } = await open({});
    for (const bad of ["https://app.acme.example", "acme", "app.acme.example/path", "10.0.0.1"]) {
      type(/^Host name to request/, bad);
      fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
      expect(await screen.findByText(/Enter a host name such as app\.example\.com/)).toBeInTheDocument();
    }
    expect(writes(calls)).toEqual([]);
  });

  it("requests a host name, then shows the TXT record as Type, Host and Value, each with a Copy button", async () => {
    const { calls } = await open(
      { [REQUEST]: [{ status: 201, body: domain("requested") }], [SETUP]: [ok(setup()), ok(setup({ domain: domain("requested"), branding_on_custom_domain: "pending" }))] },
    );
    type(/^Host name to request/, "App.Acme.Example");
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    await screen.findByText("Domain requested. Publish the DNS record, then check it.");
    expect(calls.find((c) => c.key === REQUEST)!.body).toEqual({ hostname: "app.acme.example" });
    expect(writes(calls).map((c) => c.key)).toEqual([REQUEST]); // nothing else rode along
    expect(await screen.findByText("TXT")).toBeInTheDocument();
    expect(screen.getByText("_w360-verify.app.acme.example")).toBeInTheDocument();
    expect(screen.getByText("w360-verify=abc123")).toBeInTheDocument();
    for (const [label, value] of [["Type", "TXT"], ["Host", "_w360-verify.app.acme.example"], ["Value", "w360-verify=abc123"]]) {
      const row = screen.getByText(label, { selector: "dt" }).closest("div")!;
      expect(row).toHaveTextContent(value);
      expect(within(row).getByRole("button", { name: `Copy ${label.toLowerCase()}` })).toBeInTheDocument();
    }
  });

  it("shows the status lifecycle, marking where the domain is", async () => {
    await open({}, { domain: domain("verification_pending"), branding_on_custom_domain: "pending" });
    const list = screen.getByRole("list", { name: "Domain status" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Requested", "Verification pending", "Verified", "Active"]);
    expect(within(list).getByText("Verification pending")).toHaveAttribute("aria-current", "step");
  });

  it("copies each value to the clipboard and announces Copied in a live region", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await open({}, { domain: domain("requested"), branding_on_custom_domain: "pending" });
    fireEvent.click(screen.getByRole("button", { name: "Copy host" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("_w360-verify.app.acme.example"));
    const live = await screen.findByText("Copied Host");
    expect(live.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
    fireEvent.click(screen.getByRole("button", { name: "Copy value" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith("w360-verify=abc123"));
    fireEvent.click(screen.getByRole("button", { name: "Copy type" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith("TXT"));
  });

  it("falls back to telling the person to copy by hand when the clipboard is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await open({}, { domain: domain("requested"), branding_on_custom_domain: "pending" });
    fireEvent.click(screen.getByRole("button", { name: "Copy value" }));
    expect(await screen.findByText(/Copying is not available here/)).toBeInTheDocument();
    expect(screen.getByText("w360-verify=abc123")).toBeInTheDocument(); // still there to select
  });

  it("falls back the same way when the clipboard refuses", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) }, configurable: true });
    await open({}, { domain: domain("requested"), branding_on_custom_domain: "pending" });
    fireEvent.click(screen.getByRole("button", { name: "Copy host" }));
    expect(await screen.findByText(/Copying is not available here/)).toBeInTheDocument();
  });

  it("checks verification as its own action and explains a failed check in words", async () => {
    const pending = domain("verification_pending", { failure_code: "record_not_found" });
    const { calls } = await open(
      {
        "POST /api/v1/admin/domains/d1/verify": [ok(pending)],
        [SETUP]: [ok(setup({ domain: domain("requested"), branding_on_custom_domain: "pending" })), ok(setup({ domain: pending, branding_on_custom_domain: "pending" }))],
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Check verification" }));
    await screen.findByText("Checked the DNS record.");
    expect(await screen.findByText(/was not found yet/)).toBeInTheDocument();
    expect(writes(calls).map((c) => c.key)).toEqual(["POST /api/v1/admin/domains/d1/verify"]);
  });

  it("never activates by itself: verified only offers Activate, and nothing is sent until it is pressed", async () => {
    const { calls } = await open(
      {
        "POST /api/v1/admin/domains/d1/activate": [ok(domain("active"))],
        [SETUP]: [ok(setup({ domain: domain("verified"), branding_on_custom_domain: "verified_not_active" })), ok(setup({ domain: domain("active"), branding_on_custom_domain: "active" }))],
      },
    );
    expect(screen.getByText(/Ownership is verified/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check verification" })).not.toBeInTheDocument();
    expect(writes(calls)).toEqual([]); // reaching verified activated nothing
    expect(screen.getByText(/Custom-domain branding:/).closest("p")).toHaveTextContent("Not active");
    fireEvent.click(screen.getByRole("button", { name: "Activate domain" }));
    await screen.findByText("The domain is active and now serves the brand.");
    expect(writes(calls).map((c) => c.key)).toEqual(["POST /api/v1/admin/domains/d1/activate"]);
    await waitFor(() => expect(screen.getByText(/Custom-domain branding:/).closest("p")).toHaveTextContent("Active"));
    expect(screen.getByRole("button", { name: "Disable domain" })).toBeInTheDocument();
  });

  it("cannot activate a domain that is not verified", async () => {
    await open({}, { domain: domain("verification_pending"), branding_on_custom_domain: "pending" });
    expect(screen.queryByRole("button", { name: "Activate domain" })).not.toBeInTheDocument();
  });

  it("cannot activate before the brand is saved", async () => {
    // Verification succeeds, and the re-read shows a domain that is verified but a setup with no brand.
    await open({
      "POST /api/v1/admin/domains/d1/verify": [ok(domain("verified"))],
      [SETUP]: [
        ok(setup({ domain: domain("verification_pending"), branding_on_custom_domain: "pending" })),
        ok(setup({ brand: null, domain: domain("verified"), branding_on_custom_domain: "verified_not_active" })),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "Check verification" }));
    await screen.findByText("Checked the DNS record.");
    expect(await screen.findByRole("button", { name: "Activate domain" })).toBeDisabled();
    expect(screen.getByText(/Save the brand identity first/)).toBeInTheDocument();
  });

  it("disables a live domain", async () => {
    const { calls } = await open(
      { "POST /api/v1/admin/domains/d1/disable": [ok(domain("disabled"))] },
      { domain: domain("active"), branding_on_custom_domain: "active" },
    );
    expect(screen.getByText(/Live: this domain shows the brand/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable domain" }));
    await screen.findByText(/no longer serves the brand/);
    expect(calls.some((c) => c.key === "POST /api/v1/admin/domains/d1/disable")).toBe(true);
  });

  it("asks for a second factor at the domain step", async () => {
    await open({ [REQUEST]: [STEP_UP, { status: 201, body: domain("requested") }], "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })] });
    type(/^Host name to request/, "app.acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    expect(await screen.findByLabelText(/needs a recent verification/)).toBeInTheDocument();
  });

  it("shows the server's problem on the host name field", async () => {
    await open({ [REQUEST]: [{ status: 400, body: { code: "partner_validation_error", detail: "A partner may hold at most 3 domains.", field: "hostname" } }] });
    type(/^Host name to request/, "app.acme.example");
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    await waitFor(() => expect(screen.getByLabelText(/^Host name to request/)).toBeInvalid());
    expect(screen.getByLabelText(/^Host name to request/)).toHaveAccessibleDescription("A partner may hold at most 3 domains.");
  });

  it("states honestly that sign-in is Workeasy360's, in the server's own words", async () => {
    const { container } = await open({}, { domain: domain("active"), branding_on_custom_domain: "active" });
    const box = screen.getByRole("heading", { name: "Custom domain and sign-in" }).closest("section")!;
    expect(box).toHaveTextContent("Custom-domain branding: Active");
    expect(box).toHaveTextContent("Authentication: Uses Workeasy360 secure sign-in.");
    expect(box).toHaveTextContent(AUTH.summary);
    expect(box.textContent).not.toMatch(/sign in on your (own )?domain|sign-in on the custom domain/i);
    await expectNoViolations(container);
  });

  it("does not claim secure sign-in when the server says something else", async () => {
    await open({}, { authentication: { mode: "something_else", custom_domain_sign_in: true, summary: "The server's own sentence." } });
    const box = screen.getByRole("heading", { name: "Custom domain and sign-in" }).closest("section")!;
    expect(box).toHaveTextContent("The server's own sentence.");
    expect(box).not.toHaveTextContent("Uses Workeasy360 secure sign-in");
  });
});

// --- step 8 -----------------------------------------------------------------------------------------------------

describe("step 8: login experience", () => {
  async function open(over: Record<string, unknown> = {}, extra: Record<string, Reply[]> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup(over))], [BRAND_PUT]: [ok(brand({ version: 4 }))], ...extra });
    const view = renderSetup();
    await toStep(8);
    return { calls, ...view };
  }

  it("saves the two sign-in choices, and previews the sign-in page", async () => {
    const { calls, container } = await open({ brand: brand({}, { welcome_message: "Welcome aboard", support_name: "Sam" }) });
    expect(screen.getByLabelText("Show the welcome message on the sign-in page")).toBeChecked();
    expect(screen.getByLabelText("Show support details on the sign-in page")).toBeChecked();
    const preview = screen.getByRole("region", { name: "Sign-in page preview" });
    expect(preview).toHaveTextContent("Welcome aboard");
    expect(preview).toHaveTextContent("Need help? Sam");
    fireEvent.click(screen.getByLabelText("Show the welcome message on the sign-in page"));
    fireEvent.click(screen.getByLabelText("Show support details on the sign-in page"));
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).not.toHaveTextContent("Welcome aboard");
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).not.toHaveTextContent("Need help?");
    advance();
    await heading(9);
    expect((putBody(calls) as { portal: unknown }).portal).toEqual({ login_show_welcome: false, login_show_support: false });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument(); // only the sign-in preview, no mode tabs
    await expectNoViolations(container);
  });

  it("says authentication remains Workeasy360's and there is no separate white-label sign-in", async () => {
    await open();
    expect(screen.getByText(/Authentication remains Workeasy360.s own/)).toHaveTextContent(/no separate white-label sign-in/);
    expect(screen.getByText(/Authentication remains Workeasy360.s own/)).toHaveTextContent(/secure page, which shows this brand/);
    expect(screen.getByText(AUTH.summary)).toBeInTheDocument();
  });
});

// --- step 9 -----------------------------------------------------------------------------------------------------

describe("step 9: reseller connection", () => {
  async function open(over: Record<string, unknown> = {}) {
    mockApi({ [SETUP]: [ok(setup(over))] });
    const view = renderSetup();
    await toStep(9);
    return view;
  }

  it("shows the derived labels as Enabled or Not enabled, from the server's labels", async () => {
    const { container } = await open();
    const list = screen.getByRole("list", { name: "What the partner can do" });
    const text = (name: string) => within(list).getByText(`${name}:`).closest("li")!.textContent;
    expect(text("Reseller")).toBe("Reseller:Enabled");
    expect(text("White-label")).toContain("Not enabled");
    expect(text("Affiliate")).toBe("Affiliate:Not enabled");
    expect(screen.getByText(/no kind of partner is stored/)).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows white-label Enabled when the server says the pair is held, and what a partial one still needs", async () => {
    await open({
      experience: [
        { key: "reseller", label: "Reseller" },
        { key: "white_label", label: "White-label" },
      ],
      enabled: true,
      missing_capabilities: [],
    });
    const list = screen.getByRole("list", { name: "What the partner can do" });
    expect(within(list).getByText("White-label:").closest("li")).toHaveTextContent("Enabled");
  });

  it("names the capability a half-enabled white-label partner still lacks", async () => {
    await open({ experience: [{ key: "white_label", label: "White-label" }], enabled: false, missing_capabilities: ["domain.manage"] });
    const item = within(screen.getByRole("list", { name: "What the partner can do" })).getByText("White-label:").closest("li")!;
    expect(item).toHaveTextContent("Not enabled");
    expect(item).toHaveTextContent("Still needs: manage custom domains.");
  });

  it("shows the customers and links to the partner page to change capabilities", async () => {
    await open({ customers: 12 });
    expect(screen.getByText("Customers:").closest("p")).toHaveTextContent("12");
    expect(screen.getByRole("link", { name: /Change this partner.s capabilities/ })).toHaveAttribute("href", `/admin/partners/${ID}`);
  });
});

// --- step 10 ----------------------------------------------------------------------------------------------------

describe("step 10: review", () => {
  const FULL = brand(
    { brand_name: "Acme Brand", accent_color: "#0b6e4f", support_email: "help@acme.example", support_url: "https://help.acme.example", logo_file_id: "f1", version: 6 },
    {
      short_name: "Acme",
      support_name: "Sam",
      support_phone: "+44 20 7946 0000",
      website_url: "https://acme.example",
      welcome_message: "Welcome aboard",
      footer_text: "Acme Ltd",
      sender_name: "Acme Support",
      reply_to_email: "reply@acme.example",
      use_for_invitations: true,
      login_show_welcome: false,
    },
  );

  async function open(over: Record<string, unknown> = {}, extra: Record<string, Reply[]> = {}) {
    const calls = mockApi({ [SETUP]: [ok(setup({ brand: FULL, ...over }))], ...extra });
    const view = renderSetup();
    await toStep(10);
    return { calls, ...view };
  }

  it("summarises the partner, brand, logo, colour, products, support, communication, domain and authentication", async () => {
    const { container } = await open({ domain: domain("verified"), branding_on_custom_domain: "verified_not_active" });
    const text = (name: string) => screen.getByRole("heading", { name }).closest("section")!;
    expect(text("Partner")).toHaveTextContent("Acme Partners");
    expect(text("Partner")).toHaveTextContent("Reseller");
    expect(text("Brand")).toHaveTextContent("Acme Brand");
    expect(text("Brand")).toHaveTextContent("Acme");
    expect(text("Brand")).toHaveTextContent("Saved"); // the logo
    expect(text("Brand")).toHaveTextContent("#0b6e4f");
    expect(text("Support")).toHaveTextContent("help@acme.example");
    expect(text("Support")).toHaveTextContent("+44 20 7946 0000");
    expect(text("Support")).toHaveTextContent("https://help.acme.example");
    expect(text("Support")).toHaveTextContent("https://acme.example");
    expect(text("Customer portal")).toHaveTextContent("Welcome aboard");
    expect(text("Customer portal")).toHaveTextContent("Easy CRM, Easy Project");
    expect(text("Communication")).toHaveTextContent("Acme Support");
    expect(text("Communication")).toHaveTextContent("Brand on invitation emailsYes");
    expect(text("Sign-in page and domain")).toHaveTextContent("Welcome message on sign-inNo");
    expect(text("Sign-in page and domain")).toHaveTextContent("app.acme.example");
    expect(text("Custom domain and sign-in")).toHaveTextContent("Uses Workeasy360 secure sign-in");
    await expectNoViolations(container);
  });

  it("treats the custom domain as optional", async () => {
    await open();
    expect(screen.getByText("None (optional)")).toBeInTheDocument();
  });

  it("has nothing to save, and says nothing is unsaved, when everything shown is already stored", async () => {
    await open();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(screen.queryByText(/not saved yet/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable white-label" })).toBeEnabled();
  });

  // The review shows what was saved. To show it differing from what the person changed, the re-read after the
  // step 8 save is made to answer with the brand as it was (the server has not caught up).
  async function openWithUnsavedLoginChoice(reread: Record<string, unknown>, extra: Record<string, Reply[]> = {}) {
    const calls = mockApi({
      [SETUP]: [ok(setup({ brand: FULL })), ok(reread)],
      [BRAND_PUT]: [ok(FULL)],
      ...extra,
    });
    renderSetup();
    await toStep(8);
    fireEvent.click(screen.getByLabelText("Show support details on the sign-in page"));
    advance();
    await toStep(10, 9);
    return calls;
  }

  it("Save draft stores the whole current brand and portal in one save, and enables nothing", async () => {
    const calls = await openWithUnsavedLoginChoice(setup({ brand: FULL }));
    expect(screen.getByText(/not saved yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Draft saved.")).toBeInTheDocument();
    const puts = calls.filter((c) => c.key === BRAND_PUT);
    expect(puts).toHaveLength(2); // the step 8 save, then this one
    expect(puts[1].body).toEqual({
      brand_name: "Acme Brand",
      accent_color: "#0b6e4f",
      support_email: "help@acme.example",
      support_url: "https://help.acme.example",
      theme: {},
      logo_file_id: "f1",
      portal: {
        short_name: "Acme",
        support_name: "Sam",
        support_phone: "+44 20 7946 0000",
        website_url: "https://acme.example",
        welcome_message: "Welcome aboard",
        footer_text: "Acme Ltd",
        sender_name: "Acme Support",
        reply_to_email: "reply@acme.example",
        use_for_invitations: true,
        login_show_welcome: false,
        login_show_support: false,
      },
      expected_version: 6,
    });
    expect(calls.some((c) => c.key.endsWith("/white-label/enable"))).toBe(false);
    expect(screen.getByRole("heading", { name: /Step 10 of 10/ })).toBeInTheDocument();
  });

  it("will not enable while what was reviewed differs from what is saved, and says to save the draft first", async () => {
    const calls = await openWithUnsavedLoginChoice(setup({ brand: FULL }));
    expect(screen.getByText(/Save draft first, so what you enable is what you reviewed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable white-label" })).toBeDisabled();
    expect(calls.some((c) => c.key.endsWith("/white-label/enable"))).toBe(false);
  });

  it("will not enable without a saved brand name", async () => {
    const calls = await openWithUnsavedLoginChoice(setup({ brand: null }));
    expect(screen.getByText("Save a brand name first (step 2).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable white-label" })).toBeDisabled();
    expect(calls.some((c) => c.key.endsWith("/white-label/enable"))).toBe(false);
  });

  it("enables white-label only after a confirm with a reason of at least ten characters, and records that reason", async () => {
    const { calls } = await open(
      {},
      {
        [`POST ${BASE}/white-label/enable`]: [ok({ granted: ["branding.manage", "domain.manage"] })],
        [SETUP]: [ok(setup({ brand: FULL })), ok(setup({ brand: FULL, enabled: true, missing_capabilities: [], experience: [{ key: "white_label", label: "White-label" }] }))],
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Enable white-label" }));
    expect(writes(calls)).toEqual([]); // opening the confirm sends nothing
    const reason = screen.getByLabelText(/Reason for/);
    const confirm = screen.getByRole("button", { name: "Confirm: enable white-label" });
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "too short" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(reason, { target: { value: "Launch for the Acme pilot" } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    expect(await screen.findByText("White-label is enabled.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${BASE}/white-label/enable`)!.body).toEqual({ reason: "Launch for the Acme pilot" });
    await waitFor(() => expect(screen.getByText(/White-label is enabled for this partner/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Enable white-label" })).not.toBeInTheDocument();
  });

  it("asks for a second factor when enabling, and enables once it is given", async () => {
    const { calls } = await open(
      {},
      {
        [`POST ${BASE}/white-label/enable`]: [STEP_UP, ok({ granted: ["branding.manage", "domain.manage"] })],
        "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Enable white-label" }));
    fireEvent.change(screen.getByLabelText(/Reason for/), { target: { value: "Launch for the Acme pilot" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm: enable white-label" }));
    fireEvent.change(await screen.findByLabelText(/needs a recent verification/), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("White-label is enabled.");
    expect(calls.filter((c) => c.key.endsWith("/white-label/enable"))).toHaveLength(2);
  });

  it("shows white-label as already enabled when the server says so", async () => {
    await open({ enabled: true, missing_capabilities: [], experience: [{ key: "white_label", label: "White-label" }] });
    expect(screen.getByText(/White-label is enabled for this partner/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable white-label" })).not.toBeInTheDocument();
  });
});

// --- nothing typed is ever drawn as markup -------------------------------------------------------------

describe("hostile text", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

  it("draws a partner's name, a brand and every portal field as text only", async () => {
    mockApi({
      [SETUP]: [
        ok(
          setup({
            partner: { id: ID, code: "acme", display_name: EVIL, status: "active", version: 1 },
            brand: brand({ brand_name: EVIL, support_email: "" }, { short_name: EVIL, welcome_message: EVIL, footer_text: EVIL, support_name: EVIL, sender_name: EVIL }),
            products: [product("evil", EVIL)],
          }),
        ),
      ],
    });
    const { container } = renderSetup();
    await heading(2);
    await toStep(10);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<img");
  });
});

// --- the preview ---------------------------------------------------------------------------------------------

describe("the portal preview is inert", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;
  const data = (over: Partial<PreviewData> = {}): PreviewData => ({
    brandName: "Acme",
    shortName: "",
    accent: "#1a56db",
    logoSrc: null,
    welcome: "",
    footer: "",
    supportName: "",
    supportEmail: "",
    supportPhone: "",
    supportUrl: "",
    websiteUrl: "",
    loginShowWelcome: true,
    loginShowSupport: true,
    products: [],
    ...over,
  });

  it("draws every typed value, in every mode, as text and never as markup", () => {
    const everything = data({
      brandName: EVIL,
      shortName: EVIL,
      welcome: EVIL,
      footer: EVIL,
      supportName: EVIL,
      supportEmail: EVIL,
      supportPhone: EVIL,
      supportUrl: EVIL,
      websiteUrl: EVIL,
      products: [EVIL],
    });
    const { container } = render(<PortalPreview data={everything} />);
    for (const tab of ["Login", "Dashboard", "Product launcher", "Header"]) {
      fireEvent.click(screen.getByRole("tab", { name: tab }));
      expect(container.querySelector("script")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("[onerror]")).toBeNull();
      expect(container.textContent).toContain("<img");
    }
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("sets the accent through the CSSOM only for a strict six-digit hex colour", () => {
    const { container, rerender } = render(<PortalPreview data={data({ accent: "#1a56db" })} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.getPropertyValue("--w360-brand")).toBe("#1a56db");
    rerender(<PortalPreview data={data({ accent: "red;background:url(//evil)" })} />);
    expect(root.style.getPropertyValue("--w360-brand")).toBe("");
    expect(container.innerHTML).not.toContain("evil");
    expect(container.querySelector("[style*='url']")).toBeNull();
    expect(root.getAttribute("style") ?? "").toBe("");
  });

  it("draws a logo only from the source it was given, and a plain mark otherwise", () => {
    const { container, rerender } = render(<PortalPreview data={data({ logoSrc: "data:image/png;base64,AAAA" })} />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,AAAA");
    rerender(<PortalPreview data={data({ logoSrc: null })} />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("is a labelled group whose mock fields are hidden from assistive technology", () => {
    render(<PortalPreview data={data()} />);
    expect(screen.getByRole("group", { name: /Preview - nothing here is saved or served/ })).toBeInTheDocument();
    expect(screen.getByText("Password").closest("[aria-hidden='true']")).not.toBeNull();
    expect(screen.getByText("Sign in").closest("[aria-hidden='true']")).not.toBeNull();
  });

  it("draws one mode without tabs when asked, and follows the two sign-in choices", () => {
    const welcome = data({ welcome: "Welcome aboard", supportName: "Sam" });
    const { rerender } = render(<PortalPreview data={welcome} only="login" />);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toHaveTextContent("Welcome aboard");
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toHaveTextContent("Need help? Sam");
    rerender(<PortalPreview data={{ ...welcome, loginShowWelcome: false, loginShowSupport: false }} only="login" />);
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).not.toHaveTextContent("Welcome aboard");
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).not.toHaveTextContent("Need help?");
  });

  it("uses the short name in the header modes and the brand name on the sign-in page", () => {
    render(<PortalPreview data={data({ brandName: "Acme Business Cloud", shortName: "Acme" })} />);
    expect(screen.getByRole("region", { name: "Sign-in page preview" })).toHaveTextContent("Acme Business Cloud");
    fireEvent.click(screen.getByRole("tab", { name: "Header" }));
    expect(screen.getByRole("region", { name: "Header preview" })).toHaveTextContent("Acme");
    expect(screen.getByRole("region", { name: "Header preview" })).not.toHaveTextContent("Acme Business Cloud");
  });
});
