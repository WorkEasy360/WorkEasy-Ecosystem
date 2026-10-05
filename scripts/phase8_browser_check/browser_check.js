// Real-browser verification of the Phase 8 screens against the production
// build: CSP violations, console errors, axe (WCAG 2.1 A/AA incl. colour
// contrast), keyboard focus visibility and reflow at 320px - the Phase 7
// harness's checks (../phase7_browser_check/browser_check.js). Needs
// ORG_SLUG and ORG_ID from seed.py. Not part of the repository's CI.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const { ORG_SLUG, ORG_ID } = process.env;

const report = { pages: [], csp: [], errors: [], reflow: [] };

async function newPage(browser, width = 1280) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile || ""}`),
    );
  });
  page.on("console", (m) => {
    if (m.type() === "error") report.errors.push(`${page.url()} :: ${m.text()}`);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function login(browser, email) {
  const context = await browser.createBrowserContext();
  const page = await newPage(context);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle0" });
  await page.type('input[name="email"], input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await Promise.all([
    page.click('button[type="submit"]'),
    page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {}),
  ]);
  const session = await page.evaluate(() => fetch("/api/v1/auth/session").then((r) => r.json()));
  if (!session.authenticated) throw new Error(`login failed for ${email}`);
  return page;
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src); // Runtime.evaluate is not subject to the page CSP
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 140) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  let tabbed = 0;
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow && cs.boxShadow !== "none";
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || el.name || "").slice(0, 40), visible: outline || shadow || el.matches("input,select,textarea") };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }
  const text = await page.evaluate(() => document.querySelector("main")?.innerText.slice(0, 160) || "");
  report.pages.push({ label, axe, tabbed, noFocus, text });
}

async function visit(page, url, label) {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 400));
  await audit(page, label);
}

async function reflow(browser, cookiesFrom, urls) {
  const page = await newPage(browser, 320);
  await page.setCookie(...(await cookiesFrom.cookies()));
  for (const url of urls) {
    await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
    await new Promise((r) => setTimeout(r, 300));
    const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ url, ...w, ok: w.scroll <= w.inner + 1 });
  }
  await page.close();
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    const owner = await login(browser, "owner@browser.test");
    // The Phase 8 screens, then the other organization pages: Phase 8 changed
    // the shell they share (main's width at 320px).
    const orgUrls = ["settings/ai", "settings/analytics", "apps", "settings/organization", "settings/members",
      "settings/products", "settings/security", "settings/integrations", "settings/webhooks", "settings/automations"]
      .map((s) => `/o/${ORG_SLUG}/${s}`);
    for (const u of orgUrls) await visit(owner, u, `org ${u.replace(ORG_SLUG, "<slug>")}`);
    await reflow(browser, owner, orgUrls);

    const staff = await login(browser, "staff@browser.test");
    await visit(staff, "/admin/ai", "admin /admin/ai");
    // The organization panel: look the seeded organization up, then audit again.
    await staff.evaluate(() => {
      const label = [...document.querySelectorAll("label")].find((l) => l.textContent.trim() === "Organization id");
      document.getElementById(label.htmlFor).focus();
    });
    await staff.keyboard.type(ORG_ID);
    await staff.click("::-p-text(Show AI policy and usage)");
    await staff.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
    const panel = await staff.evaluate(() =>
      [...document.querySelectorAll("caption")].map((c) => c.innerText).filter((t) => /spend by scope|Latest AI requests/.test(t)),
    );
    if (panel.length !== 2) throw new Error(`organization panel did not render: ${JSON.stringify(panel)}`);
    await audit(staff, "admin /admin/ai (organization panel)");
    await reflow(browser, staff, ["/admin/ai"]);
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(__dirname, "browser_report.json"), JSON.stringify(report, null, 2));
  const violations = report.pages.flatMap((p) => p.axe.map((v) => `${p.label}: ${v.id} (${v.impact}) x${v.nodes} ${v.sample || ""}`));
  console.log("PAGES", report.pages.map((p) => `${p.label} [tab stops ${p.tabbed}] :: ${p.text.replace(/\s+/g, " ").slice(0, 90)}`));
  console.log("CSP violations:", report.csp.length, report.csp.slice(0, 5));
  console.log("Console errors:", report.errors.length, report.errors.slice(0, 8));
  console.log("Axe violations:", violations.length);
  violations.forEach((v) => console.log("  ", v));
  const nf = report.pages.filter((p) => p.noFocus.length).map((p) => `${p.label}: ${[...new Set(p.noFocus)].join(", ")}`);
  console.log("Elements without a visible focus indicator:", nf.length);
  nf.forEach((n) => console.log("  ", n));
  const bad = report.reflow.filter((r) => !r.ok);
  console.log("Pages with horizontal scroll at 320px:", bad.length, bad);
})().catch((e) => {
  console.error("FAILED", e);
  process.exit(1);
});
