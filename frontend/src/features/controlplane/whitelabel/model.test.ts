import { describe, it, expect } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { StoredBrand } from "@/lib/api/partnerConsole";
import {
  ALL_GROUPS,
  LOGO_CONTENT_MESSAGE,
  LOGO_SIZE_MESSAGE,
  LOGO_TYPE_MESSAGE,
  brandBody,
  brandingOnDomain,
  checkLogo,
  draftOf,
  groupDirty,
  hostnameProblem,
  isHexColour,
  isHttpsUrl,
  serverProblem,
  validateGroup,
  validateGroups,
} from "./model";

/**
 * The wizard's model: what a draft is checked for, what a save sends, how a logo
 * is judged by its own bytes, and how the server's problems are read.
 */

const STORED: StoredBrand = {
  brand_name: "Acme",
  logo_file_id: "f1",
  accent_color: "#1a56db",
  theme: { accent: "#1a56db" },
  support_email: "help@acme.example",
  support_url: "https://help.acme.example",
  portal: {
    short_name: "A",
    support_name: "Sam",
    support_phone: "+44 20 7946 0000",
    website_url: "https://acme.example",
    welcome_message: "Hello",
    footer_text: "Footer",
    sender_name: "Sender",
    reply_to_email: "r@acme.example",
    login_show_welcome: false,
    login_show_support: true,
    use_for_invitations: true,
  },
  version: 7,
  updated_at: "2026-01-01T00:00:00Z",
};

describe("draftOf", () => {
  it("reads every field of a stored brand", () => {
    expect(draftOf(STORED)).toEqual({
      brandName: "Acme",
      shortName: "A",
      accent: "#1a56db",
      supportName: "Sam",
      supportEmail: "help@acme.example",
      supportPhone: "+44 20 7946 0000",
      supportUrl: "https://help.acme.example",
      websiteUrl: "https://acme.example",
      welcome: "Hello",
      footer: "Footer",
      senderName: "Sender",
      replyTo: "r@acme.example",
      useForInvitations: true,
      loginShowWelcome: false,
      loginShowSupport: true,
    });
  });

  it("starts empty with both sign-in choices on, as the server does", () => {
    const empty = draftOf(null);
    expect(empty.brandName).toBe("");
    expect(empty.loginShowWelcome).toBe(true);
    expect(empty.loginShowSupport).toBe(true);
    expect(empty.useForInvitations).toBe(false);
  });

  it("copes with a stored brand that predates the portal", () => {
    const old = { ...STORED, portal: undefined } as unknown as StoredBrand;
    expect(draftOf(old).shortName).toBe("");
    expect(draftOf(old).loginShowWelcome).toBe(true);
  });
});

describe("groupDirty", () => {
  it("is false for a draft that matches what is stored, ignoring spaces and the case of a colour", () => {
    const draft = { ...draftOf(STORED), brandName: "  Acme ", accent: "#1A56DB" };
    for (const group of ALL_GROUPS) expect(groupDirty(draft, STORED, group)).toBe(false);
  });

  it("is true only for the group that changed", () => {
    const draft = { ...draftOf(STORED), welcome: "Changed" };
    expect(groupDirty(draft, STORED, "portal")).toBe(true);
    expect(groupDirty(draft, STORED, "identity")).toBe(false);
    expect(groupDirty(draft, STORED, "communication")).toBe(false);
    expect(groupDirty(draft, STORED, "login")).toBe(false);
    expect(groupDirty({ ...draftOf(STORED), loginShowSupport: false }, STORED, "login")).toBe(true);
  });

  it("always counts the identity as unsaved while there is no brand", () => {
    expect(groupDirty(draftOf(null), null, "identity")).toBe(true);
    expect(groupDirty(draftOf(null), null, "portal")).toBe(false);
  });
});

