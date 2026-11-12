import { describe, expect, it } from "vitest";
import { contentSecurityPolicy, formTargetsFor, routePortalRequest } from "./proxy";
import { pageOwner, readPortalOrigins, servesPage, type PortalOrigins } from "./lib/portals";
import { checkoutFrom, checkoutHref, trialHref } from "./features/store/checkoutLinks";
import { rememberStoreReturn, safeStorePath, takeStoreReturn } from "./features/store/storeReturn";
import { myAccountLinks } from "./features/store/links";
import { storeCanonical } from "./lib/storeMeta";
import { storeLink } from "./features/partnerpanel/ProductsReferrals";

/**
 * The Digital Store on its own host (ADR-065): https://store.workeasy360.com is the
 * marketplace, keeps every `/store/...` deep link, signs its customers in itself,
 * and takes the Store's old addresses on the customer portal over - but only once
 * its origin is configured; until then nothing changes.
 */

const FIVE = {
  admin: "https://admin.workeasy360.com",
  partner: "https://partner.workeasy360.com",
  app: "https://app.workeasy360.com",
  auth: "https://auth.workeasy360.com",
};
const WITH: PortalOrigins = { ...FIVE, store: "https://store.workeasy360.com" };
const WITHOUT: PortalOrigins = { ...FIVE, store: "" };

const route = (origins: PortalOrigins, host: string, path: string, hasSession = false) => {
  const [pathname, search = ""] = path.split("?");
  return routePortalRequest({ origins, host, pathname, search: search ? `?${search}` : "", hasSession });
};

describe("the Store's own origin", () => {
  it("is optional: four origins without it are still multi-portal mode", () => {
    const env = {
      W360_ADMIN_ORIGIN: FIVE.admin,
      W360_PARTNER_ORIGIN: FIVE.partner,
      W360_APP_ORIGIN: FIVE.app,
      W360_AUTH_ORIGIN: FIVE.auth,
    };
    expect(readPortalOrigins(env)).toEqual(WITHOUT);
    expect(readPortalOrigins({ ...env, W360_STORE_ORIGIN: "https://store.workeasy360.com/" })).toEqual(WITH);
    expect(readPortalOrigins({ ...env, W360_STORE_ORIGIN: "https://*.workeasy360.com" })).toEqual(WITHOUT);
    expect(readPortalOrigins({ W360_STORE_ORIGIN: WITH.store })).toBeNull();
  });

  it("owns /store when it exists, and the customer portal does until then", () => {
    expect(pageOwner("/store/product/easy-crm", WITH)).toBe("store");
    expect(pageOwner("/store/product/easy-crm", WITHOUT)).toBe("app");
    expect(servesPage("app", "/store", WITHOUT)).toBe(true);
    expect(servesPage("app", "/store", WITH)).toBe(false);
    expect(servesPage("store", "/store/checkout", WITH)).toBe(true);
  });
});

describe("routing on the Store's host", () => {
  const host = "store.workeasy360.com";

  it("serves the marketplace at its root (app/page.tsx renders it there)", () => {
    expect(route(WITH, host, "/")).toEqual({ kind: "serve" });
  });

  it("keeps every Store deep link, signed in or not", () => {
    for (const path of ["/store", "/store/product/easy-crm", "/store/products?q=crm", "/store/checkout?plan=x", "/store/recharge", "/store/acme-partners"]) {
      expect(route(WITH, host, path)).toEqual({ kind: "serve" });
    }
  });

  it("signs its own customers up and in, with or without mode=user", () => {
    for (const path of ["/login", "/login?mode=user&next=%2Fstore%2Fcheckout", "/signup", "/verify-email", "/forgot-password", "/reset-password", "/r/ABCD1234"]) {
      expect(route(WITH, host, path)).toEqual({ kind: "serve" });
    }
    // Another portal's sign-in is that portal's own page.
    expect(route(WITH, host, "/login?mode=partner")).toEqual({ kind: "redirect", location: `${FIVE.partner}/login` });
  });

  it("sends the consoles' pages to their own hosts", () => {
    expect(route(WITH, host, "/o/acme/wallets", true)).toEqual({ kind: "redirect", location: `${FIVE.app}/o/acme/wallets` });
    expect(route(WITH, host, "/admin", true)).toEqual({ kind: "redirect", location: `${FIVE.admin}/admin` });
    expect(route(WITH, host, "/dashboard", true)).toEqual({ kind: "redirect", location: `${FIVE.app}/dashboard` });
  });

  it("serves the development test provider's page, where a Store payment returns", () => {
    expect(route(WITH, host, "/billing/test-checkout", true)).toEqual({ kind: "serve" });
  });
});

describe("the Store's old addresses on the customer portal", () => {
  it("open the same page on the Store's host - query kept", () => {
    expect(route(WITH, "app.workeasy360.com", "/store/product/easy-crm?ref=1")).toEqual({
      kind: "redirect",
      location: "https://store.workeasy360.com/store/product/easy-crm?ref=1",
    });
    expect(route(WITH, "app.workeasy360.com", "/store")).toEqual({ kind: "redirect", location: "https://store.workeasy360.com/store" });
  });

  it("stay on the customer portal while the Store has no host", () => {
    expect(route(WITHOUT, "app.workeasy360.com", "/store/product/easy-crm")).toEqual({ kind: "serve" });
    expect(route(WITHOUT, "app.workeasy360.com", "/")).toEqual({ kind: "serve" });
  });
});

