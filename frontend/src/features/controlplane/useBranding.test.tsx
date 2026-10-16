import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { applyAccent, contrastOn, hoverOf, textOnDark, textOnLight, useBranding, useOrganizationBranding } from "./useBranding";

/**
 * The colours that follow from a partner's accent. Found by the Phase 10 browser
 * check: on the branded sign-in page a dark-green accent (#0b6e4f) drawn as link
 * text on the dark theme's surface was 2.78:1 - a WCAG AA failure the unit tests
 * (which only covered the button label) could not see. Whatever accent a partner
 * picks, every text colour derived from it must read on the theme it is shown on.
 */

// Written out here, independently of the module under test.
function lum(hex: string): number {
  const c = (i: number) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * c(1) + 0.7152 * c(3) + 0.0722 * c(5);
}
const contrast = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const LIGHT_SURFACES = ["#f7f8fa", "#ffffff"];
const DARK_SURFACES = ["#0f1115", "#171a21", "#1d212a"];

// A spread of accents: the extremes, the awkward mid-tones, and a deterministic sweep.
const ACCENTS = [
  "#000000",
  "#ffffff",
  "#ffff00",
  "#0b6e4f",
  "#1a56db",
  "#808080",
  "#777777",
  "#7a1fa2",
  "#ff7f00",
  "#00ffff",
  ...Array.from({ length: 200 }, (_, i) => {
    const n = (i * 2654435761) >>> 0;
    return `#${(n & 0xffffff).toString(16).padStart(6, "0")}`;
  }),
];