describe("validateGroup", () => {
  const ok = draftOf(STORED);

  it("accepts a complete, well-formed identity", () => {
    expect(validateGroup(ok, "identity")).toEqual({});
  });

  it("requires a brand name and nothing else, and allows every optional field to be empty", () => {
    const blank = { ...draftOf(null) };
    expect(Object.keys(validateGroup(blank, "identity"))).toEqual(["brandName"]);
    expect(validateGroup({ ...blank, brandName: "Acme" }, "identity")).toEqual({});
    expect(validateGroup(blank, "portal")).toEqual({});
    expect(validateGroup(blank, "communication")).toEqual({});
    expect(validateGroup(blank, "login")).toEqual({});
  });

  it("holds the length limits the server holds", () => {
    expect(validateGroup({ ...ok, brandName: "x".repeat(81) }, "identity").brandName).toMatch(/80 characters or fewer/);
    expect(validateGroup({ ...ok, shortName: "x".repeat(25) }, "identity").shortName).toMatch(/24 characters or fewer/);
    expect(validateGroup({ ...ok, shortName: "x".repeat(24) }, "identity").shortName).toBeUndefined();
    expect(validateGroup({ ...ok, welcome: "x".repeat(281) }, "portal").welcome).toMatch(/280 characters or fewer/);
    expect(validateGroup({ ...ok, footer: "x".repeat(161) }, "portal").footer).toMatch(/160 characters or fewer/);
    expect(validateGroup({ ...ok, senderName: "x".repeat(81) }, "communication").senderName).toMatch(/80 characters or fewer/);
  });

  it("refuses angle brackets and line breaks in every plain-text field, never stripping them", () => {
    const hostile = ["<b>x</b>", "a > b", "line\nbreak", "tab\there", `x${String.fromCharCode(8232)}y`];
    for (const text of hostile) {
      expect(validateGroup({ ...ok, brandName: text }, "identity").brandName, text).toBeTruthy();
      expect(validateGroup({ ...ok, shortName: text }, "identity").shortName, text).toBeTruthy();
      expect(validateGroup({ ...ok, supportName: text }, "identity").supportName, text).toBeTruthy();
      expect(validateGroup({ ...ok, welcome: text }, "portal").welcome, text).toBeTruthy();
      expect(validateGroup({ ...ok, footer: text }, "portal").footer, text).toBeTruthy();
      expect(validateGroup({ ...ok, senderName: text }, "communication").senderName, text).toBeTruthy();
    }
  });

  it("checks the colour, the addresses, the phone number and the links", () => {
    expect(validateGroup({ ...ok, accent: "red" }, "identity").accent).toMatch(/six-digit hex/);
    expect(validateGroup({ ...ok, accent: "#12345" }, "identity").accent).toBeTruthy();
    expect(validateGroup({ ...ok, accent: "#ABCDEF" }, "identity").accent).toBeUndefined();
    expect(validateGroup({ ...ok, supportEmail: "nope" }, "identity").supportEmail).toBeTruthy();
    expect(validateGroup({ ...ok, supportPhone: "123" }, "identity").supportPhone).toBeTruthy();
    expect(validateGroup({ ...ok, supportPhone: "abc 12345" }, "identity").supportPhone).toBeTruthy();
    expect(validateGroup({ ...ok, supportUrl: "http://x.example.com" }, "identity").supportUrl).toBeTruthy();
    expect(validateGroup({ ...ok, websiteUrl: "javascript:alert(1)" }, "identity").websiteUrl).toBeTruthy();
    expect(validateGroup({ ...ok, replyTo: "nope" }, "communication").replyTo).toBeTruthy();
  });

  it("collects the problems of several groups, keeping the first message per field", () => {
    const errors = validateGroups({ ...ok, brandName: "", welcome: "<x>" }, ["identity", "portal"]);
    expect(Object.keys(errors).sort()).toEqual(["brandName", "welcome"]);
  });
});