describe("the forms a Store page may submit elsewhere", () => {
  it("is User Admin only (My Account), from the Store's host only", () => {
    expect(formTargetsFor("store.workeasy360.com", WITH)).toEqual([FIVE.app]);
    expect(formTargetsFor("app.workeasy360.com", WITH)).toEqual([]);
    expect(formTargetsFor("admin.workeasy360.com", WITH)).toEqual([FIVE.partner]);
    expect(formTargetsFor("store.workeasy360.com", WITHOUT)).toEqual([]);
    const csp = contentSecurityPolicy("N", false, formTargetsFor("store.workeasy360.com", WITH));
    expect(csp).toContain(`form-action 'self' ${FIVE.app};`);
  });
});

describe("what a checkout is for", () => {
  const PLAN = "11111111-2222-3333-4444-555555555555";

  it("is identifiers only, each validated", () => {
    const href = checkoutHref({ plan: PLAN, interval: "annual", quantity: 5, coupon: "LAUNCH10" });
    const ctx = checkoutFrom(new URLSearchParams(href.split("?")[1]));
    expect(ctx).toEqual({ target: { kind: "plan", plan: PLAN, interval: "annual", quantity: 5, coupon: "LAUNCH10" }, returnTo: undefined });
    expect(checkoutFrom(new URLSearchParams(trialHref("easy_crm", "crm_pro").split("?")[1]))?.target).toEqual({
      kind: "trial",
      product: "easy_crm",
      plan: "crm_pro",
    });
  });

  it("refuses anything else", () => {
    for (const q of ["", "plan=x&interval=annual", `plan=${PLAN}&interval=weekly`, "trial=Easy CRM&trial_plan=x", `plan=${PLAN}`]) {
      expect(checkoutFrom(new URLSearchParams(q))).toBeNull();
    }
    const odd = checkoutFrom(new URLSearchParams(`plan=${PLAN}&interval=monthly&quantity=-3&coupon=<x>`));
    expect(odd?.target).toEqual({ kind: "plan", plan: PLAN, interval: "monthly", quantity: 1, coupon: undefined });
  });

  it("carries a product's return address only as https, for the server to approve", () => {
    const ok = checkoutFrom(new URLSearchParams(`plan=${PLAN}&interval=annual&from=easy_crm&return_to=https%3A%2F%2Fcrm.example.com%2Fbilling`));
    expect(ok?.returnTo).toEqual({ product: "easy_crm", url: "https://crm.example.com/billing" });
    const bad = checkoutFrom(new URLSearchParams(`plan=${PLAN}&interval=annual&from=easy_crm&return_to=javascript:alert(1)`));
    expect(bad?.returnTo).toBeUndefined();
  });
});

describe("the Store page a sign-up comes back to", () => {
  it("is a plain /store path, never an address", () => {
    expect(safeStorePath("/store/checkout?plan=x")).toBe("/store/checkout?plan=x");
    for (const raw of ["https://evil.example/store", "//evil.example", "/store/../admin", "/admin", "/store%2f..", "/store\\x", 7]) {
      expect(safeStorePath(raw)).toBeNull();
    }
  });

  it("is read once", () => {
    rememberStoreReturn("/store/checkout?plan=x&interval=annual");
    expect(takeStoreReturn()).toBe("/store/checkout?plan=x&interval=annual");
    expect(takeStoreReturn()).toBeNull();
    rememberStoreReturn("https://evil.example");
    expect(takeStoreReturn()).toBeNull();
  });
});

describe("My Account", () => {
  it("opens the six User Admin pages the brief names, for the chosen organization", () => {
    expect(myAccountLinks("acme").map((l) => [l.label, l.path])).toEqual([
      ["My Products", "/o/acme/apps"],
      ["Billing & Subscriptions", "/o/acme/settings/billing"],
      ["Wallets & Credits", "/o/acme/wallets"],
      ["Organization & Team", "/o/acme/settings/organization"],
      ["Support", "/o/acme/help"],
      ["Account & Security", "/o/acme/settings/security"],
    ]);
    expect(new Set(myAccountLinks(null).map((l) => l.path))).toEqual(new Set(["/dashboard"]));
  });
});

describe("search metadata", () => {
  it("names the Store's own host as canonical, the marketplace at its root", () => {
    const env = { W360_STORE_ORIGIN: WITH.store, W360_APP_ORIGIN: FIVE.app };
    expect(storeCanonical("/store", env)).toBe("https://store.workeasy360.com/");
    expect(storeCanonical("/store/product/easy-crm", env)).toBe("https://store.workeasy360.com/store/product/easy-crm");
    expect(storeCanonical("/store", { W360_APP_ORIGIN: FIVE.app })).toBe("https://app.workeasy360.com/store");
  });
});

describe("a partner's Store referral link", () => {
  it("records the visit on the Store's host when it has one", () => {
    expect(storeLink("https://app.workeasy360.com/r/ABCD1234", WITH.store)).toBe("https://store.workeasy360.com/r/ABCD1234?to=store");
    expect(storeLink("https://app.workeasy360.com/r/ABCD1234")).toBe("https://app.workeasy360.com/r/ABCD1234?to=store");
  });
});
