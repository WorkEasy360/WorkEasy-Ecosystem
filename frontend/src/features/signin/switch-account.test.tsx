import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { mockApi, ok } from "@/features/controlplane/testSupport";
import { SwitchAccountFinish } from "./SwitchAccountFinish";

const replace = vi.fn();
beforeEach(() => {
  replace.mockReset();
  vi.stubGlobal("location", { ...window.location, hash: "#handle=signed.handle", pathname: "/switch-account", replace, reload: vi.fn() });
});
afterEach(() => vi.unstubAllGlobals());

describe("Switch Account on the central sign-in host (ADR-066)", () => {
  it("hands the fragment's hand-over to the server and opens the sign-in page it names", async () => {
    sessionStorage.setItem("w360.store-org", "acme");
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true })],
      "POST /api/v1/auth/switch-account/finish": [ok({ redirect_url: "https://app.example.com/login?switched=1" })],
    });
    render(<SwitchAccountFinish />);
    expect(screen.getByText("Switching account…")).toBeInTheDocument();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("https://app.example.com/login?switched=1"));
    // CSRF cookie first, then the hand-over - never a destination of its own.
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "POST /api/v1/auth/switch-account/finish"]);
    expect(calls[1].body).toEqual({ handle: "signed.handle" });
    expect(sessionStorage.getItem("w360.store-org")).toBeNull();
  });

  it("says so, and offers to try again, when the server cannot be reached", async () => {
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: true })],
      "POST /api/v1/auth/switch-account/finish": [{ status: 500, body: {} }],
    });
    render(<SwitchAccountFinish />);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not finish switching accounts");
    expect(replace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(window.location.reload).toHaveBeenCalled();
  });
});
