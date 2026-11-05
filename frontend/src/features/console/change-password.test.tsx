import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { expectNoViolations, mockApi, type Reply } from "@/features/controlplane/testSupport";
import { ChangePasswordForm } from "./ChangePasswordForm";

/**
 * Changing the password (2026-11-05, every form in a popup): on a settings page
 * the fields open in a popup; the gates keep the form on the page.
 */

const ok: Reply = { status: 200, body: { ok: true } };

function fill(scope: HTMLElement, current: string, next: string, confirm: string) {
  fireEvent.change(within(scope).getByLabelText("Current password"), { target: { value: current } });
  fireEvent.change(within(scope).getByLabelText("New password"), { target: { value: next } });
  fireEvent.change(within(scope).getByLabelText("Confirm the new password"), { target: { value: confirm } });
}

describe("change password in a popup", () => {
  it("opens, refuses two different passwords without sending, then changes it and closes", async () => {
    const calls = mockApi({ "POST /api/v1/auth/password": [ok] });
    const onChanged = vi.fn();
    const { container } = render(
      <main>
        <ChangePasswordForm popup onChanged={onChanged} />
      </main>,
    );
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    const popup = screen.getByRole("dialog", { name: "Change password" });
    await expectNoViolations(container.ownerDocument.body);

    fill(popup, "old long passphrase", "first long passphrase", "second long passphrase");
    fireEvent.click(within(popup).getByRole("button", { name: "Change password" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("The new passwords don't match.");
    expect(calls).toEqual([]);

    fireEvent.change(within(popup).getByLabelText("Confirm the new password"), { target: { value: "first long passphrase" } });
    fireEvent.click(within(popup).getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed. Your other sessions have been signed out.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.map((c) => c.body)).toEqual([{ current_password: "old long passphrase", new_password: "first long passphrase" }]);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("keeps the popup open with the server's reason when the current password is wrong", async () => {
    mockApi({ "POST /api/v1/auth/password": [{ status: 400, body: { code: "current_password_incorrect" } }] });
    render(<ChangePasswordForm popup />);
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    const popup = screen.getByRole("dialog", { name: "Change password" });
    fill(popup, "wrong", "new long passphrase", "new long passphrase");
    fireEvent.click(within(popup).getByRole("button", { name: "Change password" }));
    expect(await within(popup).findByRole("alert")).toHaveTextContent("The current password is not correct.");
    expect(screen.getByRole("dialog", { name: "Change password" })).toBeInTheDocument();

    fireEvent.click(within(popup).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays on the page without `popup` (the password gates)", () => {
    mockApi({});
    render(<ChangePasswordForm />);
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
