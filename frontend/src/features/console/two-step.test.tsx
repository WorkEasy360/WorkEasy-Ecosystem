import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { TwoStepPanel } from "./TwoStepPanel";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/account/security",
}));

/**
 * Accounts V3: two-step verification settings (the note's 8.3). Nothing is on until a
 * first code is confirmed; the recovery codes are shown once and must be acknowledged;
 * every later change asks for the password and a current code. The secret and the
 * codes live in the page only - never in a URL.
 */

const OFF = { available: true, enabled: false, method: null, enabled_at: null, recovery_codes_remaining: 0 };
const ON = { available: true, enabled: true, method: "totp", enabled_at: "2026-11-01T09:00:00Z", recovery_codes_remaining: 2 };
const SETUP = {
  secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret
  otpauth_uri: "otpauth://totp/WorkEasy360:ravi%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=WorkEasy360",
  qr_svg_data_uri: "data:image/svg+xml;base64,PHN2Zy8+",
  expires_at: "2026-11-20T10:00:00Z",
};
const CODES = Array.from({ length: 10 }, (_, i) => `abcde-1234${i}`);

afterEach(() => vi.restoreAllMocks());

describe("two-step verification", () => {
  it("is not offered where the deployment has no two-step key", async () => {
    mockApi({ "GET /api/v1/auth/two-step": [ok({ ...OFF, available: false })] });
    render(<TwoStepPanel />);
    expect(await screen.findByText("Not available yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Set up/ })).not.toBeInTheDocument();
  });

  it("turns on only after a first code: password, then the QR picture and key, then ten recovery codes shown once", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/two-step": [ok(OFF), ok({ ...ON, recovery_codes_remaining: 10 })],
      "POST /api/v1/auth/two-step/setup": [ok(SETUP)],
      "POST /api/v1/auth/two-step/setup/confirm": [
        { status: 400, body: { code: "two_step_code_invalid", field: "code" } },
        ok({ enabled: true, recovery_codes: CODES, other_sessions_ended: true }),
      ],
    });
    const { container } = render(
      <main>
        <TwoStepPanel />
      </main>,
    );
    expect(await screen.findByText("Off")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Set up two-step verification" }));
    // Off: the password only - there is no current code yet.
    expect(screen.queryByLabelText(/6-digit code/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "my passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const qr = await screen.findByRole("img", { name: /QR code/ });
    expect(qr).toHaveAttribute("src", SETUP.qr_svg_data_uri);
    expect(screen.getByText("JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /authenticator app on this device/ })).toHaveAttribute("href", SETUP.otpauth_uri);
    expect(calls.find((c) => c.key === "POST /api/v1/auth/two-step/setup")?.body).toEqual({ password: "my passphrase" }); // pragma: allowlist secret
    // Nothing in the address bar, nothing stored.
    expect(window.location.href).not.toContain(SETUP.secret);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(SETUP.secret);
    await expectNoViolations(container);

    fireEvent.change(screen.getByLabelText("6-digit code from the app"), { target: { value: "000 000" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and turn on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code isn't right");
    fireEvent.change(screen.getByLabelText("6-digit code from the app"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm and turn on" }));

    const list = await screen.findByRole("list", { name: "Recovery codes" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(CODES);
    expect(calls.filter((c) => c.key === "POST /api/v1/auth/two-step/setup/confirm").map((c) => c.body)).toEqual([
      { code: "000000" },
      { code: "123456" },
    ]);
    expect(screen.getByRole("button", { name: "Copy codes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download codes" })).toBeInTheDocument();
    await expectNoViolations(container);
    const done = screen.getByRole("button", { name: "Done" });
    expect(done).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I saved these codes somewhere safe"));
    fireEvent.click(done);
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Two-step verification is on. Your other devices were signed out.");
    expect(screen.getByText("10 recovery codes left.")).toBeInTheDocument();
    // Shown once: gone from the page.
    expect(screen.queryByText(CODES[0])).not.toBeInTheDocument();
  });

  it("new recovery codes and turning off each ask for the password and a current code", async () => {
    const NEW = Array.from({ length: 10 }, (_, i) => `fghij-5678${i}`);
    const calls = mockApi({
      "GET /api/v1/auth/two-step": [ok(ON), ok({ ...ON, recovery_codes_remaining: 10 }), ok(OFF)],
      "POST /api/v1/auth/two-step/recovery-codes": [ok({ recovery_codes: NEW })],
      "POST /api/v1/auth/two-step/disable": [{ status: 400, body: { code: "password_invalid", field: "password" } }, ok({ enabled: false })],
    });
    render(<TwoStepPanel />);
    expect(await screen.findByText(/2 recovery codes left/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New recovery codes" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.change(screen.getByLabelText("6-digit code or a recovery code"), { target: { value: "654 321" } });
    fireEvent.click(screen.getByRole("button", { name: "Make new codes" }));
    expect(await screen.findByText(NEW[0])).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/auth/two-step/recovery-codes")?.body).toEqual({ password: "pw", code: "654321" }); // pragma: allowlist secret
    fireEvent.click(screen.getByLabelText("I saved these codes somewhere safe"));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(await screen.findByText("New recovery codes are ready. The old ones no longer work.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.change(screen.getByLabelText("6-digit code or a recovery code"), { target: { value: "ABCDE-12345" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The password is not correct.");
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "right" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(await screen.findByText(/Two-step verification is off/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Set up two-step verification" })).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "POST /api/v1/auth/two-step/disable").map((c) => c.body)).toEqual([
      { password: "wrong", code: "abcde-12345" }, // pragma: allowlist secret
      { password: "right", code: "abcde-12345" }, // pragma: allowlist secret
    ]);
  });

  it("replacing the authenticator asks for a current code first, and Cancel changes nothing", async () => {
    const calls = mockApi({
      "GET /api/v1/auth/two-step": [ok(ON)],
      "POST /api/v1/auth/two-step/setup": [ok(SETUP)],
    });
    render(<TwoStepPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Replace authenticator app" }));
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.change(screen.getByLabelText("6-digit code or a recovery code"), { target: { value: "111222" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("img", { name: /QR code/ })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/auth/two-step/setup")?.body).toEqual({ password: "pw", code: "111222" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /api/v1/auth/two-step/setup/confirm")).toBe(false);
  });
});
