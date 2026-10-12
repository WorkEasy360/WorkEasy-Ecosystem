import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ProductsConsole } from "./AdminMoney";
import { AdminContextForTest, expectNoViolations, mockApi, ok } from "./testSupport";

/**
 * Super Admin -> Products -> Authentication (ADR-044): the product's sign-in
 * declaration, client id and exact redirect URIs are shown; credentials are
 * listed by public reference only; a generated secret appears once, from the
 * response that created it; a product without an integration can be
 * registered from the console - no code change for a new product.
 */

const ADMIN = "/api/v1/admin";
const MANAGE = ["platform.staff_catalog.manage", "platform.staff_integration.manage"];

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

function product(code: string, name: string, link: unknown) {
  return {
    id: `id-${code}`,
    code,
    name,
    slug: code.replace(/_/g, "-"),
    tagline: "",
    description: "",
    category: "",
    icon: "",
    status: "available",
    listing: "listed",
    availability: "available",
    licensing_mode: "per_seat",
    sort_order: 50,
    marketing_url: "",
    plans: [],
    channels: {
      direct: { listed: true, available: true, public_plans: 0, priced_plans: 0, ready: false },
      reseller: { partners: 0 },
      affiliate: { product_rules: 0, programme_default: false },
      trial: null,
    },
    integration: link,
  };
}

function renderConsole() {
  return render(
    <AdminContextForTest permissions={MANAGE}>
      <ProductsConsole />
    </AdminContextForTest>,
  );
}

describe("product authentication in the Super Admin console", () => {
  it("shows the sign-in declaration, client id, exact redirect URIs and scopes", async () => {
    mockApi({
      [`GET ${ADMIN}/products/console`]: [ok({ products: [product("future_product", "Future Product", integration())], can_see_integrations: true })],
    });
    const { container } = renderConsole();
    const card = (await screen.findByRole("heading", { name: "Future Product", level: 2 })).closest("section") as HTMLElement;
    expect(within(card).getByText("Workeasy360 SSO")).toBeInTheDocument();
    expect(within(card).getByText("Enabled")).toBeInTheDocument();
    expect(within(card).getByText(/Optional SSO/)).toBeInTheDocument();
    expect(within(card).getByText("cid-123")).toBeInTheDocument();
    expect(within(card).getByText("https://future.example.com/api/auth/callback")).toBeInTheDocument();
    expect(within(card).getByText("https://future.example.com/signed-out")).toBeInTheDocument();
    expect(within(card).getByText("openid email profile")).toBeInTheDocument();
    // No secret anywhere on the page.
    expect(container.textContent ?? "").not.toMatch(/secret_hash|private/i);
    await expectNoViolations(container);
  });

  it("edits the sign-in mode, sign-in page, redirect and sign-out URIs through the audited manifest write", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [ok({ products: [product("future_product", "Future Product", integration())], can_see_integrations: true })],
      [`PATCH ${ADMIN}/products/future_product/integration`]: [ok(integration({ sign_in_mode: "sso_only" }))],
    });
    renderConsole();
    fireEvent.click(await screen.findByRole("button", { name: "Edit authentication" }));
    const form = screen.getByRole("form", { name: "Authentication settings for Future Product" });
    fireEvent.change(within(form).getByLabelText("Sign-in mode"), { target: { value: "sso_only" } });
    fireEvent.change(within(form).getByLabelText(/Redirect URIs/), {
      target: { value: "https://future.example.com/api/auth/callback\nhttps://future.example.com/cb2" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save authentication settings" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)).toBe(true));
    const sent = calls.find((c) => c.key === `PATCH ${ADMIN}/products/future_product/integration`)?.body;
    expect(sent).toEqual({
      sign_in_mode: "sso_only",
      login_url: "https://future.example.com/login",
      redirect_uris: ["https://future.example.com/api/auth/callback", "https://future.example.com/cb2"],
      post_logout_redirect_uris: ["https://future.example.com/signed-out"],
      scopes: ["openid", "email", "profile"],
    });
  });

  it("registers the integration of a product that has none, from the console alone", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [ok({ products: [product("future_product", "Future Product", null)], can_see_integrations: true })],
      [`POST ${ADMIN}/products/future_product/integration`]: [{ status: 201, body: integration({ status: "draft", sso: "integration_required", sso_state: "integration_required" }) }],
    });
    renderConsole();
    fireEvent.click(await screen.findByRole("button", { name: "Register integration" }));
    const form = screen.getByRole("form", { name: "Register the integration for Future Product" });
    fireEvent.change(within(form).getByLabelText("Launch URL (https)"), { target: { value: "https://future.example.com/" } });
    fireEvent.change(within(form).getByLabelText(/Redirect URIs/), { target: { value: "https://future.example.com/api/auth/callback" } });
    fireEvent.click(within(form).getByRole("button", { name: "Register" }));
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
      [`GET ${ADMIN}/products/console`]: [ok({ products: [product("future_product", "Future Product", integration())], can_see_integrations: true })],
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
    renderConsole();
    fireEvent.click(await screen.findByRole("button", { name: "Show credentials" }));
    expect(await screen.findByText(/No credentials/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate client secret" }));
    expect(await screen.findByText("s3cr3t-value")).toBeInTheDocument();
    expect(calls.find((c) => c.key === `POST ${ADMIN}/products/future_product/integration/credentials`)?.body).toMatchObject({
      kind: "client_secret",
    });
    fireEvent.click(screen.getByRole("button", { name: "I have stored it - hide" }));
    expect(screen.queryByText("s3cr3t-value")).not.toBeInTheDocument();
    expect(screen.getByText("abcd1234")).toBeInTheDocument();
  });
});