describe("colours derived from a partner's accent", () => {
  it("link text reads at AA (4.5:1) on every light surface, whatever the accent", () => {
    for (const accent of ACCENTS) {
      for (const surface of LIGHT_SURFACES) {
        expect(contrast(textOnLight(accent), surface), `${accent} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("link text reads at AA on every dark surface, whatever the accent", () => {
    for (const accent of ACCENTS) {
      for (const surface of DARK_SURFACES) {
        expect(contrast(textOnDark(accent), surface), `${accent} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("leaves an accent alone when it already reads, and changes a failing one only as far as needed", () => {
    expect(textOnLight("#1a56db")).toBe("#1a56db"); // 6.4:1 on light already
    expect(textOnDark("#818cf8")).toBe("#818cf8"); // the platform's own dark-theme brand
    expect(textOnDark("#0b6e4f")).not.toBe("#0b6e4f"); // 2.8:1 on the dark surface: it must move
    // ...toward white, staying green, not jumping to white outright.
    const moved = textOnDark("#0b6e4f");
    expect(moved).not.toBe("#ffffff");
    expect(parseInt(moved.slice(3, 5), 16)).toBeGreaterThan(parseInt(moved.slice(1, 3), 16));
  });

  it("the hover fill never makes the label on it harder to read than at rest", () => {
    for (const accent of ACCENTS) {
      const label = contrastOn(accent);
      expect(contrast(label, hoverOf(accent)), accent).toBeGreaterThanOrEqual(contrast(label, accent) - 0.001);
    }
  });

  it("applyAccent sets every derived colour together, and clears them together", () => {
    const el = document.createElement("div");
    applyAccent(el, "#0b6e4f");
    for (const name of [
      "--w360-brand",
      "--w360-brand-contrast",
      "--w360-brand-hover",
      "--w360-brand-text-on-light",
      "--w360-brand-text-on-dark",
      "--w360-cta",
      "--w360-cta-contrast",
      "--w360-cta-hover",
    ]) {
      expect(el.style.getPropertyValue(name), name).toMatch(/^#[0-9a-f]{6}$/);
    }
    expect(el.style.getPropertyValue("--w360-brand")).toBe("#0b6e4f");
    applyAccent(el, null);
    expect(el.getAttribute("style") ?? "").toBe("");
  });

  it("ignores anything that is not a plain six-digit hex colour, and leaves the theme alone", () => {
    for (const hostile of ["red", "#12", "#gggggg", "url(//evil.example/x)", "#1a56db;background:url(//evil)", "expression(alert(1))", ""]) {
      const el = document.createElement("div");
      applyAccent(el, hostile);
      expect(el.getAttribute("style") ?? "", hostile).toBe("");
    }
  });
});

/** `useOrganizationBranding` (Phase 11, Step 30): the organization console's
 * own white-label brand - resolved through the live partner link, never a
 * Host header - and the accent it applies to `<html>` while mounted. */
function Probe({ slug }: { slug: string }) {
  const brand = useOrganizationBranding(slug);
  return <span data-testid="brand">{brand ? brand.brand_name : "none"}</span>;
}

describe("useOrganizationBranding", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.removeAttribute("style");
  });

  it("fetches this organization's own brand and applies its accent to <html>", async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/orgs/acme/brand");
      return new Response(
        JSON.stringify({
          brand: { brand_name: "ABC Business Cloud", accent_color: "#336699", support_email: "", support_url: "", logo_url: null },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }) as typeof fetch;

    render(<Probe slug="acme" />);
    await waitFor(() => expect(screen.getByTestId("brand")).toHaveTextContent("ABC Business Cloud"));
    expect(document.documentElement.style.getPropertyValue("--w360-brand")).toBe("#336699");
  });

  it("an organization with no white-label partner shows no brand and no accent", async () => {
    global.fetch = vi.fn(async () => new Response(JSON.stringify({ brand: null }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;

    render(<Probe slug="plain-co" />);
    await waitFor(() => expect(screen.getByTestId("brand")).toHaveTextContent("none"));
    expect(document.documentElement.getAttribute("style") ?? "").toBe("");
  });

  it("removes the accent on unmount, so switching to Super Admin or a partner portal never keeps a previous organization's colour", async () => {
    global.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({ brand: { brand_name: "ABC", accent_color: "#336699", support_email: "", support_url: "", logo_url: null } }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    ) as typeof fetch;

    const { unmount } = render(<Probe slug="acme" />);
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--w360-brand")).toBe("#336699"));
    unmount();
    expect(document.documentElement.getAttribute("style") ?? "").toBe("");
  });

  it("a failed request shows no brand rather than an error", async () => {
    global.fetch = vi.fn(async () => new Response(null, { status: 500 })) as typeof fetch;
    render(<Probe slug="acme" />);
    await waitFor(() => expect(screen.getByTestId("brand")).toHaveTextContent("none"));
  });
});

/** Since the white-label wizard, both public brand lookups carry the portal: the
 * words and support details a customer reads. A response without it (an older
 * one) must read as "none set", never as a crash. */
function HostProbe({ host }: { host?: string }) {
  const brand = useBranding(host);
  return (
    <div>
      <span data-testid="name">{brand ? brand.brand_name : "none"}</span>
      <span data-testid="short">{brand ? brand.portal.short_name : "-"}</span>
      <span data-testid="welcome">{brand ? `${brand.portal.welcome_message}|${brand.portal.login_show_welcome}|${brand.portal.login_show_support}` : "-"}</span>
    </div>
  );
}

function OrgPortalProbe() {
  const brand = useOrganizationBranding("acme");
  return <span data-testid="portal">{brand ? JSON.stringify(brand.portal) : "none"}</span>;
}

describe("the portal on the public brand", () => {
  const reply = (brand: unknown) =>
    vi.fn(async () => new Response(JSON.stringify({ brand }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;

  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.removeAttribute("style");
  });

  it("gives the host brand its portal fields, and its accent to <html>", async () => {
    global.fetch = reply({
      brand_name: "WL",
      accent_color: "#336699",
      support_email: "",
      support_url: "",
      logo_url: null,
      portal: { short_name: "W", support_name: "", support_phone: "", website_url: "", welcome_message: "Hello", footer_text: "", login_show_welcome: false, login_show_support: true },
    });
    render(<HostProbe host="app.wl.example" />);
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("WL"));
    expect(screen.getByTestId("short")).toHaveTextContent("W");
    expect(screen.getByTestId("welcome")).toHaveTextContent("Hello|false|true");
    expect(document.documentElement.style.getPropertyValue("--w360-brand")).toBe("#336699");
  });

  it("reads a host brand with no portal as no words set and both sign-in choices on", async () => {
    global.fetch = reply({ brand_name: "Old", accent_color: "", support_email: "", support_url: "", logo_url: null });
    render(<HostProbe host="app.wl.example" />);
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("Old"));
    expect(screen.getByTestId("welcome")).toHaveTextContent("|true|true");
  });

  it("gives the organization's own brand a complete portal, whatever the server sent", async () => {
    global.fetch = reply({
      brand_name: "ABC",
      accent_color: "",
      support_email: "",
      support_url: "",
      logo_url: null,
      portal: { short_name: "A", welcome_message: 7, login_show_welcome: "yes" },
    });
    render(<OrgPortalProbe />);
    await waitFor(() => expect(screen.getByTestId("portal")).not.toHaveTextContent("none"));
    expect(JSON.parse(screen.getByTestId("portal").textContent ?? "")).toEqual({
      short_name: "A",
      support_name: "",
      support_phone: "",
      website_url: "",
      welcome_message: "",
      footer_text: "",
      login_show_welcome: true,
      login_show_support: true,
    });
  });
});
