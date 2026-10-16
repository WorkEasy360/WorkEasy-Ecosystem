import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PartnerShell } from "./ControlShell";
import { PortalWhiteLabelPage, WhiteLabelWizard, currentDomain } from "./WhiteLabelWizard";
import { WhiteLabelList } from "./WhiteLabelList";
import { LivePreview } from "./BrandPreview";
import { AdminContextForTest, SIGNED_IN, expectNoViolations, mockApi, ok, type Call, type Reply } from "./testSupport";
import { staffWhiteLabel } from "@/lib/api/partnerConsole";

/**
 * Phase 10d: the white-label list and the partner's own five-step wizard. What
 * the backend cannot enforce for the browser: the preview stores nothing, each
 * step is its own action (so a second-factor prompt lands on the step that needs
 * one), activation never happens by itself, and nothing a partner typed is ever
 * rendered as markup.
 *
 * The wizard takes its calls as a ``WhiteLabelApi``, so these tests drive it
 * through the staff address table (the same eight calls, easiest to route); the
 * partner's own addresses are covered by "the partner portal" below. The staff
 * ten-step setup has its own tests in ``WhiteLabelSetup.test.tsx``.
 */

let query = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(query),
  usePathname: () => "/admin/partners/white-label",
}));

const ID = "p1";
const BASE = `/api/v1/admin/partners/${ID}`;
const MANAGE = ["platform.staff_partner.view", "platform.staff_partner.manage"];
const STEP_UP: Reply = { status: 401, body: { code: "step_up_required", detail: "Verify again." } };

const BRAND = {
  brand_name: "Acme Brand",
  logo_file_id: null,
  accent_color: "#1a56db",
  theme: {},
  support_email: "help@acme.example",
  support_url: "",
  version: 0,
  updated_at: "2026-02-01T00:00:00Z",
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

const writes = (calls: Call[]) => calls.filter((c) => /^(POST|PUT|PATCH|DELETE) /.test(c.key));
const next = () => fireEvent.click(screen.getByRole("button", { name: "Next" }));

function wizard() {
  return render(
    <AdminContextForTest permissions={MANAGE}>
      <WhiteLabelWizard api={staffWhiteLabel(ID)} partnerName="Acme Partners" />
    </AdminContextForTest>,
  );
}

async function heading(step: number) {
  return screen.findByRole("heading", { name: new RegExp(`Step ${step} of 5`) });
}

async function fillBrand(name = "Acme Brand") {
  fireEvent.change(await screen.findByLabelText("Brand name"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/Accent colour/), { target: { value: "#1a56db" } });
}

beforeEach(() => {
  vi.restoreAllMocks();
  query = "";
});

describe("currentDomain", () => {
  it("picks the most advanced live request and ignores closed ones", () => {
    const rows = [domain("failed", { id: "f" }), domain("requested", { id: "r" }), domain("verified", { id: "v" }), domain("disabled", { id: "x" })];
    expect(currentDomain(rows as never)?.id).toBe("v");
    expect(currentDomain([domain("failed"), domain("disabled")] as never)).toBeNull();
  });
});

describe("the partner's own wizard", () => {
  it("stays the five steps a partner can take itself, with none of the staff-only ones", async () => {
    mockApi({ [`GET ${BASE}/brand`]: [ok({ brand: null })], [`GET ${BASE}/domains`]: [ok({ results: [] })] });
    const { container } = wizard();
    await heading(1);
    const steps = screen.getByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem").map((li) => li.textContent?.replace(/^\d+\.\s*/, ""))).toEqual([
      "Brand identity",
      "Preview",
      "Domain request",
      "DNS verification",
      "Activation",
    ]);
    // Products, portal words, communication and enabling white-label are staff acts: not here.
    expect(container.textContent).not.toMatch(/Enable white-label|Visible in portal|Welcome message|Sender display name/);
  });
});

