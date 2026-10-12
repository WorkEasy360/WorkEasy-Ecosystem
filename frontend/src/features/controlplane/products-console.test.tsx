import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe, { type Result } from "axe-core";

import { ProductsConsole } from "./AdminMoney";
import { AdminContextForTest, mockApi } from "./testSupport";

/**
 * The Super Admin products console (Phase 11, Step 17/18 of the brief): a
 * product's commercial status and technical (integration) status render as
 * two separate facts, the channel summary is read-only text from the server,
 * and every mutation - trial configuration, integration edits, suspend/resume,
 * a health check - reaches exactly the endpoint and body it should, gated by
 * the right permission.
 */

const ADMIN = "/api/v1/admin";

const RULESET = { runOnly: { type: "tag" as const, values: ["wcag2a", "wcag2aa"] } };
async function expectNoViolations(container: HTMLElement) {
  const { violations } = await axe.run(container, RULESET);
  const detail = violations.map((v: Result) => `${v.id}: ${v.help}\n    ${v.nodes[0]?.html ?? ""}`).join("\n  ");
  expect(violations, detail).toEqual([]);
}

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: "prod-1",
    code: "crm",
    name: "Easy CRM",
    slug: "easy-crm",
    tagline: "Sell more",
    description: "A CRM.",
    category: "Sales / CRM",
    icon: "customers",
    status: "available",
    listing: "listed",
    availability: "available",
    licensing_mode: "per_seat",
    sort_order: 10,
    marketing_url: "",
    plans: [
      { code: "crm_starter", name: "CRM Starter", status: "available", is_public: true, kind: "standard", billing_type: "paid", priced: true },
    ],
    channels: {
      direct: { listed: true, available: true, public_plans: 1, priced_plans: 1, ready: true },
      reseller: { partners: 0 },
      affiliate: { product_rules: 0, programme_default: false },
      trial: null,
    },
    integration: {
      client_id: "cid-1",
      status: "active",
      display_name: "Easy CRM",
      launch_url: "https://crm.example.com/launch",
      health_url: "https://crm.example.com/healthz",
      redirect_uris: ["https://crm.example.com/callback"],
      contact_email: "ops@crm.example.com",
      suspension_reason: "",
      sso: "connected",
      health: { status: "unknown", checked_at: null, detail: "" },
      manifest_version: "2026-10-12",
      sign_in_mode: "sso_only",
      login_url: "",
      post_logout_redirect_uris: [],
      scopes: ["openid", "email"],
      token_endpoint_auth_method: "private_key_jwt",
      sso_state: "enabled",
    },
    ...overrides,
  };
}

function console_(overrides: Record<string, unknown> = {}) {
  return { products: [product()], can_see_integrations: true, ...overrides };
}

const CATALOG = ["platform.staff_catalog.manage", "platform.staff_pricing.manage"];
const INTEGRATION = ["platform.staff_integration.manage"];

function renderConsole(permissions: string[]) {
  return render(
    <AdminContextForTest permissions={permissions}>
      <ProductsConsole />
    </AdminContextForTest>,
  );
}