describe("isHexColour, isHttpsUrl and hostnameProblem", () => {
  it("accepts only a six-digit hex colour", () => {
    expect(isHexColour("#1a56db")).toBe(true);
    for (const bad of ["1a56db", "#1a56d", "#1a56dbb", "#gggggg", "red", "", "#1a56db;background:url(//evil)"]) expect(isHexColour(bad), bad).toBe(false);
  });

  it("accepts only a plain https address", () => {
    expect(isHttpsUrl("https://acme.example/help")).toBe(true);
    for (const bad of ["http://acme.example", "ftp://acme.example", "https://acme", "https://user:pw@acme.example", "https:acme.example", "https://a b.example", "//acme.example", "javascript:alert(1)", `https://${"a".repeat(300)}.example`]) {
      expect(isHttpsUrl(bad), bad).toBe(false);
    }
  });

  it("accepts a host name and nothing else", () => {
    expect(hostnameProblem("app.acme.example")).toBeNull();
    expect(hostnameProblem("  App.Acme.Example ")).toBeNull();
    for (const bad of ["", "acme", "https://app.acme.example", "app.acme.example/x", "app acme.example", "10.0.0.1", "-bad.example.com", "app..example.com"]) {
      expect(hostnameProblem(bad), bad).toBeTruthy();
    }
  });
});

describe("brandBody", () => {
  const draft = { ...draftOf(STORED), welcome: "New welcome", brandName: "Renamed" };

  it("sends the stored brand fields back unchanged when a step edits only portal words", () => {
    expect(brandBody(STORED, draft, ["portal"])).toEqual({
      brand_name: "Acme", // not "Renamed": that is the identity step's to save
      accent_color: "#1a56db",
      support_email: "help@acme.example",
      support_url: "https://help.acme.example",
      theme: { accent: "#1a56db" },
      logo_file_id: "f1",
      portal: { welcome_message: "New welcome", footer_text: "Footer" },
    });
  });

  it("sends the identity from the draft, with its own portal keys, when the identity is saved", () => {
    const body = brandBody(STORED, draft, ["identity"]);
    expect(body.brand_name).toBe("Renamed");
    expect(body.portal).toEqual({ short_name: "A", support_name: "Sam", support_phone: "+44 20 7946 0000", website_url: "https://acme.example" });
    expect(body.logo_file_id).toBe("f1");
  });

  it("sends every portal key when every group is saved, and none outside the closed set", () => {
    const keys = Object.keys(brandBody(STORED, draft, ALL_GROUPS).portal ?? {}).sort();
    expect(keys).toEqual(
      [
        "short_name",
        "support_name",
        "support_phone",
        "website_url",
        "welcome_message",
        "footer_text",
        "sender_name",
        "reply_to_email",
        "use_for_invitations",
        "login_show_welcome",
        "login_show_support",
      ].sort(),
    );
  });

  it("always carries the identity for the very first save, and a newly uploaded logo replaces the stored one", () => {
    const first = brandBody(null, { ...draftOf(null), brandName: "New" }, ["portal"]);
    expect(first.brand_name).toBe("New");
    expect(first.logo_file_id).toBeUndefined();
    expect(brandBody(STORED, draft, ["identity"], "f2").logo_file_id).toBe("f2");
  });

  it("sends no logo for a brand that has none, rather than a null", () => {
    expect("logo_file_id" in brandBody({ ...STORED, logo_file_id: null }, draft, ["portal"])).toBe(false);
  });

  it("trims what it sends", () => {
    const body = brandBody(STORED, { ...draft, brandName: "  Spaced  ", welcome: " hi " }, ALL_GROUPS);
    expect(body.brand_name).toBe("Spaced");
    expect(body.portal?.welcome_message).toBe("hi");
  });
});

