import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api/client";
import type { ProductDomain } from "@/lib/api/controlPlane";
import { ProductDetail } from "./ProductDetail";
import { domainAction, outcomeHint } from "./SsoSettings";
import { backchannelUriProblem, domainBlockOf, hostnameProblem } from "./model";
import { statusLabel, toneOf } from "../kit";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "../testSupport";

/**
 * Super Admin -> Products -> a product -> Integration -> single sign-on
 * (ADR-069, ADR-070): the readiness verdict and its checks, the hostnames whose
 * control must be proven (with the TXT record to publish, Verify and Add
 * hostname for managers only), the back-channel logout URL (edited with the
 * registry's rules) and its delivery status, loaded on demand. A refusal
 * because a hostname is not proven reaches the person beside Domains.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams("tab=integration"),
  usePathname: () => "/admin/products/id-future_product",
}));

const ADMIN = "/api/v1/admin";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_integration.manage"];
const VIEW = ["platform.staff_catalog.view"];
const DETAIL = `GET ${ADMIN}/products/id-future_product`;
const BASE = `${ADMIN}/products/future_product/integration`;
const READINESS = `GET ${BASE}/readiness`;
const DOMAINS = `GET ${BASE}/domains`;
const ASK = `POST ${BASE}/domains`;
const VERIFY = `POST ${BASE}/domains/verify`;
const BACKCHANNEL = `GET ${BASE}/backchannel`;
const PATCH = `PATCH ${BASE}`;
const CALLBACK = "https://future.example.com/api/auth/callback";
const LOGOUT_URL = "https://future.example.com/api/auth/backchannel-logout";

function integration(overrides: Record<string, unknown> = {}) {
  return {
    client_id: "cid-123",
    status: "active",
    display_name: "Future Product",
    launch_url: "https://future.example.com/",
    health_url: "",
    redirect_uris: [CALLBACK],
    contact_email: "",
    suspension_reason: "",
    sso: "connected",
    health: { status: "unknown", checked_at: null, detail: "" },
    manifest_version: "2026-10-12",
    sign_in_mode: "sso_optional",
    login_url: "https://future.example.com/login",
    post_logout_redirect_uris: ["https://future.example.com/signed-out"],
    scopes: ["openid", "email", "profile"],
    token_endpoint_auth_method: "private_key_jwt",
    sso_state: "enabled",
    backchannel_logout_uri: "",
    ...overrides,
  };
}

function detail(link: unknown, extra: Record<string, unknown> = {}) {
  return {
    product: {
      id: "id-future_product",
      code: "future_product",
      name: "Future Product",
      slug: "future-product",
      tagline: "",
      description: "",
      category: "",
      icon: "",
      status: "available",
      manager_status: "active",
      listing: "listed",
      availability: "available",
      licensing_mode: "per_seat",
      sort_order: 50,
      marketing_url: "",
      store_title: "",
      store_summary: "",
      application_url: "",
      documentation_url: "",
      support_url: "",
      customer_store_enabled: true,
      reseller_enabled: true,
      affiliate_enabled: true,
      white_label_enabled: true,
      created_at: null,
      updated_at: null,
      activated_at: null,
      logo_media_id: null,
      media: [],
      launch_url: link ? "https://future.example.com/" : "",
      plans: [],
      channels: {
        direct: { listed: true, available: true, public_plans: 0, priced_plans: 0, ready: false },
        reseller: { partners: 0 },
        affiliate: { product_rules: 0, programme_default: false },
        trial: null,
      },
      integration: link,
      readiness: [],
    },
    categories: [],
    can_see_integrations: true,
    can_see_activity: false,
    ...extra,
  };
}

const NOT_READY = {
  verdict: "not_ready",
  checks: [
    { key: "registered", label: "Registered with Workeasy360", state: "ok", detail: "Client cid-123 (draft)." },
    { key: "sign_in", label: "Workeasy360 sign-in", state: "ok", detail: "1 callback URL(s) registered." },
    { key: "credential", label: "Client credential", state: "ok", detail: "1 active credential(s)." },
    { key: "domains", label: "Domain ownership", state: "action", detail: "Not proven: future.example.com, gone.example.com." },
    { key: "status", label: "Integration status", state: "action", detail: "Draft: not yet active." },
    { key: "backchannel", label: "Back-channel logout", state: "warning", detail: "Not registered: a sign-out waits for the next entitlement check." },
    { key: "health", label: "Address health", state: "info", detail: "Not checked yet." },
  ],
};
const READY = { verdict: "ready", checks: [{ key: "domains", label: "Domain ownership", state: "ok", detail: "1 hostname(s) proven." }] };

function domain(overrides: Record<string, unknown> = {}) {
  const hostname = String(overrides.hostname ?? "future.example.com");
  return {
    hostname,
    in_use: true,
    status: "pending",
    method: "",
    record_name: `_w360-product-verify.${hostname}`,
    record_type: "TXT",
    record_value: `w360-product-verify=token-${hostname}`,
    requested_at: "2026-11-15T10:00:00Z",
    verified_at: null,
    last_checked_at: null,
    last_outcome: "",
    ...overrides,
  };
}

const MIXED = {
  domains: [
    domain(),
    domain({ hostname: "gone.example.com", status: "lapsed", method: "dns_txt", last_checked_at: "2026-11-16T01:00:00Z", last_outcome: "control_lost" }),
    domain({ hostname: "old.example.com", status: "verified", method: "pre_existing", record_value: "", verified_at: "2026-11-01T00:00:00Z" }),
  ],
  unverified: ["future.example.com", "gone.example.com"],
  all_verified: false,
};
const PROVEN = {
  domains: [domain({ status: "verified", method: "dns_txt", verified_at: "2026-11-15T11:00:00Z", last_checked_at: "2026-11-15T11:00:00Z", last_outcome: "verified" })],
  unverified: [],
  all_verified: true,
};

function routes(link: unknown, extra: Record<string, { status: number; body?: unknown }[]> = {}, readiness: unknown = NOT_READY, domains: unknown = MIXED) {
  return {
    [DETAIL]: [ok(detail(link))],
    [READINESS]: [ok(readiness)],
    [DOMAINS]: [ok(domains)],
    ...extra,
  };
}

function renderDetail(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <ProductDetail productId="id-future_product" />
    </AdminContextForTest>,
  );
}

const valueOf = (term: string, scope: HTMLElement = document.body) => within(scope).getByText(term, { selector: "dt" }).nextElementSibling as HTMLElement;

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("SSO readiness", () => {
  it("shows the verdict and every check with its state, linking a missing proof to Domains, and passes axe", async () => {
    mockApi(routes(integration({ status: "draft" })));
    const { container } = renderDetail();
    const card = await screen.findByRole("region", { name: "SSO readiness" });
    expect(await within(card).findByText("Not ready")).toBeInTheDocument();
    const checks = within(within(card).getByRole("list", { name: "Readiness checks" })).getAllByRole("listitem");
    expect(checks).toHaveLength(7);
    expect(checks[3]).toHaveTextContent("Domain ownership");
    expect(checks[3]).toHaveTextContent("Not proven: future.example.com, gone.example.com.");
    expect(checks[3]).toHaveTextContent("Action needed");
    expect(within(checks[3]).getByRole("link", { name: "Go to Domains" })).toHaveAttribute("href", "#sso-domains");
    expect(checks[5]).toHaveTextContent("Warning");
    expect(within(checks[5]).getByRole("link", { name: "Go to Back-channel logout" })).toHaveAttribute("href", "#sso-backchannel");
    expect(checks[6]).toHaveTextContent("Note");
    expect(checks[0]).toHaveTextContent("Done");
    expect(within(checks[0]).queryByRole("link")).not.toBeInTheDocument();
    expect(document.getElementById("sso-domains")).toHaveTextContent("Domains");
    await screen.findByText("TXT records to publish");
    await expectNoViolations(container);
  });

  it("re-reads the verdict on Refresh", async () => {
    const calls = mockApi({ ...routes(integration()), [READINESS]: [ok(NOT_READY), ok(READY)] });
    renderDetail();
    const card = await screen.findByRole("region", { name: "SSO readiness" });
    await within(card).findByText("Not ready");
    fireEvent.click(within(card).getByRole("button", { name: "Refresh SSO readiness" }));
    expect(await within(card).findByText("Ready")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === READINESS)).toHaveLength(2);
  });

  it("a product with no integration makes no single sign-on reads but its setup state", async () => {
    // ADR-074: automatic setup has a state before any integration exists
    // ("Pending Configuration"), so its one read is the only one made.
    const SETUP = `GET ${ADMIN}/products/future_product/integration/sign-in-setup`;
    const calls = mockApi({
      [DETAIL]: [ok(detail(null))],
      [SETUP]: [ok({ product_code: "future_product", automatic: false, state: null, state_label: "Set up by hand", checks: [] })],
    });
    renderDetail();
    expect(await screen.findByRole("button", { name: "Register integration" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "SSO readiness" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Domains" })).not.toBeInTheDocument();
    await waitFor(() => expect(calls.map((c) => c.key).sort()).toEqual([DETAIL, SETUP].sort()));
  });
});

describe("Domains", () => {
  it("lists each hostname with its status, method and last check, and the TXT records to publish with copy buttons", async () => {
    mockApi(routes(integration({ status: "draft" })));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Domains" });
    const table = await within(card).findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(within(rows[0]).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Hostname", "Status", "Method", "Last check", "Action"]);
    expect(rows[1]).toHaveTextContent("future.example.com");
    expect(rows[1]).toHaveTextContent("Pending");
    expect(rows[1]).toHaveTextContent("Not checked yet");
    expect(rows[2]).toHaveTextContent("Lapsed");
    expect(rows[2]).toHaveTextContent("The record disappeared on repeated re-checks.");
    expect(rows[2]).toHaveTextContent("Proven by DNS");
    expect(within(rows[2]).getByText("control_lost")).toBeInTheDocument();
    expect(rows[3]).toHaveTextContent("Verified");
    expect(rows[3]).toHaveTextContent("Registered before verification");
    expect(rows[3]).toHaveTextContent("Ask for a DNS proof to replace it.");
    expect(within(card).getByRole("alert")).toHaveTextContent("Not proven yet: future.example.com, gone.example.com");

    expect(within(rows[1]).getByRole("button", { name: "Verify future.example.com" })).toBeEnabled();
    expect(within(rows[2]).getByRole("button", { name: "Verify gone.example.com" })).toBeEnabled();
    expect(within(rows[3]).getByRole("button", { name: "Ask for a DNS proof for old.example.com" })).toBeEnabled();

    const records = within(card).getByText("TXT records to publish").parentElement as HTMLElement;
    const future = within(records).getByText("future.example.com", { selector: "p" }).parentElement as HTMLElement;
    expect(valueOf("Name", future)).toHaveTextContent("_w360-product-verify.future.example.com");
    expect(valueOf("Type", future)).toHaveTextContent("TXT");
    expect(valueOf("Value", future)).toHaveTextContent("w360-product-verify=token-future.example.com");
    expect(future).toHaveTextContent("Publish this record at your DNS provider, then choose Verify.");
    expect(within(records).getByRole("button", { name: "Copy record name for future.example.com" })).toBeInTheDocument();
    expect(within(records).getByRole("button", { name: "Copy record value for gone.example.com" })).toBeInTheDocument();
    // A carried-over hostname with no value asked for yet has no record to show.
    expect(within(records).queryByText("old.example.com")).not.toBeInTheDocument();
  });

  it("a viewer sees the readiness, the domains and their records, but cannot verify, ask or add", async () => {
    mockApi(routes(integration({ status: "draft" })));
    renderDetail(VIEW);
    const card = await screen.findByRole("region", { name: "Domains" });
    const table = await within(card).findByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Hostname", "Status", "Method", "Last check"]);
    expect(within(card).getByText("TXT records to publish")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /^Verify/ })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /Ask for a DNS proof|Get TXT value/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add hostname" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit authentication" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate" })).not.toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "SSO readiness" })).toHaveTextContent("Not ready");
    // Reading the delivery status is a view, not a change.
    expect(screen.getByRole("button", { name: "Show delivery status" })).toBeInTheDocument();
  });

  it("Verify sends the hostname, shows the outcome and the new status, and re-reads the readiness", async () => {
    const verified = { ...MIXED, domains: [domain({ status: "verified", method: "dns_txt", last_checked_at: "2026-11-16T02:00:00Z", last_outcome: "verified" }), ...MIXED.domains.slice(1)], unverified: ["gone.example.com"], outcome: "verified" };
    const calls = mockApi(routes(integration({ status: "draft" }), { [VERIFY]: [ok(verified)] }));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Domains" });
    fireEvent.click(await within(card).findByRole("button", { name: "Verify future.example.com" }));
    expect(await within(card).findByText("future.example.com is verified.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === VERIFY)?.body).toEqual({ hostname: "future.example.com" });
    const rows = within(within(card).getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Verified");
    expect(within(rows[1]).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).getByRole("alert")).toHaveTextContent("Not proven yet: gone.example.com");
    await waitFor(() => expect(calls.filter((c) => c.key === READINESS)).toHaveLength(2));
  });

  it("a check that finds no record, and the rate limit, are said plainly", async () => {
    mockApi(
      routes(integration({ status: "draft" }), {
        [VERIFY]: [ok({ ...MIXED, outcome: "record_not_found" }), { status: 429, body: { code: "rate_limited", detail: "Too many requests. Try again shortly." } }],
      }),
    );
    renderDetail();
    const card = await screen.findByRole("region", { name: "Domains" });
    fireEvent.click(await within(card).findByRole("button", { name: "Verify future.example.com" }));
    expect(
      await within(card).findByText(/No TXT record with the verification value was found at _w360-product-verify\.future\.example\.com\./),
    ).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Verify future.example.com" }));
    expect(await within(card).findByText(/Too many verification checks/)).toBeInTheDocument();
  });

  it("asks for a DNS proof of a carried-over hostname", async () => {
    const asked = { ...MIXED, domains: MIXED.domains.map((d) => (d.hostname === "old.example.com" ? { ...d, record_value: "w360-product-verify=fresh" } : d)) };
    const calls = mockApi(routes(integration({ status: "draft" }), { [ASK]: [ok(asked)] }));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Domains" });
    fireEvent.click(await within(card).findByRole("button", { name: "Ask for a DNS proof for old.example.com" }));
    expect(await within(card).findByText(/Verification value issued for old\.example\.com/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === ASK)?.body).toEqual({ hostname: "old.example.com" });
    expect(within(card).getByText("w360-product-verify=fresh")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Verify old.example.com" })).toBeInTheDocument();
  });

  it("Add hostname asks for a proof before a live product is pointed at it, refusing anything but a bare hostname", async () => {
    const added = {
      domains: [...PROVEN.domains, domain({ hostname: "new.example.com", in_use: false })],
      unverified: [],
      all_verified: true,
    };
    const calls = mockApi(routes(integration(), { [ASK]: [ok(added)] }, READY, PROVEN));
    const { container } = renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Add hostname" }));
    const popup = screen.getByRole("dialog", { name: "Add a hostname" });
    const submit = within(popup).getByRole("button", { name: "Get verification value" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText(/^Hostname/), { target: { value: "https://new.example.com/app" } });
    expect(within(popup).getByText(/Enter the hostname only/)).toBeInTheDocument();
    expect(submit).toBeDisabled();
    await expectNoViolations(container.ownerDocument.body);
    fireEvent.change(within(popup).getByLabelText(/^Hostname/), { target: { value: " new.example.com " } });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.find((c) => c.key === ASK)?.body).toEqual({ hostname: "new.example.com" });
    const card = screen.getByRole("region", { name: "Domains" });
    expect(await within(card).findByText("new.example.com", { selector: "code" })).toBeInTheDocument();
    expect(within(card).getByText("Not in use")).toBeInTheDocument();
    expect(within(card).getByText("w360-product-verify=token-new.example.com")).toBeInTheDocument();
    expect(screen.getByText(/Verification value issued for new\.example\.com/)).toBeInTheDocument();
  });
});

describe("a refusal because a hostname is not proven", () => {
  const REFUSED_ACTIVATION = {
    status: 409,
    body: {
      code: "product_domain_unverified",
      detail:
        "Cannot activate this integration: control of future.example.com is not proven yet. Publish the verification value shown under Domains as TXT _w360-product-verify.future.example.com, then choose Verify.",
      field: "hostname",
      hostnames: ["future.example.com"],
      product_code: "future_product",
    },
  };

  it("activation: the server's words reach the page and the Domains section", async () => {
    const calls = mockApi(routes(integration({ status: "draft" }), { [`POST ${BASE}/status`]: [REFUSED_ACTIVATION] }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Activate" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${BASE}/status`)).toBe(true));
    const card = screen.getByRole("region", { name: "Domains" });
    const block = await within(card).findByText("Refused: control of a hostname is not proven yet");
    expect(block.closest("[role=alert]")).toHaveTextContent(/Cannot activate this integration: control of future\.example\.com is not proven yet/);
    // The page's own outcome line says it too.
    expect(screen.getAllByText(/Cannot activate this integration/)).toHaveLength(2);
    // Its record is already listed, so nothing more to ask for.
    expect(within(card).queryByRole("button", { name: /Get TXT value for/ })).not.toBeInTheDocument();
  });

  it("an edit to a new hostname: the popup keeps the server's words, and Domains offers to ask for the proof", async () => {
    const asked = { domains: [...PROVEN.domains, domain({ hostname: "new.example.com", in_use: false })], unverified: [], all_verified: true };
    const calls = mockApi(
      routes(
        integration(),
        {
          [PATCH]: [
            {
              status: 409,
              body: {
                code: "product_domain_unverified",
                detail: "Cannot point this product at a new hostname: control of new.example.com is not proven yet.",
                field: "hostname",
                hostnames: ["new.example.com"],
              },
            },
          ],
          [ASK]: [ok(asked)],
        },
        READY,
        PROVEN,
      ),
    );
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Edit authentication" }));
    const form = screen.getByRole("dialog", { name: "Authentication settings for Future Product" });
    fireEvent.change(within(form).getByLabelText(/Redirect URIs/), { target: { value: `${CALLBACK}\nhttps://new.example.com/cb` } });
    fireEvent.click(within(form).getByRole("button", { name: "Save authentication settings" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("control of new.example.com is not proven yet");
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    const card = screen.getByRole("region", { name: "Domains" });
    expect(await within(card).findByText("Refused: control of a hostname is not proven yet")).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Get TXT value for new.example.com" }));
    await waitFor(() => expect(calls.find((c) => c.key === ASK)?.body).toEqual({ hostname: "new.example.com" }));
    expect(await within(card).findByText("w360-product-verify=token-new.example.com")).toBeInTheDocument();
    // Asked for, not yet proven: still said, with nothing more to ask.
    expect(within(card).getByText("Refused: control of a hostname is not proven yet")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /Get TXT value for/ })).not.toBeInTheDocument();
  });

  it("reads the refusal's hostnames from the 409, and nothing else", () => {
    expect(domainBlockOf(new ApiError(409, REFUSED_ACTIVATION.body))).toEqual({ detail: REFUSED_ACTIVATION.body.detail, hostnames: ["future.example.com"] });
    expect(domainBlockOf(new ApiError(409, { code: "manifest_rejected", detail: "x" }))).toBeNull();
    expect(domainBlockOf(new ApiError(400, { code: "product_domain_unverified" }))).toBeNull();
    expect(domainBlockOf(new Error("boom"))).toBeNull();
    expect(domainBlockOf(new ApiError(409, { code: "product_domain_unverified" }))?.detail).toMatch(/Prove control/);
  });
});

describe("Back-channel logout", () => {
  it("is shown in the facts and edited in the authentication popup with the registry's rules, sent only when changed", async () => {
    const calls = mockApi(routes(integration(), { [PATCH]: [ok(integration({ backchannel_logout_uri: LOGOUT_URL }))] }, READY, PROVEN));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Back-channel logout" });
    expect(valueOf("Registration status", card)).toHaveTextContent("Not registered");
    expect(valueOf("Registration status", card)).toHaveTextContent(/stays signed in to the product until its next entitlement check/);
    expect(valueOf("Back-channel logout URL", card)).toHaveTextContent("—");
    expect(screen.getByText(/^None \(a sign-out reaches the product/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit authentication" }));
    const form = screen.getByRole("dialog", { name: "Authentication settings for Future Product" });
    const field = within(form).getByLabelText("Back-channel logout URL (https, optional)");
    const save = within(form).getByRole("button", { name: "Save authentication settings" });
    const tries: [string, RegExp][] = [
      ["http://future.example.com/bc", /Use an https:\/\/ address/],
      ["https://future.example.com/bc?next=1", /Remove the query string/],
      ["https://future.example.com/bc#x", /Remove the fragment/],
      ["https://*.example.com/bc", /Wildcards are not accepted/],
      ["https://user:pw@future.example.com/bc", /Remove the user name and password/], // pragma: allowlist secret
      ["https://other.example.com/bc", /Use the same origin \(scheme, host and port\) as a sign-in callback URL \(https:\/\/future\.example\.com\)/],
    ];
    for (const [value, message] of tries) {
      fireEvent.change(field, { target: { value } });
      expect(within(form).getByText(message)).toBeInTheDocument();
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(save).toBeDisabled();
    }
    fireEvent.change(field, { target: { value: ` ${LOGOUT_URL} ` } });
    expect(field).not.toHaveAttribute("aria-invalid");
    fireEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.key === PATCH)).toBe(true));
    expect(calls.find((c) => c.key === PATCH)?.body).toEqual({
      sign_in_mode: "sso_optional",
      login_url: "https://future.example.com/login",
      redirect_uris: [CALLBACK],
      post_logout_redirect_uris: ["https://future.example.com/signed-out"],
      scopes: ["openid", "email", "profile"],
      backchannel_logout_uri: LOGOUT_URL,
    });
    expect(await screen.findByText("Authentication settings saved.")).toBeInTheDocument();
  });

  it("clearing the URL removes it; switching to local sign-in only never keeps one", async () => {
    const calls = mockApi(routes(integration({ backchannel_logout_uri: LOGOUT_URL }), { [PATCH]: [ok(integration())] }, READY, PROVEN));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Back-channel logout" });
    expect(valueOf("Registration status", card)).toHaveTextContent("Registered");
    expect(valueOf("Back-channel logout URL", card)).toHaveTextContent(LOGOUT_URL);
    expect(screen.getAllByText(LOGOUT_URL)).toHaveLength(2); // the card and the authentication facts

    fireEvent.click(screen.getByRole("button", { name: "Edit authentication" }));
    let form = screen.getByRole("dialog", { name: "Authentication settings for Future Product" });
    const field = within(form).getByLabelText("Back-channel logout URL (https, optional)");
    expect(field).toHaveValue(LOGOUT_URL);
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save authentication settings" }));
    await waitFor(() => expect(calls.filter((c) => c.key === PATCH)).toHaveLength(1));
    expect((calls.find((c) => c.key === PATCH)?.body as Record<string, unknown>).backchannel_logout_uri).toBe("");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Edit authentication" }));
    form = screen.getByRole("dialog", { name: "Authentication settings for Future Product" });
    fireEvent.change(within(form).getByLabelText("Sign-in mode"), { target: { value: "local_only" } });
    const disabled = within(form).getByLabelText("Back-channel logout URL (https, optional)");
    expect(disabled).toBeDisabled();
    expect(disabled).toHaveValue("");
    expect(within(form).getByText(/Not used with local sign-in only/)).toBeInTheDocument();
    fireEvent.click(within(form).getByRole("button", { name: "Save authentication settings" }));
    await waitFor(() => expect(calls.filter((c) => c.key === PATCH)).toHaveLength(2));
    expect(calls.filter((c) => c.key === PATCH)[1].body).toMatchObject({ sign_in_mode: "local_only", backchannel_logout_uri: "" });
  });

  it("loads the delivery status on demand: last delivery, last error with its hint, retries, gave up and the day's counts", async () => {
    const summary = {
      backchannel_logout_uri: LOGOUT_URL,
      enabled: true,
      window_hours: 24,
      counts: { pending: 2, delivered: 5, failed: 1, dead: 1 },
      last_problem: "http_503",
      last_delivery: { status: "delivered", reason: "signed_out", scope: "everywhere", created_at: "2026-11-16T08:00:00Z", finished_at: "2026-11-16T08:00:01Z", attempts: 1, outcome: "http_200" },
      last_error: { status: "pending", reason: "signed_out", scope: "session", created_at: "2026-11-16T07:00:00Z", finished_at: null, attempts: 3, outcome: "http_503" },
      retry: { pending: 2, retrying: 1, next_attempt_at: "2026-11-16T09:00:00Z", max_attempts: 3, attempt_limit: 8 },
    };
    const quiet = {
      ...summary,
      counts: {},
      last_problem: "",
      last_delivery: null,
      last_error: null,
      retry: { pending: 0, retrying: 0, next_attempt_at: null, max_attempts: 0, attempt_limit: 8 },
    };
    const calls = mockApi(routes(integration({ backchannel_logout_uri: LOGOUT_URL }), { [BACKCHANNEL]: [ok(summary), ok(quiet)] }, READY, PROVEN));
    const { container } = renderDetail();
    const card = await screen.findByRole("region", { name: "Back-channel logout" });
    expect(calls.some((c) => c.key === BACKCHANNEL)).toBe(false);
    fireEvent.click(within(card).getByRole("button", { name: "Show delivery status" }));

    const last = await waitFor(() => valueOf("Last delivery status", card));
    expect(last).toHaveTextContent("Delivered");
    expect(last).toHaveTextContent("1 attempt");
    expect(last).toHaveTextContent("Signed out everywhere");
    const error = valueOf("Last delivery error", card);
    expect(within(error).getByText("http_503")).toBeInTheDocument();
    expect(error).toHaveTextContent("The product was unavailable. Delivery is retried.");
    expect(error).toHaveTextContent("3 attempts");
    const retry = valueOf("Retry status", card);
    expect(retry).toHaveTextContent("2 pending · 1 retrying");
    expect(retry).toHaveTextContent("Next attempt");
    expect(retry).toHaveTextContent("Most attempts so far: 3 of 8");
    expect(retry).toHaveTextContent("Gave up");
    expect(retry).toHaveTextContent("1 delivery in the last 24 h, after every retry (8 attempts).");
    const counts = within(card).getByRole("group", { name: "Deliveries in the last 24 hours" });
    expect(counts).toHaveTextContent(/Delivered\s*5/);
    expect(counts).toHaveTextContent(/Pending\s*2/);
    expect(counts).toHaveTextContent(/Failed\s*1/);
    expect(counts).toHaveTextContent(/Gave up\s*1/);
    await expectNoViolations(container);

    fireEvent.click(within(card).getByRole("button", { name: "Refresh delivery status" }));
    await waitFor(() => expect(valueOf("Last delivery status", card)).toHaveTextContent("None in the last 24 h"));
    expect(valueOf("Last delivery error", card)).toHaveTextContent("None");
    expect(valueOf("Retry status", card)).toHaveTextContent("Nothing is waiting to be retried.");
    expect(calls.filter((c) => c.key === BACKCHANNEL)).toHaveLength(2);
  });

  it("a product with local sign-in only does not use it", async () => {
    mockApi(routes(integration({ sign_in_mode: "local_only", sso_state: "disabled" }), {}, READY, PROVEN));
    renderDetail();
    const card = await screen.findByRole("region", { name: "Back-channel logout" });
    expect(valueOf("Registration status", card)).toHaveTextContent("Not used: the product signs people in itself.");
  });
});

describe("single sign-on vocabulary", () => {
  it("reads a delivery outcome in plain words", () => {
    expect(outcomeHint("http_200")).toBe("The product accepted the logout token.");
    expect(outcomeHint("http_401")).toMatch(/refused the logout token.*Not retried/);
    expect(outcomeHint("http_429")).toMatch(/try again later\. Delivery is retried/);
    expect(outcomeHint("http_502")).toBe("The product was unavailable. Delivery is retried.");
    expect(outcomeHint("http_502", "dead")).toBe("The product was unavailable. WorkEasy360 gave up after its last retry.");
    expect(outcomeHint("connect_timeout")).toMatch(/did not answer in time/);
    expect(outcomeHint("read_timeout")).toMatch(/did not answer in time/);
    expect(outcomeHint("dns_failure")).toMatch(/could not be resolved/);
    expect(outcomeHint("tls_error")).toMatch(/TLS/);
    expect(outcomeHint("rejected_private_address")).toMatch(/would not call this address.*Not retried/);
    expect(outcomeHint("something_new")).toBe("");
  });

  it("checks a back-channel logout URL and a hostname as the registry would", () => {
    expect(backchannelUriProblem("", [CALLBACK])).toBeUndefined();
    expect(backchannelUriProblem(LOGOUT_URL, [CALLBACK])).toBeUndefined();
    expect(backchannelUriProblem("https://FUTURE.example.com/bc", [CALLBACK])).toBeUndefined();
    expect(backchannelUriProblem("https://future.example.com:8443/bc", [CALLBACK])).toMatch(/same origin/);
    expect(backchannelUriProblem(LOGOUT_URL, [])).toMatch(/Add the sign-in callback URL first/);
    expect(backchannelUriProblem("not a url", [CALLBACK])).toMatch(/full https/);
    expect(backchannelUriProblem("https://future.example.com\\bc", [CALLBACK])).toMatch(/backslash/);
    expect(hostnameProblem("app.example.com")).toBeUndefined();
    expect(hostnameProblem("localhost")).toBeUndefined();
    expect(hostnameProblem("")).toMatch(/Enter a hostname/);
    expect(hostnameProblem("app.example.com:443")).toMatch(/hostname only/);
    expect(hostnameProblem("*.example.com")).toMatch(/hostname only/);
    expect(hostnameProblem("intranet")).toMatch(/full hostname/);
  });

  it("offers Verify, a request for a value, or nothing, by a hostname's state", () => {
    expect(domainAction(domain() as ProductDomain)).toBe("verify");
    expect(domainAction(domain({ status: "lapsed" }) as ProductDomain)).toBe("verify");
    expect(domainAction(domain({ status: "missing", record_value: "" }) as ProductDomain)).toBe("request");
    expect(domainAction(domain({ status: "verified", method: "pre_existing", record_value: "" }) as ProductDomain)).toBe("request");
    expect(domainAction(domain({ status: "verified", method: "pre_existing" }) as ProductDomain)).toBe("verify");
    expect(domainAction(domain({ status: "verified", method: "dns_txt" }) as ProductDomain)).toBeNull();
    expect(domainAction(domain({ status: "verified", method: "loopback", record_value: "" }) as ProductDomain)).toBeNull();
  });

  it("the console's one status map knows the new states", () => {
    expect(toneOf("delivered")).toBe("success");
    expect(toneOf("dead")).toBe("danger");
    expect(statusLabel("dead")).toBe("Gave up");
    expect(toneOf("lapsed")).toBe("danger");
    expect(toneOf("missing")).toBe("warning");
    expect(toneOf("attention")).toBe("warning");
    expect(statusLabel("not_ready")).toBe("Not ready");
    expect(toneOf("not_ready")).toBe("danger");
    expect(toneOf("verified")).toBe("success");
  });
});
