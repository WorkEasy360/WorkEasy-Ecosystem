import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expectNoViolations, mockApi, ok } from "@/features/controlplane/testSupport";
import { ChangeEmailForm } from "./ChangeEmailForm";
import { ConfirmEmailScreen } from "@/features/auth/SelfService";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/confirm-email",
}));

afterEach(() => window.history.replaceState(null, "", "/"));

describe("changing the sign-in email (ADR-066)", () => {
  it("asks for the new address and the current password in a popup, then says where the link went", async () => {
    const calls = mockApi({ "POST /api/v1/auth/email": [{ status: 202, body: { status: "confirmation_sent" } }] });
    const { container } = render(
      <main>
        <ChangeEmailForm currentEmail="rahul@abccompany.com" />
      </main>,
    );
    expect(screen.getByText("rahul@abccompany.com")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change sign-in email" }));
    const popup = screen.getByRole("dialog", { name: "Change sign-in email" });
    await expectNoViolations(container.ownerDocument.body);
    const send = within(popup).getByRole("button", { name: "Send confirmation link" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("New sign-in email"), { target: { value: " rahul.new@abccompany.com " } });
    fireEvent.change(within(popup).getByLabelText("Current password"), { target: { value: "my passphrase" } });
    fireEvent.click(send);
    expect(await screen.findByRole("status")).toHaveTextContent("We sent a confirmation link to rahul.new@abccompany.com");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls[0].body).toEqual({ new_email: "rahul.new@abccompany.com", password: "my passphrase" });
  });

  it("says a wrong password in words and keeps the popup open", async () => {
    mockApi({ "POST /api/v1/auth/email": [{ status: 400, body: { code: "current_password_incorrect" } }] });
    render(<ChangeEmailForm currentEmail="rahul@abccompany.com" />);
    fireEvent.click(screen.getByRole("button", { name: "Change sign-in email" }));
    const popup = screen.getByRole("dialog", { name: "Change sign-in email" });
    fireEvent.change(within(popup).getByLabelText("New sign-in email"), { target: { value: "x@abccompany.com" } });
    fireEvent.change(within(popup).getByLabelText("Current password"), { target: { value: "wrong" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Send confirmation link" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("The current password is not correct.");
  });

  it("with two-step verification on, asks for the code too and sends it (2026-11-27)", async () => {
    const calls = mockApi({ "POST /api/v1/auth/email": [{ status: 202, body: { status: "confirmation_sent" } }] });
    render(<ChangeEmailForm currentEmail="rahul@abccompany.com" twoStep />);
    fireEvent.click(screen.getByRole("button", { name: "Change sign-in email" }));
    const popup = screen.getByRole("dialog", { name: "Change sign-in email" });
    fireEvent.change(within(popup).getByLabelText("New sign-in email"), { target: { value: "rahul.new@abccompany.com" } });
    fireEvent.change(within(popup).getByLabelText("Current password"), { target: { value: "my passphrase" } });
    const send = within(popup).getByRole("button", { name: "Send confirmation link" });
    expect(send).toBeDisabled();
    fireEvent.change(within(popup).getByLabelText("Two-step code"), { target: { value: " 123456 " } });
    fireEvent.click(send);
    expect(await screen.findByRole("status")).toHaveTextContent("We sent a confirmation link to rahul.new@abccompany.com");
    expect(calls[0].body).toEqual({ new_email: "rahul.new@abccompany.com", password: "my passphrase", code: "123456" });
  });

  it("when the caller did not know two-step is on, the server's refusal brings the code field", async () => {
    const calls = mockApi({
      "POST /api/v1/auth/email": [
        { status: 400, body: { code: "two_step_code_invalid", field: "code" } },
        { status: 202, body: { status: "confirmation_sent" } },
      ],
    });
    render(<ChangeEmailForm currentEmail="rahul@abccompany.com" />);
    fireEvent.click(screen.getByRole("button", { name: "Change sign-in email" }));
    const popup = screen.getByRole("dialog", { name: "Change sign-in email" });
    expect(within(popup).queryByLabelText("Two-step code")).not.toBeInTheDocument();
    fireEvent.change(within(popup).getByLabelText("New sign-in email"), { target: { value: "x@abccompany.com" } });
    fireEvent.change(within(popup).getByLabelText("Current password"), { target: { value: "my passphrase" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Send confirmation link" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("Two-step verification is on");
    fireEvent.change(within(popup).getByLabelText("Two-step code"), { target: { value: "654321" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Send confirmation link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("We sent a confirmation link to x@abccompany.com");
    expect(calls.map((c) => c.body)).toEqual([
      { new_email: "x@abccompany.com", password: "my passphrase" },
      { new_email: "x@abccompany.com", password: "my passphrase", code: "654321" },
    ]);
  });
});

describe("the emailed confirmation link (ADR-066)", () => {
  it("confirms only on a click - a mail scanner opening the link changes nothing", async () => {
    window.history.replaceState(null, "", "/confirm-email#token=tok-abc");
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/email/confirm": [ok({ status: "email_changed" })],
    });
    render(<ConfirmEmailScreen />);
    const button = await screen.findByRole("button", { name: "Confirm new email" });
    expect(calls.map((c) => c.key)).not.toContain("POST /api/v1/public/email/confirm");
    // The token left the address bar at once.
    expect(window.location.hash).toBe("");
    fireEvent.click(button);
    expect(await screen.findByRole("heading", { name: "Sign-in email changed" })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/v1/public/email/confirm")?.body).toEqual({ token: "tok-abc" });
  });

  it("says a spent or taken link is not valid", async () => {
    window.history.replaceState(null, "", "/confirm-email-spent#token=tok-old");
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/email/confirm": [{ status: 404, body: { code: "link_invalid" } }],
    });
    render(<ConfirmEmailScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm new email" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is not valid");
  });
});