describe("serverProblem", () => {
  const refused = (field: unknown, detail: unknown = "Enter a plain https:// address.") => new ApiError(400, { code: "partner_validation_error", detail, field });

  it("reads the field and the message of a validation problem, with or without the portal prefix", () => {
    expect(serverProblem(refused("portal.website_url"))).toEqual({ key: "websiteUrl", message: "Enter a plain https:// address." });
    expect(serverProblem(refused("brand_name"))?.key).toBe("brandName");
    expect(serverProblem(refused("accent_color"))?.key).toBe("accent");
    expect(serverProblem(refused("portal.welcome_message"))?.key).toBe("welcome");
    expect(serverProblem(refused("logo_file_id"))?.key).toBe("logo");
    expect(serverProblem(refused("content_type"))?.key).toBe("logo");
    expect(serverProblem(refused("hostname"))?.key).toBe("hostname");
  });

  it("returns nothing for a failure that names no field of this wizard", () => {
    expect(serverProblem(refused("something_else"))).toBeNull();
    expect(serverProblem(refused(undefined))).toBeNull();
    expect(serverProblem(refused("brand_name", null))).toBeNull();
    expect(serverProblem(new ApiError(500, { code: "boom" }))).toBeNull();
    expect(serverProblem(new Error("nope"))).toBeNull();
    expect(serverProblem(undefined)).toBeNull();
  });
});

describe("checkLogo", () => {
  const file = (bytes: number[] | string, name: string, type: string, pad = 0) =>
    new File([typeof bytes === "string" ? bytes : new Uint8Array([...bytes, ...new Array(pad).fill(0)])], name, { type });
  const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  const JPEG = [0xff, 0xd8, 0xff, 0xe0];

  it("accepts a PNG and a JPEG whose first bytes say so", async () => {
    expect(await checkLogo(file(PNG, "a.png", "image/png", 10))).toBeNull();
    expect(await checkLogo(file(JPEG, "a.jpg", "image/jpeg", 10))).toBeNull();
  });

  it("refuses every other type, however it is named", async () => {
    for (const [name, type] of [["a.svg", "image/svg+xml"], ["a.html", "text/html"], ["a.gif", "image/gif"], ["a.webp", "image/webp"], ["a.png", ""]]) {
      expect(await checkLogo(file(PNG, name, type)), name).toBe(LOGO_TYPE_MESSAGE);
    }
  });

  it("refuses a file by its size: empty, or over 512 KB", async () => {
    expect(await checkLogo(file([], "a.png", "image/png"))).toBe("That file is empty.");
    expect(await checkLogo(file(PNG, "a.png", "image/png", 512 * 1024 - PNG.length))).toBeNull(); // exactly 512 KB
    expect(await checkLogo(file(PNG, "a.png", "image/png", 512 * 1024 - PNG.length + 1))).toBe(LOGO_SIZE_MESSAGE);
  });

  it("refuses a file whose bytes do not match its declared type", async () => {
    expect(await checkLogo(file("<html><script>alert(1)</script></html>", "logo.png", "image/png"))).toBe(LOGO_CONTENT_MESSAGE);
    expect(await checkLogo(file("<svg xmlns='http://www.w3.org/2000/svg'/>", "logo.jpg", "image/jpeg"))).toBe(LOGO_CONTENT_MESSAGE);
    expect(await checkLogo(file(PNG, "logo.jpg", "image/jpeg"))).toBe(LOGO_CONTENT_MESSAGE);
    expect(await checkLogo(file(JPEG, "logo.png", "image/png"))).toBe(LOGO_CONTENT_MESSAGE);
    expect(await checkLogo(file([0x89, 0x50], "short.png", "image/png"))).toBe(LOGO_CONTENT_MESSAGE); // too short to be a PNG
  });
});

describe("brandingOnDomain", () => {
  it("is active only when the server says the domain serves the brand", () => {
    expect(brandingOnDomain("active").active).toBe(true);
    for (const state of ["none", "pending", "verified_not_active"] as const) expect(brandingOnDomain(state).active, state).toBe(false);
  });

  it("explains each state in words", () => {
    expect(brandingOnDomain("verified_not_active").detail).toMatch(/verified but not activated/);
    expect(brandingOnDomain("pending").detail).toMatch(/waiting for DNS verification/);
    expect(brandingOnDomain("none").detail).toMatch(/No custom domain/);
  });
});
