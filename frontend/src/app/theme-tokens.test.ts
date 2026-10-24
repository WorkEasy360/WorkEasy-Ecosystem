import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The WorkEasy360 palette (globals.css) checked where it is defined: every
 * text/fill pairing the consoles draw must pass WCAG AA in BOTH themes, so a
 * later tweak to one token cannot quietly break a screen nobody re-tested.
 */

const css = readFileSync(path.resolve(__dirname, "globals.css"), "utf8");

/** The custom properties declared directly inside the first block that `selector` opens. */
function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`no ${selector} block`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const vars: Record<string, string> = {};
  for (const m of css.slice(open + 1, close).matchAll(/(--w360-[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    vars[m[1]] = m[2].trim();
  }
  return vars;
}

const light = block(":root {");
// The explicit dark theme repeats the media-query values (asserted below).
const dark = { ...light, ...block(':root[data-theme="dark"] {') };

function resolve(theme: Record<string, string>, name: string): string {
  let value = theme[name];
  for (let i = 0; i < 5 && value?.startsWith("var("); i++) value = theme[value.slice(4, -1).trim()];
  if (!value || !/^#[0-9a-f]{6}$/i.test(value)) throw new Error(`${name} is not a hex colour: ${value}`);
  return value;
}

const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = c.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// [foreground, background, minimum]: 4.5 for text, 3 for a control's edge or a state marker.
const PAIRS: [string, string, number][] = [
  ["--w360-text", "--w360-surface", 4.5],
  ["--w360-text", "--w360-bg", 4.5],
  ["--w360-text-secondary", "--w360-surface", 4.5],
  ["--w360-text-muted", "--w360-surface", 4.5],
  ["--w360-text-muted", "--w360-bg", 4.5],
  ["--w360-text-muted", "--w360-bg-soft", 4.5],
  ["--w360-text-muted", "--w360-neutral-bg", 4.5], // neutral badge, disabled button
  ["--w360-text-muted", "--w360-surface-mint", 4.5],
  ["--w360-brand", "--w360-surface", 4.5], // links
  ["--w360-brand", "--w360-bg", 4.5],
  ["--w360-brand", "--w360-bg-soft", 4.5],
  ["--w360-brand", "--w360-brand-soft", 4.5], // the current page, selected tab and range
  ["--w360-brand", "--w360-surface-mint", 4.5],
  ["--w360-brand-contrast", "--w360-brand", 4.5],
  ["--w360-cta-contrast", "--w360-cta", 4.5], // primary button
  ["--w360-cta-contrast", "--w360-cta-hover", 4.5],
  ["--w360-danger-contrast", "--w360-danger", 4.5], // destructive button
  ["--w360-danger-contrast", "--w360-danger-hover", 4.5],
  ["--w360-success", "--w360-success-bg", 4.5], // badges
  ["--w360-warning", "--w360-warning-bg", 4.5],
  ["--w360-danger", "--w360-danger-bg", 4.5],
  ["--w360-info", "--w360-info-bg", 4.5],
  ["--w360-danger", "--w360-surface", 4.5], // error text
  ["--w360-input-border", "--w360-surface", 3], // WCAG 1.4.11
  ["--w360-focus-ring", "--w360-surface", 3],
  ["--w360-focus-ring", "--w360-bg-soft", 3],
  ["--w360-indicator", "--w360-surface", 3],
  ["--w360-indicator", "--w360-brand-soft", 3],
  ["--w360-chart-1-edge", "--w360-surface", 3],
  ["--w360-chart-2", "--w360-surface", 3],
  // The website's section colours (the public site's bands and footer).
  ["--w360-band-text", "--w360-band", 4.5], // also the focus ring on the band
  ["--w360-band-muted", "--w360-band", 4.5],
  ["--w360-sun-contrast", "--w360-sun", 4.5], // yellow button, step numbers
  ["--w360-sun-contrast", "--w360-sun-hover", 4.5],
  ["--w360-sun", "--w360-band", 3], // the yellow button's edge on the band
  ["--w360-text", "--w360-cream", 4.5],
  ["--w360-text-muted", "--w360-cream", 4.5],
  ["--w360-brand", "--w360-cream", 4.5],
];

describe("WorkEasy360 colour tokens", () => {
  it("keep the website's brand values", () => {
    expect(resolve(light, "--w360-cta")).toBe("#02c065");
    expect(resolve(light, "--w360-cta-hover")).toBe("#04a05b");
    expect(resolve(light, "--w360-focus-ring")).toBe("#04884c");
    expect(resolve(light, "--w360-text")).toBe("#161d24");
    expect(resolve(light, "--w360-text-secondary")).toBe("#2d2e2f");
    expect(resolve(light, "--w360-bg")).toBe("#eaf6ef");
    expect(resolve(light, "--w360-bg-soft")).toBe("#f4faf7");
    expect(resolve(light, "--w360-surface-mint")).toBe("#dbefe6");
    expect(resolve(light, "--w360-border")).toBe("#d8e5de");
    expect(resolve(dark, "--w360-cta")).toBe("#02c065");
    expect(resolve(light, "--w360-sun")).toBe("#fcc63c");
    expect(resolve(light, "--w360-cream")).toBe("#fff8e8");
  });

  it("puts the website's typeface first", () => {
    expect(block(":root {")["--w360-font"]).toMatch(/^var\(--w360-font-brand, "Plus Jakarta Sans"\)/);
    const layout = readFileSync(path.resolve(__dirname, "layout.tsx"), "utf8");
    expect(layout).toMatch(/Plus_Jakarta_Sans\(/);
    expect(layout).toMatch(/variable: "--w360-font-brand"/);
  });

  it("is always light, like the website - never the device's dark mode", () => {
    const layout = readFileSync(path.resolve(__dirname, "layout.tsx"), "utf8");
    expect(layout).toMatch(/<html [^>]*data-theme="light"/);
  });

  for (const [themeName, theme] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    it.each(PAIRS)(`${themeName}: %s on %s reaches %s:1`, (fg, bg, min) => {
      expect(ratio(resolve(theme, fg), resolve(theme, bg))).toBeGreaterThanOrEqual(min);
    });
  }

  it("declares the same dark values for the system preference and for an explicit dark theme", () => {
    const media = css.indexOf("@media (prefers-color-scheme: dark)");
    const inner = css.indexOf(":root:not", media);
    const viaMedia = block(css.slice(inner, css.indexOf("{", inner) + 1));
    expect(viaMedia).toEqual(block(':root[data-theme="dark"] {'));
  });
});

describe("partner panel colours", () => {
  const src = path.resolve(__dirname, "..");
  // The six preset dots are partner data (Account.tsx PRESETS), not panel colours;
  // the public site's six monogram accents tell products apart, likewise.
  const strip = (text: string) =>
    text.replace(/\.dot[A-Z][a-z]+\s*\{[^}]*\}/g, "").replace(/\.accent\d\s*\{[^}]*\}/g, "");

  it.each([
    "features/partnerpanel/PartnerPanel.module.css",
    "components/sidebar/SidebarShell.module.css",
    "components/brand/Logo.module.css",
    "features/controlplane/ControlPlane.module.css",
    "features/auth/LoginScreen.module.css",
    "features/site/Site.module.css",
  ])("%s uses the tokens, never a hard-coded colour", (file) => {
    const text = strip(readFileSync(path.join(src, file), "utf8")).replace(/\/\*[\s\S]*?\*\//g, "");
    expect(text.match(/#[0-9a-f]{3,8}\b|rgba?\(/gi) ?? []).toEqual([]);
  });
});