describe("brand identity and preview", () => {
  it("checks the form before moving on and offers only what the brand model has", async () => {
    mockApi({ [`GET ${BASE}/brand`]: [ok({ brand: null })], [`GET ${BASE}/domains`]: [ok({ results: [] })] });
    wizard();
    await heading(1);
    next();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Enter the brand name/);
    fireEvent.change(screen.getByLabelText("Brand name"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Accent colour/), { target: { value: "red" } });
    fireEvent.change(screen.getByLabelText(/Support page/), { target: { value: "http://insecure.example.com" } });
    next();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/six-digit hex colour/);
    expect(alert).toHaveTextContent(/https:\/\//);
    expect(screen.getByRole("heading", { name: /Step 1 of 5/ })).toBeInTheDocument();
    const labels = screen.getAllByRole("textbox").map((el) => (el as HTMLInputElement).labels?.[0]?.textContent ?? "");
    expect(labels.sort()).toEqual(["Accent colour (hex, e.g. #1a56db)", "Brand name", "Support email (optional)", "Support page (https, optional)"].sort());
  });

  it("accepts only PNG or JPEG under 512 KB", async () => {
    mockApi({ [`GET ${BASE}/brand`]: [ok({ brand: null })], [`GET ${BASE}/domains`]: [ok({ results: [] })] });
    wizard();
    const input = (await screen.findByLabelText(/Logo \(PNG or JPEG/)) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/PNG or JPEG image\. SVG is not accepted/);
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(600 * 1024)], "big.png", { type: "image/png" })] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/512 KB or smaller/));
    expect(input.accept).toBe("image/png,image/jpeg");
  });

  it("shows the preview from the draft and saves nothing until it is approved", async () => {
    const calls = mockApi({ [`GET ${BASE}/brand`]: [ok({ brand: null })], [`GET ${BASE}/domains`]: [ok({ results: [] })] });
    const { container } = wizard();
    await fillBrand();
    next();
    await heading(2);
    for (const name of ["Sign-in page preview", "Header preview", "Launcher preview"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(container.textContent).toContain("Acme Brand");
    expect(writes(calls)).toEqual([]); // looking is not saving
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled(); // not saved yet
    await expectNoViolations(container);
  });

  it("saves the brand (logo first) as its own action, then unlocks the next step", async () => {
    const calls = mockApi({
      [`GET ${BASE}/brand`]: [ok({ brand: null }), ok({ brand: { ...BRAND, logo_file_id: "f1" } })],
      [`GET ${BASE}/domains`]: [ok({ results: [] })],
      [`POST ${BASE}/brand/logo`]: [{ status: 201, body: { logo_file_id: "f1" } }],
      [`PUT ${BASE}/brand`]: [ok({ ...BRAND, logo_file_id: "f1" })],
    });
    wizard();
    await fillBrand();
    const input = screen.getByLabelText(/Logo \(PNG or JPEG/) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(64)], "logo.png", { type: "image/png" })] } });
    next();
    await heading(2);
    fireEvent.click(screen.getByRole("button", { name: "Save brand" }));
    await screen.findByText("Brand saved.");
    const keys = writes(calls).map((c) => c.key);
    expect(keys).toEqual([`POST ${BASE}/brand/logo`, `PUT ${BASE}/brand`]);
    expect(calls.find((c) => c.key === `POST ${BASE}/brand/logo`)!.headers["content-type"]).toBe("image/png");
    expect(calls.find((c) => c.key === `PUT ${BASE}/brand`)!.body).toEqual({
      brand_name: "Acme Brand",
      accent_color: "#1a56db",
      support_email: "",
      support_url: "",
      theme: {},
      logo_file_id: "f1",
      expected_version: undefined,
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  });

  it("sends the version of an existing brand so a stale edit is refused, not merged", async () => {
    const calls = mockApi({
      [`GET ${BASE}/brand`]: [ok({ brand: { ...BRAND, version: 4 } })],
      [`GET ${BASE}/domains`]: [ok({ results: [] })],
      [`PUT ${BASE}/brand`]: [{ status: 409, body: { code: "concurrent_modification", detail: "The brand changed since you read it. Re-read it and retry." } }],
    });
    wizard();
    fireEvent.change(await screen.findByLabelText("Brand name"), { target: { value: "Renamed" } });
    next();
    await heading(2);
    fireEvent.click(screen.getByRole("button", { name: "Save brand" }));
    expect(await screen.findByText(/The brand changed since you read it/)).toBeInTheDocument();
    expect((calls.find((c) => c.key === `PUT ${BASE}/brand`)!.body as { expected_version: number }).expected_version).toBe(4);
  });
});

