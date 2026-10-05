import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MfaSettings } from "./MfaSettings";

type Reply = { status: number; body?: unknown };

/** Routes `fetch` by "METHOD path" to a queue of replies (last one repeats). */
function mockApi(routes: Record<string, Reply[]>) {
  const calls: { key: string; body: unknown }[] = [];
  global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const queue = routes[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
      status: reply.status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

const STEP_UP_REQUIRED: Reply = { status: 401, body: { code: "step_up_required" } };
const TOTP_FACTOR = {
  factor_id: "f-existing",
  kind: "totp",
  label: "Authenticator app",
  confirmed: true,
  created_at: null,
  last_used_at: null,
};
const ENROLL_OK: Reply = {
  status: 200,
  body: { factor_id: "f-new", secret: "JBSWY3DPEHPK3PXP", provisioning_uri: "otpauth://totp/x" },
};
// The Phase 5 recovery-code format: two groups of five hex characters.
const NEW_FORMAT_CODES = ["a1b2c-3d4e5", "f6a7b-8c9d0"];

let unhandled: unknown[] = [];
const onUnhandled = (reason: unknown) => unhandled.push(reason);

beforeEach(() => {
  unhandled = [];
  process.on("unhandledRejection", onUnhandled);
});
afterEach(() => {
  process.off("unhandledRejection", onUnhandled);
  vi.restoreAllMocks();
});

describe("MfaSettings", () => {
  it("offers step-up when enrolling a second app needs a recent verification, then retries enrollment", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP_FACTOR] } }],
      "POST /api/v1/auth/mfa/totp/enroll": [STEP_UP_REQUIRED, ENROLL_OK],
      "POST /api/v1/auth/mfa/step-up": [{ status: 200, body: { aal: 2 } }],
    });
    render(<MfaSettings />);

    fireEvent.click(await screen.findByRole("button", { name: "Add another authenticator app" }));

    const input = await screen.findByLabelText(/authenticator app you've already set up/);
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));

    const heading = await screen.findByRole("heading", { name: "Set up your authenticator app" });
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
    // Focus moves in the step's mount effect, which can run just after the
    // commit that made the heading findable - wait for it, don't race it.
    await waitFor(() => expect(heading).toHaveFocus());
    expect(calls.filter((c) => c.key === "POST /api/v1/auth/mfa/totp/enroll")).toHaveLength(2);
    expect(unhandled).toEqual([]);
  });

  it("shows an error instead of failing silently when starting enrollment fails", async () => {
    mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [] } }],
      "POST /api/v1/auth/mfa/totp/enroll": [{ status: 500, body: { code: "server_error" } }],
    });
    render(<MfaSettings />);

    fireEvent.click(await screen.findByRole("button", { name: "Add an authenticator app" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    await waitFor(() => expect(unhandled).toEqual([]));
  });

  it("steps up when confirmation needs it, retries with the typed code, and shows new-format recovery codes once", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [] } }],
      "POST /api/v1/auth/mfa/totp/enroll": [ENROLL_OK],
      "POST /api/v1/auth/mfa/totp/confirm": [
        STEP_UP_REQUIRED,
        { status: 200, body: { recovery_codes: NEW_FORMAT_CODES } },
      ],
      "POST /api/v1/auth/mfa/step-up": [{ status: 200, body: { aal: 2 } }],
    });
    render(<MfaSettings />);

    fireEvent.click(await screen.findByRole("button", { name: "Add an authenticator app" }));
    fireEvent.change(await screen.findByLabelText("Enter the 6-digit code"), {
      target: { value: "654321" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    const stepUpInput = await screen.findByLabelText(/authenticator app you've already set up/);
    fireEvent.change(stepUpInput, { target: { value: "111222" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));

    const heading = await screen.findByRole("heading", { name: "Save your recovery codes" });
    await waitFor(() => expect(heading).toHaveFocus());
    for (const code of NEW_FORMAT_CODES) expect(screen.getByText(code)).toBeInTheDocument();

    const confirms = calls.filter((c) => c.key === "POST /api/v1/auth/mfa/totp/confirm");
    expect(confirms.map((c) => c.body)).toEqual([
      { factor_id: "f-new", code: "654321" },
      { factor_id: "f-new", code: "654321" },
    ]);
  });

  it("accepts a new-format recovery code for step-up and ties the error to the field", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP_FACTOR] } }],
      "DELETE /api/v1/auth/mfa/factors/f-existing": [STEP_UP_REQUIRED],
      "POST /api/v1/auth/mfa/step-up": [{ status: 400, body: { code: "mfa_code_invalid" } }],
    });
    render(<MfaSettings />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove Authenticator app" }));
    const input = await screen.findByLabelText(/recovery code/);
    fireEvent.change(input, { target: { value: " a1b2c-3d4e5 " } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/works only once/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")).toBe(alert.id);
    expect(calls.find((c) => c.key === "POST /api/v1/auth/mfa/step-up")?.body).toEqual({
      code: "a1b2c-3d4e5",
    });
  });

  it("reports a removal failure that is not a step-up request", async () => {
    mockApi({
      "GET /api/v1/auth/mfa/factors": [{ status: 200, body: { factors: [TOTP_FACTOR] } }],
      "DELETE /api/v1/auth/mfa/factors/f-existing": [{ status: 429, body: { code: "rate_limited" } }],
    });
    render(<MfaSettings />);

    fireEvent.click(await screen.findByRole("button", { name: "Remove Authenticator app" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });
});
