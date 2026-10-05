// Real-browser verification of the Phase 7 screens against the production build:
// CSP violations, console errors, axe (WCAG 2.1 A/AA incl. colour contrast),
// keyboard focus visibility, and reflow at 320px. Not part of the repository.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(
  "C:/Users/aqw03/OneDrive/Desktop/Workeasy360/frontend/node_modules/axe-core/axe.min.js",
  "utf8",
);
const PASSWORD = "Browser-Test-Password-1!";

const report = { pages: [], csp: [], errors: [] };

async function newPage(browser, width = 1280) {
  const page = await browser.newPage();  // browser may be a BrowserContext
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
  if (!session.authenticated) throw new Error(`login failed for ${email}: ${JSON.stringify(session)}`);
  return page;
}

async function check(page, url, label) {
  await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
  await page.waitForSelector("main", { timeout: 15000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 400));
  const csp = await page.evaluate(() => window.__csp);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));

  const axe = await page.evaluate(async (src) => {
    // Runtime.evaluate is not subject to the page CSP, so the library can be injected.
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 140) }));
  }, AXE);

  // Keyboard: tab through the first focusable elements and check each shows a focus indicator.
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
      const border = el.matches("input,select,textarea");
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || el.name || "").slice(0, 40), visible: outline || shadow || border };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }

  const reflowWidth = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  report.pages.push({ label, url, axe, tabbed, noFocus, reflowDesktop: reflowWidth });
}

async function reflow(browser, cookiesFrom, urls) {
  const page = await newPage(browser, 320);
  await page.setCookie(...(await cookiesFrom.cookies()));
  const out = [];
  for (const url of urls) {
    await page.goto(`${BASE}${url}`, { waitUntil: "networkidle0", timeout: 30000 });
    await new Promise((r) => setTimeout(r, 300));
    const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    out.push({ url, ...w, ok: w.scroll <= w.inner + 1 });
  }
  await page.close();
  return out;
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    const staff = await login(browser, "staff@browser.test");
    const partnersPage = await staff.goto(`${BASE}/admin/partners`, { waitUntil: "networkidle0" });
    const links = await staff.$$eval("a[href^='/admin/partners/']", (as) => as.map((a) => a.getAttribute("href")));
    const partnerHref = links[0];
    await staff.goto(`${BASE}/admin/customers`, { waitUntil: "networkidle0" });
    const custLinks = await staff.$$eval("a[href^='/admin/customers/']", (as) => as.map((a) => a.getAttribute("href")));
    const customerHref = custLinks[0];
    const adminUrls = ["/admin", "/admin/customers", customerHref, "/admin/products", "/admin/partners", partnerHref, "/admin/commissions", "/admin/payouts", "/admin/diagnostics", "/admin/audit", "/admin/security"].filter(Boolean);
    for (const u of adminUrls) await check(staff, u, `admin ${u}`);
    const staffReflow = await reflow(browser, staff, adminUrls);

    const partnerUser = await login(browser, "partner@browser.test");
    await partnerUser.goto(`${BASE}/partner`, { waitUntil: "networkidle0" });
    const pLinks = await partnerUser.$$eval("a[href^='/partner/']", (as) => as.map((a) => a.getAttribute("href")));
    const base = pLinks.find((h) => /^\/partner\/[0-9a-f-]{36}$/.test(h));
    const partnerUrls = ["/partner", base, ...["customers", "products", "referrals", "commissions", "payouts", "branding", "domains"].map((s) => `${base}/${s}`)].filter(Boolean);
    for (const u of partnerUrls) await check(partnerUser, u, `partner ${u.replace(/[0-9a-f-]{36}/, "<id>")}`);
    const partnerReflow = await reflow(browser, partnerUser, partnerUrls);

    report.reflow = { staff: staffReflow, partner: partnerReflow };
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(__dirname, "browser_report.json"), JSON.stringify(report, null, 2));
  const violations = report.pages.flatMap((p) => p.axe.map((v) => `${p.label}: ${v.id} (${v.impact}) x${v.nodes} ${v.sample || ""}`));
  console.log("PAGES", report.pages.length);
  console.log("CSP violations:", report.csp.length, report.csp.slice(0, 5));
  console.log("Console errors:", report.errors.length, report.errors.slice(0, 8));
  console.log("Axe violations:", violations.length);
  violations.forEach((v) => console.log("  ", v));
  const nf = report.pages.filter((p) => p.noFocus.length).map((p) => `${p.label}: ${[...new Set(p.noFocus)].join(", ")}`);
  console.log("Elements without a visible focus indicator:", nf.length);
  nf.forEach((n) => console.log("  ", n));
  const bad = [...report.reflow.staff, ...report.reflow.partner].filter((r) => !r.ok);
  console.log("Pages with horizontal scroll at 320px:", bad.length);
  bad.forEach((b) => console.log("  ", JSON.stringify(b)));
})().catch((e) => {
  console.error("FAILED", e);
  process.exit(1);
});
