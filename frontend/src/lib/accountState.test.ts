import { afterEach, describe, expect, it, vi } from "vitest";
import { forgetAccountState } from "./accountState";

describe("forgetting a person's browser state (ADR-066)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("forgets what names a person, their records or their choices", () => {
    for (const key of [
      "w360.org.notifications.seen.acme",
      "w360.admin.recent",
      "w360.admin.notifications.seen",
      "w360.store-return",
      "w360.store-return-to",
    ]) {
      localStorage.setItem(key, "x");
    }
    sessionStorage.setItem("w360.admin.partners.list", "[{\"name\":\"Acme Partners\"}]");
    sessionStorage.setItem("w360.cart.acme", "[]");
    sessionStorage.setItem("w360.store-org", "acme");
    forgetAccountState();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("keeps layout choices and what a visitor brought before signing in", () => {
    localStorage.setItem("w360.columns.members", "[]");
    localStorage.setItem("w360.org.rail", "open");
    localStorage.setItem("w360.purchase-intent", "{}");
    localStorage.setItem("someone-elses.key", "1");
    sessionStorage.setItem("w360.referral-token", "t");
    sessionStorage.setItem("w360.admin.menu.partners", "open");
    forgetAccountState();
    expect(localStorage.getItem("w360.columns.members")).toBe("[]");
    expect(localStorage.getItem("w360.org.rail")).toBe("open");
    expect(localStorage.getItem("w360.purchase-intent")).toBe("{}");
    expect(localStorage.getItem("someone-elses.key")).toBe("1");
    expect(sessionStorage.getItem("w360.referral-token")).toBe("t");
    expect(sessionStorage.getItem("w360.admin.menu.partners")).toBe("open");
  });

  it("never throws when storage is blocked", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => forgetAccountState()).not.toThrow();
  });
});