describe("the preview is inert", () => {
  const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

  it("renders hostile text as text and no markup", () => {
    const { container } = render(<LivePreview brandName={EVIL} accent="#1a56db" logoSrc={null} supportEmail={EVIL} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.textContent).toContain("<img");
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("sets the accent through the CSSOM only for a strict hex colour", () => {
    const { container, rerender } = render(<LivePreview brandName="A" accent="#1a56db" logoSrc={null} supportEmail="" />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.getPropertyValue("--w360-brand")).toBe("#1a56db");
    rerender(<LivePreview brandName="A" accent={"red;background:url(//evil)"} logoSrc={null} supportEmail="" />);
    expect(root.style.getPropertyValue("--w360-brand")).toBe("");
    expect(container.innerHTML).not.toContain("evil");
    expect(container.querySelector("[style*='url']")).toBeNull();
  });

  it("draws a logo only from the source it was given", () => {
    const { container } = render(<LivePreview brandName="A" accent="" logoSrc="data:image/png;base64,AAAA" supportEmail="" />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,AAAA");
  });
});

describe("the domain steps", () => {
  const saved = { [`GET ${BASE}/brand`]: [ok({ brand: BRAND })] };

  async function toDomainStep() {
    await screen.findByLabelText("Brand name");
    next(); // brand -> preview (brand is saved and unchanged)
    await heading(2);
    next(); // preview -> domain
    await heading(3);
  }

  it("requests a domain as a separate action from saving the brand", async () => {
    const calls = mockApi({
      ...saved,
      [`GET ${BASE}/domains`]: [ok({ results: [] }), ok({ results: [domain("requested")] })],
      [`POST ${BASE}/domains/request`]: [{ status: 201, body: domain("requested") }],
    });
    wizard();
    await toDomainStep();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Hostname/), { target: { value: "App.Acme.Example" } });
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    await screen.findByText("Domain requested. Publish the DNS record, then check it.");
    expect(writes(calls).map((c) => c.key)).toEqual([`POST ${BASE}/domains/request`]); // no brand write rode along
    expect(calls.find((c) => c.key.endsWith("/domains/request"))!.body).toEqual({ hostname: "app.acme.example" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  });

  it("asks for a second factor at the domain step, not at the brand step, and does not repeat what was done", async () => {
    const calls = mockApi({
      [`GET ${BASE}/brand`]: [ok({ brand: null }), ok({ brand: BRAND })],
      [`GET ${BASE}/domains`]: [ok({ results: [] })],
      [`PUT ${BASE}/brand`]: [ok(BRAND)],
      [`POST ${BASE}/domains/request`]: [STEP_UP, { status: 201, body: domain("requested") }],
      "POST /api/v1/auth/mfa/step-up": [ok({ aal: 2 })],
    });
    wizard();
    await fillBrand();
    next();
    await heading(2);
    fireEvent.click(screen.getByRole("button", { name: "Save brand" })); // no prompt: nothing dangerous
    await screen.findByText("Brand saved.");
    expect(screen.queryByLabelText(/needs a recent verification/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
    next();
    await heading(3);
    fireEvent.change(screen.getByLabelText(/Hostname/), { target: { value: "app.acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Request domain" }));
    const code = await screen.findByLabelText(/needs a recent verification/);
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText("Domain requested. Publish the DNS record, then check it.");
    const keys = writes(calls).map((c) => c.key);
    expect(keys.filter((k) => k === `PUT ${BASE}/brand`)).toHaveLength(1);
    expect(keys.filter((k) => k === `POST ${BASE}/domains/request`)).toHaveLength(2); // refused once, then done
  });

  it("shows the DNS record to publish and what a failed check means", async () => {
    const calls = mockApi({
      ...saved,
      [`GET ${BASE}/domains`]: [ok({ results: [domain("requested")] }), ok({ results: [domain("verification_pending", { failure_code: "record_not_found" })] })],
      "POST /api/v1/admin/domains/d1/verify": [ok(domain("verification_pending", { failure_code: "record_not_found" }))],
    });
    const { container } = wizard();
    await toDomainStep();
    next();
    await heading(4);
    expect(screen.getByText("_w360-verify.app.acme.example")).toBeInTheDocument();
    expect(screen.getByText("w360-verify=abc123")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await expectNoViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Check verification" }));
    await screen.findByText("Checked the DNS record.");
    expect(await screen.findByText(/was not found yet/)).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/admin/domains/d1/verify")).toBe(true);
  });

  it("does not activate by itself: verified only unlocks the activation step", async () => {
    const calls = mockApi({
      ...saved,
      [`GET ${BASE}/domains`]: [ok({ results: [domain("verified")] })],
      "POST /api/v1/admin/domains/d1/activate": [ok(domain("active"))],
    });
    wizard();
    await screen.findByLabelText("Brand name");
    next();
    await heading(2);
    next();
    await heading(3);
    next();
    await heading(4);
    expect(screen.getByText(/Ownership is verified/)).toBeInTheDocument();
    next();
    await heading(5);
    expect(writes(calls)).toEqual([]); // nothing was activated on the way
    fireEvent.click(screen.getByRole("button", { name: "Activate the domain" }));
    await screen.findByText(/The domain is active and now serves the brand\./);
    expect(writes(calls).map((c) => c.key)).toEqual(["POST /api/v1/admin/domains/d1/activate"]);
  });

  it("cannot activate a domain that is not verified", async () => {
    mockApi({ ...saved, [`GET ${BASE}/domains`]: [ok({ results: [domain("verification_pending")] })] });
    wizard();
    await screen.findByLabelText("Brand name");
    next();
    await heading(2);
    next();
    await heading(3);
    // The next step is locked until the server reports verification, so the
    // activation step is unreachable by the buttons.
    next();
    await heading(4);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Activate the domain" })).not.toBeInTheDocument();
  });

  it("shows a live domain and can disable it", async () => {
    const calls = mockApi({
      ...saved,
      [`GET ${BASE}/domains`]: [ok({ results: [domain("active")] })],
      "POST /api/v1/admin/domains/d1/disable": [ok(domain("disabled"))],
    });
    wizard();
    await screen.findByLabelText("Brand name");
    next();
    await heading(2);
    next();
    await heading(3);
    next();
    await heading(4);
    next();
    await heading(5);
    expect(screen.getByText("Live: this domain serves the brand.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable the domain" }));
    await screen.findByText(/no longer serves the brand/);
    expect(calls.some((c) => c.key === "POST /api/v1/admin/domains/d1/disable")).toBe(true);
  });
});

describe("the partner portal", () => {
  it("runs the same wizard against the partner's own endpoints", async () => {
    const PORTAL = `/api/v1/partner/${ID}`;
    const calls = mockApi({
      "GET /api/v1/auth/session": [SIGNED_IN],
      "GET /api/v1/partner/portal": [ok({ partners: [{ partner_id: ID, code: "acme", display_name: "Acme Partners", status: "active", role: "admin", is_admin: true, capabilities: ["branding.manage", "domain.manage"], permissions: ["platform.partner_branding.view", "platform.partner_branding.manage", "platform.partner_domain.view", "platform.partner_domain.manage", "platform.partner_dashboard.view"] }] })],
      [`GET ${PORTAL}/branding`]: [ok({ brand: null })],
      [`GET ${PORTAL}/domains`]: [ok({ results: [] })],
    });
    const { PortalWhiteLabelScreen } = await import("./WhiteLabelWizard");
    render(
      <PartnerShell partnerId={ID}>
        <PortalWhiteLabelScreen />
      </PartnerShell>,
    );
    await screen.findByLabelText("Brand name");
    expect(calls.some((c) => c.key === `GET ${PORTAL}/branding`)).toBe(true);
    expect(calls.some((c) => c.url.includes("/api/v1/admin/"))).toBe(false); // never the staff endpoints
    expect(screen.getByRole("link", { name: "Set up white-label" })).toHaveAttribute("href", `/partner/${ID}/white-label`);
  });

  it("says when white-label is not enabled or the partner is not active", () => {
    const { rerender } = render(<PortalWhiteLabelPage partnerId={ID} partnerName="Acme" capabilities={["branding.manage"]} active />);
    expect(screen.getByRole("status")).toHaveTextContent(/not enabled for your account/);
    rerender(<PortalWhiteLabelPage partnerId={ID} partnerName="Acme" capabilities={["branding.manage", "domain.manage"]} active={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(/not active/);
  });
});

describe("the white-label list", () => {
  const row = (overrides: Record<string, unknown> = {}) => ({
    id: ID,
    code: "acme",
    display_name: "Acme Partners",
    status: "active",
    experience: [{ key: "white_label", label: "White-label" }],
    brand: { configured: true, name: "Acme Brand", has_logo: true },
    domain: { hostname: "app.acme.example", status: "verified" },
    customers: 3,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  });
  const routes = (rows: unknown[]) => ({
    "GET /api/v1/admin/partners/white-label": [ok({ results: rows, next_cursor: null, has_more: false })],
    "GET /api/v1/admin/partners/resellers": [ok({ results: [{ id: "p9", display_name: "Nine Co" }], next_cursor: null, has_more: false })],
  });

  it("shows the seven columns the brief lists, derived from capabilities", async () => {
    mockApi(routes([row()]));
    const { container } = render(
      <AdminContextForTest permissions={MANAGE}>
        <WhiteLabelList />
      </AdminContextForTest>,
    );
    const table = await screen.findByRole("table", { name: "White-label partners" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Brand",
      "Partner",
      "Brand status",
      "Domain",
      "Domain status",
      "Customers",
      "Updated",
      "",
    ]);
    expect(within(table).getByText("Acme Brand")).toBeInTheDocument();
    expect(within(table).getByText("app.acme.example")).toBeInTheDocument();
    expect(within(table).getByText("White-label")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("sends its filters to the server, starting from the address", async () => {
    query = "domain_status=awaiting";
    const calls = mockApi(routes([]));
    render(
      <AdminContextForTest permissions={MANAGE}>
        <WhiteLabelList />
      </AdminContextForTest>,
    );
    await screen.findByText(/No partner matches/);
    expect(calls.find((c) => c.key === "GET /api/v1/admin/partners/white-label")!.url).toContain("domain_status=awaiting");
    fireEvent.change(screen.getByLabelText("Brand"), { target: { value: "none" } });
    await waitFor(() => expect(calls.filter((c) => c.key === "GET /api/v1/admin/partners/white-label").at(-1)!.url).toContain("brand=none"));
  });

  it("starts the ten-step setup, which chooses the partner itself, and opens a listed partner pre-selected", async () => {
    mockApi(routes([row()]));
    render(
      <AdminContextForTest permissions={MANAGE}>
        <WhiteLabelList />
      </AdminContextForTest>,
    );
    // The call to action under the list: a link into the wizard's first step (below).
    // Each row opens the same wizard with that partner already chosen.
    expect(await screen.findByRole("link", { name: "Set up white-label for Acme Partners" })).toHaveAttribute("href", `/admin/partners/white-label/${ID}`);
    expect(screen.getByRole("link", { name: "Set up white-label" })).toHaveAttribute("href", "/admin/partners/white-label/new");
  });

  it("offers no setup to someone who may only view partners", async () => {
    mockApi(routes([row()]));
    render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <WhiteLabelList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "White-label partners" });
    expect(screen.queryByRole("link", { name: /Set up white-label/ })).not.toBeInTheDocument();
  });

  it("renders hostile brand and partner names as text", async () => {
    const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;
    mockApi(routes([row({ display_name: EVIL, brand: { configured: true, name: EVIL, has_logo: false }, domain: { hostname: EVIL, status: "active" } })]));
    const { container } = render(
      <AdminContextForTest permissions={["platform.staff_partner.view"]}>
        <WhiteLabelList />
      </AdminContextForTest>,
    );
    await screen.findByRole("table", { name: "White-label partners" });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container.textContent).toContain("<img");
  });
});
