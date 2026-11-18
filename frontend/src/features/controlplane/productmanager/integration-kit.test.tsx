/** Super Admin > Product > Integration: the integration kit card. It asks the
 * platform for the chosen framework's kit and shows each file with copy and
 * download, what the registration still lacks, and the next steps. */
import { describe, it, expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IntegrationKitCard } from "./IntegrationKitCard";
import { expectNoViolations, mockApi, ok } from "../testSupport";

const KIT = "GET /api/v1/admin/products/future_product/integration/kit";

function kit(framework: string) {
  return {
    product_code: "future_product",
    framework,
    adapter: framework === "spring" ? "Spring Boot: Spring Security OAuth 2.0 client" : "Django: the workeasy360-sso connector",
    generated_at: "2026-11-18T03:00:00Z",
    facts: {
      issuer: "https://accounts.workeasy360.com",
      discovery: "https://accounts.workeasy360.com/.well-known/openid-configuration",
      client_id: "cid-123",
      product_code: "future_product",
      redirect_uris: ["https://future.example.com/auth/workeasy360/callback/"],
      backchannel_logout_uri: "",
      key_id: "kid-1",
      integration_status: "active",
    },
    missing: ["a back-channel logout URI (needed for global sign-out)"],
    files: [
      { path: ".env.example", purpose: "Environment template (no secrets)", content: "WORKEASY360_CLIENT_ID=cid-123\n" },
      { path: "requirements.txt", purpose: "The connector", content: "workeasy360-sso @ git+ssh://...\n" },
    ],
    next: ["Store the private key in the product's secret manager."],
  };
}

describe("Integration kit", () => {
  it("generates the chosen framework's kit and shows its files, gaps and next steps, and passes axe", async () => {
    const calls = mockApi({ [KIT]: [ok(kit("spring"))] });
    const { container } = render(<IntegrationKitCard code="future_product" />);
    fireEvent.change(screen.getByLabelText("Framework"), { target: { value: "spring" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    await screen.findByLabelText("Generated integration kit");
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/admin/products/future_product/integration/kit?framework=spring"]);
    expect(screen.getByText(/Spring Security OAuth 2.0 client/)).toBeTruthy();
    expect(screen.getByText("WORKEASY360_CLIENT_ID=cid-123")).toBeTruthy();
    expect(screen.getByText("a back-channel logout URI (needed for global sign-out)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy .env.example" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(2);
    expect(screen.getByText("Store the private key in the product's secret manager.")).toBeTruthy();
    await expectNoViolations(container);
  });

  it("says why when the platform refuses", async () => {
    mockApi({ [KIT]: [{ status: 403, body: { code: "permission_denied", detail: "You do not have permission." } }] });
    render(<IntegrationKitCard code="future_product" />);
    fireEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByLabelText("Generated integration kit")).toBeNull();
  });

  it("forgets a generated kit when the framework changes", async () => {
    mockApi({ [KIT]: [ok(kit("django"))] });
    render(<IntegrationKitCard code="future_product" />);
    fireEvent.click(screen.getByRole("button", { name: "Generate kit" }));
    await screen.findByLabelText("Generated integration kit");
    fireEvent.change(screen.getByLabelText("Framework"), { target: { value: "node" } });
    expect(screen.queryByLabelText("Generated integration kit")).toBeNull();
  });
});
