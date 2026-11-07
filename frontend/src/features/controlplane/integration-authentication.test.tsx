import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ProductDetail } from "./productmanager/ProductDetail";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * Super Admin -> Product Manager -> a product -> Integration (ADR-044, moved
 * here by ADR-051): the product's sign-in declaration, client id and exact
 * redirect URIs are shown; credentials are listed by public reference only; a
 * generated secret appears once, from the response that created it; a product
 * without an integration can be registered from the console - no code change
 * for a new product. Commercial and technical status stay separate facts.
 */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams("tab=integration"),
  usePathname: () => "/admin/products/id-future_product",
}));

const ADMIN = "/api/v1/admin";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_integration.manage"];
const DETAIL = `GET ${ADMIN}/products/id-future_product`;

function integration(overrides: Record<string, unknown> = {}) {
  return {
    client_id: "cid-123",
    status: "active",
    display_name: "Future Product",
    launch_url: "https://future.example.com/",
    health_url: "",
    redirect_uris: ["https://future.example.com/api/auth/callback"],
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

function renderDetail(permissions = MANAGE) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <ProductDetail productId="id-future_product" />
    </AdminContextForTest>,
  );
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("product integration in the Product Manager", () => {
  it("shows the sign-in declaration, client id, exact redirect URIs and scopes - never a secret", async () => {
    mockApi({ [DETAIL]: [ok(detail(integration()))] });
    const { container } = renderDetail();
    await screen.findByRole("heading", { level: 1, name: "Future Product" });
    expect(screen.getByText("Connected", { selector: "span" })).toBeInTheDocument(); // integration status
    expect(screen.getByText("OIDC")).toBeInTheDocument();
    expect(screen.getByText("WorkEasy360 SSO")).toBeInTheDocument();
    expect(screen.getByText(/Optional SSO/)).toBeInTheDocument();
    expect(screen.getByText("cid-123")).toBeInTheDocument();
    expect(screen.getByText("https://future.example.com/api/auth/callback")).toBeInTheDocument();
    expect(screen.getByText("https://future.example.com/signed-out")).toBeInTheDocument();
    expect(screen.getByText("openid email profile")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toMatch(/secret_hash|private key|client_secret/i);
    await expectNoViolations(container);
  });

  it("edits the sign-in mode, sign-in page, redirect and sign-out URIs through the audited manifest write", async () => {
    const calls = mockApi({
      [DETAIL]: [ok(detail(integration()))],
      [`PATCH ${ADMIN}/products/future_product/integration`]: [ok(integration({ sign_in_mode: "sso_only" }))],
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Edit authentication" }));
    const form = screen.getByRole("dialog", { name: "Authentication settings for Future Product" });
    fireEvent.change(within(form).getByLabelText("Sign-in mode"), { target: { value: "sso_only" } });
    fireEvent.change(within(form).getByLabelText(/Redirect URIs/), {
      target: { value: "https://future.example.com/api/auth/callback\nhttps://future.example.com/cb2" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save authentication settings" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)).toBe(true));
    expect(await screen.findByText("Authentication settings saved.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)?.body).toEqual({
      sign_in_mode: "sso_only",
      login_url: "https://future.example.com/login",
      redirect_uris: ["https://future.example.com/api/auth/callback", "https://future.example.com/cb2"],
      post_logout_redirect_uris: ["https://future.example.com/signed-out"],
      scopes: ["openid", "email", "profile"],
    });
  });

  it("editing the launch settings sends only the fields shown, to the product's own code", async () => {
    const calls = mockApi({
      [DETAIL]: [ok(detail(integration()))],
      [`PATCH ${ADMIN}/products/future_product/integration`]: [ok(integration())],
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Edit launch settings" }));
    const popup = screen.getByRole("dialog", { name: "Launch settings" });
    fireEvent.change(within(popup).getByLabelText("Launch URL (https)"), { target: { value: "http://new.example.com/open" } });
    expect(within(popup).getByText("Use an https:// address.")).toBeInTheDocument();
    expect(within(popup).getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Launch URL (https)"), { target: { value: "https://new.example.com/open" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)).toBe(true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText("Integration updated.")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)?.body).toEqual({
      display_name: "Future Product",
      launch_url: "https://new.example.com/open",
      health_url: "",
      contact_email: "",
    });
  });

  it("suspending records a reason without asking; check now shows the probe's answer", async () => {
    const calls = mockApi({
      [DETAIL]: [ok(detail(integration()))],
      [`POST ${ADMIN}/products/future_product/integration/status`]: [ok(integration({ status: "suspended" }))],
      [`POST ${ADMIN}/products/future_product/integration/health-check`]: [ok({ status: "healthy", detail: "http_200" })],
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Check now" }));
    expect(await screen.findByText("healthy (http_200)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    fireEvent.click(within(screen.getByRole("group", { name: /^Confirm / })).getByRole("button", { name: /^Confirm/ }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/future_product/integration/status`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${ADMIN}/products/future_product/integration/status`)?.body).toEqual({
      status: "suspended",
      reason: "Suspend - Super Admin console",
    });
  });

  it("registers the integration of a product that has none, from the console alone", async () => {
    const calls = mockApi({
      [DETAIL]: [ok(detail(null))],
      [`POST ${ADMIN}/products/future_product/integration`]: [{ status: 201, body: integration({ status: "draft", sso: "integration_required", sso_state: "integration_required" }) }],
    });
    renderDetail();
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Register integration" }));
    const form = screen.getByRole("dialog", { name: "Register the integration for Future Product" });
    const register = within(form).getByRole("button", { name: "Register" });
    fireEvent.change(within(form).getByLabelText("Launch URL (https)"), { target: { value: "https://future.example.com/" } });
    expect(register).toBeDisabled(); // the redirect URIs are required
    fireEvent.change(within(form).getByLabelText(/Redirect URIs/), { target: { value: "https://future.example.com/api/auth/callback" } });
    fireEvent.click(register);
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/future_product/integration`)).toBe(true));
    expect(calls.find((c) => c.key === `POST ${ADMIN}/products/future_product/integration`)?.body).toMatchObject({
      display_name: "Future Product",
      launch_url: "https://future.example.com/",
      sign_in_mode: "sso_only",
      redirect_uris: ["https://future.example.com/api/auth/callback"],
      scopes: ["openid", "email", "profile", "organization"],
    });
  });

  it("lists credentials by reference and shows a generated secret once", async () => {
    const calls = mockApi({
      [DETAIL]: [ok(detail(integration()))],
      [`GET ${ADMIN}/products/future_product/integration/credentials`]: [
        ok({ credentials: [] }),
        ok({
          credentials: [
            {
              credential_id: "abcd1234",
              kind: "client_secret",
              status: "active",
              algorithm: "",
              label: "",
              expires_at: "2027-01-01T00:00:00+00:00",
              revoked_at: null,
              last_used_on: null,
              created_at: "2026-10-12T00:00:00+00:00",
            },
          ],
        }),
      ],
      [`POST ${ADMIN}/products/future_product/integration/credentials`]: [
        { status: 201, body: { credential_id: "abcd1234", kind: "client_secret", expires_at: "2027-01-01T00:00:00+00:00", client_secret: "s3cr3t-value", notice: "This secret is shown once." } },
      ],
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Show credentials" }));
    expect(await screen.findByText(/No credentials/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate client secret" }));
    const popup = screen.getByRole("dialog", { name: "Generate a client secret" });
    fireEvent.click(within(popup).getByRole("button", { name: "Generate client secret" }));
    expect(await screen.findByText("s3cr3t-value")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${ADMIN}/products/future_product/integration/credentials`)?.body).toMatchObject({ kind: "client_secret" });
    fireEvent.click(screen.getByRole("button", { name: "I have stored it - hide" }));
    expect(screen.queryByText("s3cr3t-value")).not.toBeInTheDocument();
    expect(screen.getByText("abcd1234")).toBeInTheDocument();
  });

  it("adds a public key in a popup, which keeps the key typed when the server refuses it", async () => {
    const PEM = "-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----";
    const calls = mockApi({
      [DETAIL]: [ok(detail(integration()))],
      [`GET ${ADMIN}/products/future_product/integration/credentials`]: [ok({ credentials: [] })],
      [`POST ${ADMIN}/products/future_product/integration/credentials`]: [
        { status: 400, body: { detail: "That is not a supported public key." } },
        { status: 201, body: { credential_id: "k1", kind: "public_key", expires_at: null, client_secret: null, notice: "" } },
      ],
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Show credentials" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add public key" }));
    const popup = screen.getByRole("dialog", { name: "Add a public key" });
    const add = within(popup).getByRole("button", { name: "Add public key" });
    expect(add).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText(/Product public key/), { target: { value: PEM } });
    fireEvent.change(within(popup).getByLabelText("Label (optional)"), { target: { value: "prod" } });
    fireEvent.click(add);
    expect(await within(popup).findByRole("alert")).toHaveTextContent("That is not a supported public key.");
    expect(within(popup).getByLabelText(/Product public key/)).toHaveValue(PEM);
    fireEvent.click(add);
    expect(await screen.findByText("Public key added as credential k1.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const posts = calls.filter((c) => c.key === `POST ${ADMIN}/products/future_product/integration/credentials`);
    expect(posts[1].body).toEqual({ kind: "public_key", public_key_pem: PEM, label: "prod" });
  });

  it("without the integration grant, the integration section is absent - not merely disabled", async () => {
    mockApi({ [DETAIL]: [ok(detail(null, { can_see_integrations: false }))] });
    renderDetail(["platform.staff_catalog.manage"]);
    expect(await screen.findByText("Integration details need the integration view permission.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Register integration" })).not.toBeInTheDocument();
    expect(screen.queryByText("Single sign-on")).not.toBeInTheDocument();
  });
});
