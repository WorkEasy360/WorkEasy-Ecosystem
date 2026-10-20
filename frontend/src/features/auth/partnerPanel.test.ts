import { describe, expect, it } from "vitest";
import { readPartnerPanelUrl } from "./partnerPanel";

describe("readPartnerPanelUrl", () => {
  it("accepts an absolute http(s) URL", () => {
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: " http://localhost:5180/login " })).toBe("http://localhost:5180/login");
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: "https://partner.example.com" })).toBe("https://partner.example.com/");
  });

  it("ignores unset, relative, non-http and credential-bearing values", () => {
    expect(readPartnerPanelUrl({})).toBeUndefined();
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: "" })).toBeUndefined();
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: "/partner" })).toBeUndefined();
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: "javascript:alert(1)" })).toBeUndefined();
    expect(readPartnerPanelUrl({ W360_PARTNER_PANEL_URL: "https://user:pw@partner.example.com" })).toBeUndefined();
  });
});
