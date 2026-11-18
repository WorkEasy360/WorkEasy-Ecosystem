// Partner Panel UI audit (2026-11-11, the owner's "Professional Partner Panel UI/UX
// redesign" brief): signs in as each kind of partner on the scratch database of
// scripts/partner_admin_panel_check, walks every page the partner's own menus,
// section tabs and page tabs lead to, and measures what the brief asks to be
// consistent - one h1 and where it sits, control heights, the type scale, sideways
// overflow, things pushed off screen, axe, console errors - at several widths, with
// a screenshot of each. Run once before a change and once after, with LABEL
// before / after, and compare the two report.json files (compare.js).
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   EDGE=127.0.0.1:8088 OUT=<dir> LABEL=before node scripts/partner_ui_check/audit.js

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const LABEL = process.env.LABEL || "run";
const OUT = path.join(process.env.OUT || ".", LABEL);
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const HOSTS = ["admin", "partner", "app", "auth", "api"].map((p) => `${p}.workeasy360.com`);
const PARTNER = "http://partner.workeasy360.com";
const PERSONAS = [
  ["reseller", "owner@pap-reseller.dev"],
  ["affiliate", "owner@pap-affiliate.dev"],
  ["white-label", "owner@pap-brand.dev"],
  ["draft", "owner@pap-draft.dev"],
];
// Every page at these; the main pages (MAJOR) at every width the brief names.
const WIDTHS = [1440, 1024, 390];
const ALL_WIDTHS = [1920, 1440, 1366, 1024, 768, 390, 360, 320];
const MAJOR = /\/(dashboard|users|users\/subscriptions|platform-wallet|payments\/commissions|help|business-kyc|eligible-products|products)$/;
const MAX_PAGES = 90;

