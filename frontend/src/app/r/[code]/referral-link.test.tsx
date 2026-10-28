import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

import { mockApi, ok } from "@/features/controlplane/testSupport";
import ReferralLinkPage from "./page";

/**
 * `/r/<code>` (ADR-053): the session is read first - a first visit has no CSRF
 * cookie until then - and only then is the visit captured; the visitor goes
 * where the server said, and never anywhere off the platform.
 */

let code = "AMAN360";
vi.mock("next/navigation", () => ({ useParams: () => ({ code }) }));

const replace = vi.fn();
beforeEach(() => {
  replace.mockReset();
  vi.stubGlobal("location", { ...window.location, replace });
});

describe("a referral link", () => {
  it("reads the session before capturing, then opens the server's path", async () => {
    code = "AMAN360";
    const calls = mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/referrals": [{ status: 201, body: { token: "t1", destination: "/products/easy-crm" } }],
    });
    render(<ReferralLinkPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/products/easy-crm"));
    expect(calls.map((c) => c.key)).toEqual(["GET /api/v1/auth/session", "POST /api/v1/public/referrals"]);
    expect(calls[1].body).toEqual({ code: "AMAN360" });
  });

  it("never follows a destination that is not a platform path", async () => {
    code = "AMAN360";
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/referrals": [{ status: 201, body: { token: "t1", destination: "//evil.example/x" } }],
    });
    render(<ReferralLinkPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/signup"));
  });

  it("opens sign-up for an unknown or malformed code", async () => {
    code = "NOPE404";
    mockApi({
      "GET /api/v1/auth/session": [ok({ authenticated: false })],
      "POST /api/v1/public/referrals": [{ status: 404, body: { code: "not_found" } }],
    });
    render(<ReferralLinkPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/signup"));
    code = "<script>";
    replace.mockReset();
    render(<ReferralLinkPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/signup"));
  });
});
