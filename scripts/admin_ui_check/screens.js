// Before/after screenshots and layout measurements of every Super Admin page
// (2026-11-07 design system, docs/admin-ui/status.md). Runs against a
// production build of the frontend and a real backend on the scratch database
// seeded by scripts/ecosystem_browser_check/prepare.py --skip-rehearsal
// (Super Admin staff@eco.test). Read-only: it opens pages, never submits a form.
//
// Per page and width (1920, 1440, 1280, 1024, 768): a screenshot, page-level
// horizontal overflow, the h1 count and its distance from the top, the
// browser title, console errors; at 1440 also axe WCAG 2.1 A/AA.
//
//   CHECK_PASSWORD=... NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> \
//   LABEL=after OUT=<dir> BASE=http://127.0.0.1:3999 node scripts/admin_ui_check/screens.js
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PASSWORD = process.env.CHECK_PASSWORD;
const LABEL = process.env.LABEL || "after";
const OUT = process.env.OUT || "admin_ui_shots";
const AXE = process.env.AXE_PATH ? fs.readFileSync(process.env.AXE_PATH, "utf8") : null;
const EMAIL = process.env.CHECK_EMAIL || "staff@eco.test";
const WIDTHS = (process.env.WIDTHS || "1920,1440,1280,1024,768").split(",").map(Number);
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const DIR = path.join(OUT, LABEL);
fs.mkdirSync(DIR, { recursive: true });

const PAGES = [
  "/admin",
  "/admin/users",
  "USER_DETAIL",
  "/admin/subscriptions",
  "/admin/entitlements",
  "/admin/partners",
  "/admin/partners/all",
  "PARTNER_DETAIL",
  "/admin/partners/product-requests",
  "/admin/partners/tickets",
  "/admin/partners/withdrawals",
  "/admin/partners/upgrade-fees",
  "/admin/products",
  "PRODUCT_DETAIL",
  "/admin/store",
  "/admin/plans",
  "/admin/pricing",
  "/admin/orders",
  "/admin/payments",
  "/admin/communication",
  "/admin/ai",
  "/admin/security",
  "/admin/audit",
  "/admin/diagnostics",
  "/admin/settings",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const slug = (p) => (p === "/admin" ? "dashboard" : p.replace(/^\/admin\//, "").replace(/[^a-z0-9]+/gi, "-"));

async function settle(page) {
  await page.waitForNetworkIdle({ idleTime: 600, timeout: 30000 }).catch(() => {});
  await sleep(500);
}

async function firstLink(page, from, pattern) {
  await page.goto(`${BASE}${from}`, { waitUntil: "networkidle0" });
  await settle(page);
  return page.evaluate((src) => {
    const re = new RegExp(src);
    const a = [...document.querySelectorAll("main a[href]")].find((x) => re.test(x.getAttribute("href")));
    return a ? a.getAttribute("href") : null;
  }, pattern.source);
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--force-prefers-reduced-motion"] });
  const page = await browser.newPage();
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await page.setViewport({ width: 1440, height: 900 });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`${page.url()} :: ${m.text()}`);
  });

  await page.goto(`${BASE}/login?mode=admin`, { waitUntil: "networkidle0" });
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', EMAIL);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
  await settle(page);

  const detail = {
    USER_DETAIL: await firstLink(page, "/admin/users", /^\/admin\/users\/[0-9a-f-]{36}$/),
    PARTNER_DETAIL: await firstLink(page, "/admin/partners/all", /^\/admin\/partners\/[0-9a-f-]{36}$/),
    PRODUCT_DETAIL: await firstLink(page, "/admin/products", /^\/admin\/products\/[0-9a-f-]{36}$/),
  };

  const report = { label: LABEL, base: BASE, detail, pages: [], errors };
  for (const entry of PAGES) {
    const target = detail[entry] ?? entry;
    if (!target) {
      report.pages.push({ page: entry, skipped: "no record to open" });
      continue;
    }
    const name = entry.includes("_DETAIL") ? entry.toLowerCase().replace("_", "-") : slug(entry);
    for (const width of WIDTHS) {
      await page.setViewport({ width, height: 900 });
      const before = errors.length;
      await page.goto(`${BASE}${target}`, { waitUntil: "networkidle0" }).catch(() => {});
      await settle(page);
      const m = await page.evaluate(() => {
        const h1s = [...document.querySelectorAll("main h1")];
        const h1 = h1s[0];
        const top = h1 ? Math.round(h1.getBoundingClientRect().top + window.scrollY) : null;
        const sub = h1 ? h1.closest("header")?.querySelector("p") : null;
        const gap = h1 && sub ? Math.round(sub.getBoundingClientRect().top - h1.getBoundingClientRect().bottom) : null;
        const buttons = [...document.querySelectorAll("main button, main a[class*='btn']")].filter((b) => b.offsetParent);
        const tall = buttons.filter((b) => b.getBoundingClientRect().height > 40).map((b) => `${(b.textContent || "").trim().slice(0, 30)} ${Math.round(b.getBoundingClientRect().height)}px`);
        const fontSizes = [...new Set([...document.querySelectorAll("main *")].filter((e) => e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())).map((e) => getComputedStyle(e).fontSize))].sort();
        const upper = [...document.querySelectorAll("main th")].filter((th) => getComputedStyle(th).textTransform === "uppercase").length;
        return {
          title: document.title,
          h1: h1s.map((h) => h.textContent.trim()),
          titleTop: top,
          titleToSubtitle: gap,
          overflowX: document.documentElement.scrollWidth - window.innerWidth,
          tallButtons: tall.slice(0, 8),
          fontSizes,
          uppercaseHeaders: upper,
        };
      });
      const file = `${name}-${width}.png`;
      await page.screenshot({ path: path.join(DIR, file), fullPage: width === 1440 });
      let axe = null;
      if (AXE && width === 1440) {
        await page.addScriptTag({ content: AXE });
        axe = await page.evaluate(async () => {
          const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
          return r.violations.map((v) => `${v.id} (${v.nodes.length})`);
        });
      }
      report.pages.push({ page: entry, path: target, width, file, ...m, axe, consoleErrors: errors.slice(before) });
    }
  }
  fs.writeFileSync(path.join(DIR, "report.json"), JSON.stringify(report, null, 2));
  await browser.close();
  const problems = report.pages.filter((p) => p.overflowX > 0 || (p.axe && p.axe.length) || (p.h1 && p.h1.length !== 1));
  console.log(`${LABEL}: ${report.pages.length} shots, ${problems.length} with overflow/axe/h1 findings -> ${DIR}`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