fs.mkdirSync(OUT, { recursive: true });
const report = { label: LABEL, pages: [], consoleErrors: [], failures: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const settle = (page) => page.waitForNetworkIdle({ idleTime: 600, timeout: 25000 }).catch(() => {});
const slug = (p) => p.replace(/^\/partner\/[^/]+\//, "").replace(/[/?=&]+/g, "_") || "home";

async function measure(page) {
  return page.evaluate(() => {
    const main = document.querySelector("main") || document.body;
    const vis = (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
    };
    const h1 = [...document.querySelectorAll("h1")].filter(vis);
    const h1r = h1[0] ? h1[0].getBoundingClientRect() : null;
    const controls = [...main.querySelectorAll("button, a.w360-btn, a[class*='btn'], a[class*='Btn'], a[class*='button']")].filter(vis);
    const heights = (els) => [...new Set(els.map((e) => Math.round(e.getBoundingClientRect().height)))].sort((a, b) => a - b);
    const inputs = [...main.querySelectorAll("input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select")].filter(vis);
    const textEls = [...main.querySelectorAll("h1,h2,h3,h4,p,li,td,th,label,dt,dd,span,strong,a,button")].filter(
      (e) => vis(e) && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()),
    );
    const sizes = [...new Set(textEls.map((e) => getComputedStyle(e).fontSize))].sort((a, b) => parseFloat(a) - parseFloat(b));
    const vw = window.innerWidth;
    const offscreen = [...main.querySelectorAll("*")]
      .filter((e) => {
        if (!vis(e)) return false;
        const r = e.getBoundingClientRect();
        if (r.right <= vw + 1) return false;
        // Inside a scroll region (tables, chips) is allowed.
        for (let p = e.parentElement; p && p !== main; p = p.parentElement) {
          const ox = getComputedStyle(p).overflowX;
          if (ox === "auto" || ox === "scroll" || ox === "hidden") return false;
        }
        return true;
      })
      .slice(0, 5)
      .map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 40)}`);
    const clipped = textEls
      .filter((e) => {
        const s = getComputedStyle(e);
        return e.scrollWidth > e.clientWidth + 1 && s.overflow === "hidden" && s.textOverflow !== "ellipsis";
      })
      .slice(0, 5)
      .map((e) => e.textContent.trim().slice(0, 40));
    const small = controls.filter((c) => {
      const r = c.getBoundingClientRect();
      return r.height < 24 || r.width < 24;
    }).length;
    return {
      h1: h1.map((e) => e.textContent.trim()),
      h1Top: h1r ? Math.round(h1r.top + window.scrollY) : null,
      h1Left: h1r ? Math.round(h1r.left) : null,
      h1Size: h1[0] ? getComputedStyle(h1[0]).fontSize : null,
      h2Sizes: [...new Set([...main.querySelectorAll("h2")].filter(vis).map((e) => getComputedStyle(e).fontSize))],
      buttonHeights: heights(controls),
      inputHeights: heights(inputs),
      fontSizes: sizes,
      overflowX: document.documentElement.scrollWidth > vw + 1,
      offscreen,
      clipped,
      smallTargets: small,
    };
  });
}

async function axe(page) {
  try {
    await page.addScriptTag({ content: AXE });
    return await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] });
      return r.violations.map((v) => `${v.id} (${v.nodes.length})`);
    });
  } catch (err) {
    return [`axe failed: ${err.message}`];
  }
}

async function links(page, base) {
  return page.evaluate((b) => {
    const pick = (sel) => [...document.querySelectorAll(sel)].map((a) => new URL(a.href, location.href).pathname);
    return [
      ...pick('nav[aria-label$="workspace"] a'),
      ...pick('nav[aria-label="Sections"] a'),
      ...pick('nav[aria-label="Pages"] a'),
    ].filter((p) => p.startsWith(b));
  }, base);
}

async function signIn(page, email) {
  await page.goto(`${PARTNER}/login`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 60000 });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login") && /\/partner\/[^/]+\/./.test(location.pathname), { timeout: 90000 });
  await settle(page);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
    args: [
      `--host-resolver-rules=${HOSTS.map((h) => `MAP ${h} ${EDGE}`).join(",")}`,
      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
      `--unsafely-treat-insecure-origin-as-secure=${HOSTS.map((h) => `http://${h}`).join(",")}`,
      "--no-first-run",
    ],
    defaultViewport: { width: 1440, height: 900 },
  });
  try {
    for (const [persona, email] of PERSONAS) {
      const ctx = await browser.createBrowserContext();
      const page = await ctx.newPage();
      page.on("console", (m) => {
        if (m.type() === "error" && !/status of (401|403|404|409)/.test(m.text())) report.consoleErrors.push(`${persona} ${page.url()} ${m.text().slice(0, 200)}`);
      });
      try {
        await signIn(page, email);
      } catch (err) {
        report.failures.push(`${persona}: sign-in ${err.message}`);
        await ctx.close();
        continue;
      }
      const base = new URL(page.url()).pathname.split("/").slice(0, 3).join("/");
      fs.mkdirSync(path.join(OUT, persona), { recursive: true });
      const queue = [new URL(page.url()).pathname];
      const seen = new Set();
      while (queue.length && seen.size < MAX_PAGES) {
        const p = queue.shift();
        if (seen.has(p)) continue;
        seen.add(p);
        await page.setViewport({ width: 1440, height: 900 });
        try {
          await page.goto(`${PARTNER}${p}`, { waitUntil: "domcontentloaded" });
          await page.waitForSelector("h1", { timeout: 30000 }).catch(() => {});
          await settle(page);
          await sleep(300);
        } catch (err) {
          report.failures.push(`${persona} ${p}: ${err.message}`);
          continue;
        }
        const finalPath = new URL(page.url()).pathname;
        for (const l of await links(page, base)) if (!seen.has(l) && !queue.includes(l)) queue.push(l);
        const entry = { persona, path: p, finalPath, widths: {} };
        entry.axe = await axe(page);
        const widths = MAJOR.test(finalPath) ? ALL_WIDTHS : WIDTHS;
        for (const w of widths) {
          await page.setViewport({ width: w, height: w <= 390 ? 844 : 900 });
          await sleep(350);
          entry.widths[w] = await measure(page);
          await page.screenshot({ path: path.join(OUT, persona, `${slug(finalPath)}-${w}.png`), fullPage: true }).catch(() => {});
        }
        report.pages.push(entry);
        console.log(`${persona} ${finalPath} h1=${JSON.stringify(entry.widths[1440].h1)} axe=${entry.axe.length}`);
      }
      await ctx.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    console.log(`\n${report.pages.length} pages, ${report.failures.length} failures, ${report.consoleErrors.length} console errors -> ${OUT}`);
  }
})();