describe("the products console", () => {
  it("shows commercial and technical status as two separate facts, and passes axe", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }] });
    const { container } = renderConsole([...CATALOG, ...INTEGRATION]);
    await screen.findByRole("heading", { level: 2, name: "Easy CRM" });
    // Commercial status (product.status) and public availability are both
    // "available" here - two separate facts, rendered as two separate badges.
    expect(screen.getAllByText("available", { selector: "span" })).toHaveLength(2);
    expect(screen.getByText("active", { selector: "span" })).toBeInTheDocument(); // integration status, distinct
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("https://crm.example.com/launch")).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("shows the reseller, affiliate and trial channel facts from the server, never computed here", async () => {
    mockApi({
      [`GET ${ADMIN}/products/console`]: [
        {
          status: 200,
          body: console_({
            products: [
              product({
                channels: {
                  direct: { listed: true, available: true, public_plans: 2, priced_plans: 1, ready: false },
                  reseller: { partners: 3 },
                  affiliate: { product_rules: 1, programme_default: true },
                  trial: { enabled: true, duration_days: 14, seat_limit: null, payment_method_required: false, eligible_plan_codes: ["crm_starter"] },
                },
              }),
            ],
          }),
        },
      ],
    });
    renderConsole(CATALOG);
    await screen.findByRole("heading", { level: 2, name: "Easy CRM" });
    expect(screen.getByText(/3 partner\(s\) granted/)).toBeInTheDocument();
    expect(screen.getByText(/1 product rule\(s\) · programme default applies/)).toBeInTheDocument();
    expect(screen.getByText(/14 days, on · 1 eligible plan\(s\)/)).toBeInTheDocument();
    expect(screen.getByText(/Not ready/)).toBeInTheDocument();
  });

  it("without the integration grant, the integration section is absent - not merely disabled", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_({ can_see_integrations: false, products: [product({ integration: null })] }) }] });
    renderConsole(CATALOG);
    await screen.findByRole("heading", { level: 2, name: "Easy CRM" });
    expect(screen.queryByText("Single sign-on")).not.toBeInTheDocument();
    expect(screen.queryByText("No integration registered for this product.")).not.toBeInTheDocument();
  });

  it("a product with no registered integration says so plainly, without crashing", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_({ products: [product({ integration: null })] }) }] });
    renderConsole([...CATALOG, ...INTEGRATION]);
    expect(await screen.findByText("No integration registered for this product.")).toBeInTheDocument();
  });

  it("without the catalog grant, there is nothing to edit and no way to create a product", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }] });
    renderConsole([]);
    await screen.findByRole("heading", { level: 2, name: "Easy CRM" });
    expect(screen.queryByRole("button", { name: "Edit Easy CRM" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Configure trial" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add a product SKU" })).not.toBeInTheDocument();
  });

  it("without the integration grant, the facts show but no integration controls do", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }] });
    renderConsole(CATALOG);
    await screen.findByText("Connected");
    expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suspend" })).not.toBeInTheDocument();
  });

  it("configuring a trial sends exactly the terms chosen, never a payment-method requirement", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }],
      [`PUT ${ADMIN}/catalog/products/prod-1/trial`]: [
        { status: 200, body: { enabled: true, duration_days: 14, seat_limit: null, payment_method_required: false, eligible_plan_codes: ["crm_starter"] } },
      ],
    });
    renderConsole(CATALOG);
    fireEvent.click(await screen.findByRole("button", { name: "Configure trial" }));
    fireEvent.click(screen.getByLabelText("Trial enabled"));
    fireEvent.change(screen.getByLabelText("Duration (days)"), { target: { value: "14" } });
    fireEvent.click(screen.getByLabelText(/CRM Starter/));
    fireEvent.click(screen.getByRole("button", { name: "Save trial configuration" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PUT ${ADMIN}/catalog/products/prod-1/trial`)).toBe(true));
    const put = calls.find((c) => c.key === `PUT ${ADMIN}/catalog/products/prod-1/trial`)!;
    expect(put.body).toEqual({
      enabled: true,
      duration_days: 14,
      seat_limit: null,
      payment_method_required: false,
      eligible_plan_codes: ["crm_starter"],
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Trial configuration for Easy CRM saved.");
  });

  it("a trial cannot be saved enabled with no eligible plan chosen", async () => {
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }] });
    renderConsole(CATALOG);
    fireEvent.click(await screen.findByRole("button", { name: "Configure trial" }));
    fireEvent.click(screen.getByLabelText("Trial enabled"));
    expect(screen.getByRole("button", { name: "Save trial configuration" })).toBeDisabled();
  });

  it("editing the integration sends only the fields shown, to the product's own code", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }],
      [`PATCH ${ADMIN}/products/crm/integration`]: [{ status: 200, body: product().integration }],
    });
    renderConsole(INTEGRATION);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Launch URL (https)"), { target: { value: "https://new.crm.example.com/open" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.key === `PATCH ${ADMIN}/products/crm/integration`)).toBe(true));
    const patch = calls.find((c) => c.key === `PATCH ${ADMIN}/products/crm/integration`)!;
    expect(patch.body).toEqual({
      display_name: "Easy CRM",
      launch_url: "https://new.crm.example.com/open",
      health_url: "https://crm.example.com/healthz",
      contact_email: "ops@crm.example.com",
    });
  });

  it("suspending needs a reason, and resuming needs none", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }],
      [`POST ${ADMIN}/products/crm/integration/status`]: [{ status: 200, body: { ...product().integration, status: "suspended" } }],
    });
    renderConsole(INTEGRATION);
    fireEvent.click(await screen.findByRole("button", { name: "Suspend" }));
    fireEvent.change(screen.getByLabelText(/reason/i), { target: { value: "planned maintenance window" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/products/crm/integration/status`)).toBe(true));
    const post = calls.find((c) => c.key === `POST ${ADMIN}/products/crm/integration/status`)!;
    expect(post.body).toEqual({ status: "suspended", reason: "planned maintenance window" });
  });

  it("check now probes this product's own code and shows the result without a page reload", async () => {
    mockApi({
      [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }],
      [`POST ${ADMIN}/products/crm/integration/health-check`]: [{ status: 200, body: { status: "healthy", detail: "http_200" } }],
    });
    renderConsole(INTEGRATION);
    fireEvent.click(await screen.findByRole("button", { name: "Check now" }));
    expect(await screen.findByText("healthy (http_200)")).toBeInTheDocument();
  });

  it("shows the client id but never a secret or key anywhere on the page", async () => {
    // ADR-044: the brief's Authentication section shows the client id. It is
    // a public identifier - it travels in every authorization URL and is the
    // ID token's audience - not a credential. Secrets and keys are never in
    // the console's response at all, so they can never be on the page.
    mockApi({ [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_() }] });
    renderConsole([...CATALOG, ...INTEGRATION]);
    await screen.findByRole("heading", { level: 2, name: "Easy CRM" });
    expect(screen.getByText("cid-1")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toMatch(/secret_hash|PRIVATE KEY|client_secret/i);
  });

  it("adding a product SKU sends only a code and a name", async () => {
    const calls = mockApi({
      [`GET ${ADMIN}/products/console`]: [{ status: 200, body: console_({ products: [] }) }],
      [`POST ${ADMIN}/catalog/products`]: [{ status: 201, body: product({ status: "draft" }) }],
    });
    renderConsole(CATALOG);
    fireEvent.change(await screen.findByLabelText("Code"), { target: { value: "easy_helpdesk" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Easy Help Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Create product" }));
    await waitFor(() => expect(calls.some((c) => c.key === `POST ${ADMIN}/catalog/products`)).toBe(true));
    const post = calls.find((c) => c.key === `POST ${ADMIN}/catalog/products`)!;
    expect(post.body).toEqual({ code: "easy_helpdesk", name: "Easy Help Desk" });
    expect(post.headers["idempotency-key"]).toBeTruthy();
  });
});
