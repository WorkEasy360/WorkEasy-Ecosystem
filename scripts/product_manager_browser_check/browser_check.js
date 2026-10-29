// Real-browser verification of the Product Manager (ADR-051), against the production
// build and a real backend on the scratch database prepared by prepare.py. Same
// harness rules as the earlier checks:
//   - CSP violations and console errors on every audited page;
//   - axe WCAG 2.1 A/AA, a visible keyboard focus indicator, exactly one h1;
//   - reflow (no horizontal page scroll) at 1440, 1366, 1024, 768 and 390 px;
// and the brief's journey, driven through the UI as the Super Admin:
//   L. the list shows the four live products (Active, Published), searches and filters
//   W. Add Product: "Workeasy Test App" / workeasy-test-app - logo, description,
//      screenshot, store preview, launch URL, SSO, trial 7 days - Save Draft
//   D. the draft is Draft + Hidden, absent from the public catalog; every tab renders
//   P. Publish: Active + Published, present in the public catalog; no entitlement
//   U. Unpublish: hidden again
//   X. the test product has an integration, so Delete is refused (Archive instead);
//      a product nothing uses is deleted from its page
// Screenshots for the owner's manual review are written next to OUT.
// Not part of CI. A manual screen-reader pass is NOT part of it: NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = process.env.CHECK_PASSWORD;
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const OUT = process.env.OUT || "product_manager_report.json";
const SHOTS = path.join(path.dirname(OUT), "screens");
fs.mkdirSync(SHOTS, { recursive: true });
const WIDTHS = [1440, 1366, 1024, 768, 390];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], notes: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ images --

function png(width, height, rgb) {
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(body) >>> 0 : crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 2, 0, 0, 0], 8);
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set(rgb(x, width), 1 + x * 3);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
function crc32(buf) {
  let c = ~0;
  for (const b of buf) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return ~c >>> 0;
}
const LOGO = path.join(SHOTS, "test-logo.png");
const SCREEN = path.join(SHOTS, "test-screenshot.png");
fs.writeFileSync(LOGO, png(256, 256, (x, w) => (x < w / 2 ? [16, 128, 80] : [12, 40, 90])));
fs.writeFileSync(SCREEN, png(1280, 720, (x, w) => [Math.round((255 * x) / w), 200, 160]));

// ------------------------------------------------------------------ plumbing --

async function newPage(browser, width = 1440) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    if (m.text().startsWith("Failed to load resource") && /status of (400|401|403|404|409)/.test(m.text())) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function audit(page, label) {
  await sleep(450);
  (await page.evaluate(() => window.__csp || [])).forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
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
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 40), visible: ring || el.matches("input,select,textarea") };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url().replace(BASE, ""), axe, tabbed, noFocus, h1 });
}

async function reflow(page, label) {
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: 900 });
    await sleep(350);
    const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ label, width, ok: r.scroll <= r.inner, scroll: r.scroll });
    if (width === 390 || width === 1440) await page.screenshot({ path: path.join(SHOTS, `${label.replace(/\W+/g, "-")}-${width}.png`), fullPage: true });
  }
  await page.setViewport({ width: 1440, height: 900 });
  await sleep(300);
}

const text = (page) => page.evaluate(() => (document.querySelector("main") || document.body).innerText);

async function waitForText(page, wanted, timeout = 20000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, wanted).catch(async () => {
    throw new Error(`text not found: "${wanted}" on ${page.url()} :: ${(await text(page)).slice(0, 400).replace(/\n+/g, " | ")}`);
  });
}

async function click(page, label, { exact = true, within = "body", timeout = 10000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const ok = await page.evaluate(
      (label, exact, within) => {
        const root = document.querySelector(within) || document.body;
        const el = [...root.querySelectorAll("button, a, [role=tab], summary")].find((c) => {
          if (c.disabled) return false;
          const r = c.getBoundingClientRect();
          if (!r.width || !r.height) return false;
          const t = (c.innerText || "").replace(/\s+/g, " ").trim();
          const a = (c.getAttribute("aria-label") || "").trim();
          return exact ? t === label || a === label : t.includes(label) || a.includes(label);
        });
        if (!el) return false;
        el.scrollIntoView({ block: "center" });
        el.click();
        return true;
      },
      label, exact, within,
    );
    if (ok) return;
    await sleep(200);
  }
  throw new Error(`nothing to click for "${label}" on ${page.url()}`);
}

/** The control a visible label names (its `for`), as a CSS selector. */
async function field(page, label) {
  const id = await page.evaluate((label) => {
    const l = [...document.querySelectorAll("label")].find((x) => x.innerText.replace(/\*/g, "").trim() === label);
    return l ? l.htmlFor : null;
  }, label);
  if (!id) throw new Error(`no field labelled "${label}" on ${page.url()}`);
  return `[id="${id}"]`;
}

async function type(page, label, value) {
  const sel = await field(page, label);
  await page.click(sel, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(sel, value);
}

/** Pick an option of a radio group named by its legend. */
async function choose(page, legend, option) {
  const ok = await page.evaluate(
    (legend, option) => {
      const set = [...document.querySelectorAll("fieldset")].find((f) => f.querySelector("legend")?.innerText.trim() === legend);
      const label = set && [...set.querySelectorAll("label")].find((l) => l.innerText.trim() === option);
      if (!label) return false;
      label.querySelector("input").click();
      return true;
    },
    legend, option,
  );
  if (!ok) throw new Error(`no option "${option}" in "${legend}"`);
}

const api = (page, method, url) =>
  page.evaluate(
    async (method, url) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(url, { method, credentials: "same-origin", headers: { "X-CSRFToken": csrf ? decodeURIComponent(csrf) : "", Accept: "application/json" } });
      let body = null;
      try {
        body = await r.json();
      } catch {}
      return { status: r.status, body };
    },
    method, url,
  );

async function publicSlugs(page) {
  const r = await page.evaluate(async () => (await fetch("/api/v1/public/catalog", { credentials: "omit" })).json());
  return r.products.map((p) => p.slug);
}

async function flow(name, fn) {
  console.log("flow", name);
  try {
    const detail = await fn();
    report.flows.push({ name, ok: true, detail: detail || "" });
  } catch (e) {
    report.flows.push({ name, ok: false, detail: String(e.message || e).slice(0, 600) });
  }
}

const check = (cond, msg) => {
  if (!cond) throw new Error(msg);
};

// ------------------------------------------------------------------ journey --

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    // A throwaway profile: never the person's own Chrome profile.
    userDataDir: fs.mkdtempSync(path.join(require("os").tmpdir(), "w360-pm-check-")),
    args: ["--no-sandbox"],
  });
  const page = await newPage(browser);
  let productId = null;

  await page.goto(`${BASE}/login?mode=admin`, { waitUntil: "networkidle0" });
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', "staff@browser.test");
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => location.pathname.startsWith("/admin"), { timeout: 90000 });

  await flow("L. the list shows the four live products, searches and filters", async () => {
    await page.goto(`${BASE}/admin/products`, { waitUntil: "networkidle0" });
    await waitForText(page, "Easy Help Desk");
    for (const name of ["Easy CRM", "EasyBook", "Easy Project", "Easy Help Desk"]) await waitForText(page, name);
    const rows = await page.evaluate(() => [...document.querySelectorAll("tbody tr")].map((r) => r.innerText.replace(/\s+/g, " ")));
    check(rows.length === 4 && rows.every((r) => r.includes("Active") && r.includes("Published")), `rows: ${rows.join(" || ")}`);
    await audit(page, "Product Manager list");
    await reflow(page, "list");
    await type(page, "Search products", "book");
    const shown = await page.evaluate(() => document.querySelectorAll("tbody tr").length);
    check(shown === 1, `search "book" shows ${shown}`);
    await page.select(await field(page, "Status"), "draft");
    await waitForText(page, "No product matches these filters.");
    return "4 products, search and status filter work";
  });

  await flow("W. Add Product wizard: Workeasy Test App, Save Draft", async () => {
    await page.goto(`${BASE}/admin/products`, { waitUntil: "networkidle0" });
    await click(page, "+ Add Product");
    await page.waitForFunction(() => location.pathname === "/admin/products/new");
    await waitForText(page, "1. Identity");
    await type(page, "Product name", "Workeasy Test App");
    const slug = await page.$eval(await field(page, "Product slug"), (el) => el.value);
    check(slug === "workeasy-test-app", `slug suggested: ${slug}`);
    await page.select(await field(page, "Category"), "Project Management");
    await type(page, "Short description", "A test product for the Product Manager check.");
    const logoInput = await page.$('input[type=file][aria-label="Product logo"]');
    await logoInput.uploadFile(LOGO);
    await waitForText(page, "test-logo.png");
    await audit(page, "Wizard step 1 Identity");
    await reflow(page, "wizard-identity");
    await click(page, "Next");

    await waitForText(page, "2. Store Listing");
    await type(page, "Short store description", "Plan and test everything in one place.");
    const desc = await page.$("textarea");
    await desc.type("**Workeasy Test App** shows how a new product appears in the store.\n\n- Plans\n- Trials");
    const shots = await page.$('input[type=file][aria-label="Add screenshots"]');
    await shots.uploadFile(SCREEN);
    await waitForText(page, "Caption for screenshot 1");
    await type(page, "Caption for screenshot 1 (optional)", "The main board");
    await type(page, "Support URL", "https://support.workeasy360.com/test-app");
    await audit(page, "Wizard step 2 Store Listing");
    await reflow(page, "wizard-listing");
    await click(page, "Preview Store Listing");
    await waitForText(page, "Store listing preview");
    await audit(page, "Store preview (wizard)");
    await page.screenshot({ path: path.join(SHOTS, "store-preview-wizard.png") });
    await page.keyboard.press("Escape");
    await click(page, "Next");

    await waitForText(page, "3. Integration");
    await type(page, "Launch URL", "javascript:alert(1)");
    await click(page, "Next");
    await waitForText(page, "The launch URL must be an https:// address.");
    await type(page, "Application URL", "https://testapp.workeasy360.com");
    await type(page, "Launch URL", "https://testapp.workeasy360.com/");
    await type(page, "Sign-in callback URL", "https://testapp.workeasy360.com/api/auth/callback");
    await audit(page, "Wizard step 3 Integration");
    await reflow(page, "wizard-integration");
    await click(page, "Next");

    await waitForText(page, "4. Availability");
    await choose(page, "Free trial", "Enabled");
    await type(page, "Trial duration (days)", "7");
    await waitForText(page, "Plans/Pricing required before final Store sale activation.");
    await audit(page, "Wizard step 4 Availability");
    await reflow(page, "wizard-availability");
    await click(page, "Save Draft");
    await page.waitForFunction(() => /^\/admin\/products\/[0-9a-f-]{36}$/.test(location.pathname), { timeout: 60000 });
    productId = page.url().match(/products\/([0-9a-f-]{36})/)[1];
    await waitForText(page, "Saved as a draft");
    return `created ${productId}`;
  });

  await flow("D. the draft is Draft + Hidden, not in the store, every tab renders", async () => {
    check(productId, "no product");
    const r = await api(page, "GET", `/api/v1/admin/products/${productId}`);
    const p = r.body.product;
    check(p.status === "draft" && p.listing === "hidden" && p.manager_status === "draft", `status ${p.status}/${p.listing}`);
    check(p.code === "workeasy_test_app" && p.slug === "workeasy-test-app", `identity ${p.code} ${p.slug}`);
    check(p.channels.trial && p.channels.trial.enabled && p.channels.trial.duration_days === 7, "trial 7 days");
    check(p.media.some((m) => m.kind === "logo") && p.media.some((m) => m.kind === "screenshot" && m.caption === "The main board"), "logo + screenshot stored");
    check(p.launch_url === "https://testapp.workeasy360.com/" && p.integration.status === "draft", "integration draft with launch URL");
    check(p.readiness.every((i) => i.ok), `readiness: ${JSON.stringify(p.readiness.filter((i) => !i.ok))}`);
    check(!(await publicSlugs(page)).includes("workeasy-test-app"), "draft visible publicly");
    await waitForText(page, "Draft");
    await audit(page, "Detail Overview (draft)");
    await reflow(page, "detail-overview");
    for (const tab of ["Store Listing", "Integration", "Availability", "Plans", "Pricing", "Activity"]) {
      await click(page, tab, { within: "[role=tablist]" });
      await sleep(700);
      await audit(page, `Detail ${tab}`);
      if (tab === "Store Listing" || tab === "Availability") await reflow(page, `detail-${tab}`);
    }
    await waitForText(page, "Created");
    return "draft hidden; logo, screenshot, integration, trial stored; 7 tabs audited";
  });

  await flow("P. Publish: Active + Published, in the store, no access granted", async () => {
    await page.goto(`${BASE}/admin/products/${productId}`, { waitUntil: "networkidle0" });
    await click(page, "Publish", { within: "main header" });
    await waitForText(page, "is published to the store");
    const p = (await api(page, "GET", `/api/v1/admin/products/${productId}`)).body.product;
    check(p.status === "available" && p.listing === "listed" && p.manager_status === "active", `after publish ${p.status}/${p.listing}`);
    check((await publicSlugs(page)).includes("workeasy-test-app"), "published product not public");
    await click(page, "Preview listing");
    await waitForText(page, "Store listing preview");
    await audit(page, "Store preview (stored product)");
    await page.screenshot({ path: path.join(SHOTS, "store-preview-detail.png") });
    await page.keyboard.press("Escape");
    await click(page, "Plans", { within: "[role=tablist]" });
    await click(page, "Manage Plans →");
    await page.waitForFunction(() => location.pathname === "/admin/plans" && location.search.includes("product=workeasy_test_app"));
    await waitForText(page, "Showing Workeasy Test App only.");
    await audit(page, "Plans filtered to the product");
    await page.goto(`${BASE}/admin/pricing?product=workeasy_test_app`, { waitUntil: "networkidle0" });
    await waitForText(page, "Showing prices for Workeasy Test App only.");
    await audit(page, "Pricing filtered to the product");
    return "active + published; public catalog lists it; plans/pricing open filtered";
  });

  await flow("U. Unpublish: hidden again", async () => {
    await page.goto(`${BASE}/admin/products/${productId}`, { waitUntil: "networkidle0" });
    await click(page, "Unpublish", { within: "main header" });
    await waitForText(page, "is hidden from the store");
    const p = (await api(page, "GET", `/api/v1/admin/products/${productId}`)).body.product;
    check(p.status === "available" && p.listing === "hidden", `after unpublish ${p.status}/${p.listing}`);
    check(!(await publicSlugs(page)).includes("workeasy-test-app"), "still public after unpublish");
    await click(page, "Activity", { within: "[role=tablist]" });
    for (const label of ["Created", "Integration registered", "Trial changed", "Image added", "Published", "Unpublished"]) await waitForText(page, label);
    return "hidden; activity lists created, integration, trial, images, published, unpublished";
  });

  await flow("X. delete only what nothing uses; the test product is archived instead", async () => {
    const refused = await api(page, "DELETE", `/api/v1/admin/products/${productId}`);
    check(refused.status === 400 && /Archive it instead/.test(refused.body.detail), `delete of test product: ${refused.status} ${JSON.stringify(refused.body)}`);
    await page.goto(`${BASE}/admin/products/${productId}?tab=availability`, { waitUntil: "networkidle0" });
    await click(page, "Archive");
    await click(page, "Confirm archive");
    await waitForText(page, "is archived");
    const p = (await api(page, "GET", `/api/v1/admin/products/${productId}`)).body.product;
    check(p.manager_status === "archived" && p.listing === "hidden", `archived ${p.status}/${p.listing}`);

    // A product nothing ever used is deleted from its own page.
    await page.goto(`${BASE}/admin/products/new`, { waitUntil: "networkidle0" });
    await type(page, "Product name", "Scratch Delete Me");
    await click(page, "Save Draft");
    await page.waitForFunction(() => /^\/admin\/products\/[0-9a-f-]{36}$/.test(location.pathname), { timeout: 60000 });
    const scratchId = page.url().match(/products\/([0-9a-f-]{36})/)[1];
    await click(page, "Delete", { within: "main header" });
    await click(page, "Confirm delete");
    await page.waitForFunction(() => location.pathname === "/admin/products", { timeout: 30000 });
    const gone = await api(page, "GET", `/api/v1/admin/products/${scratchId}`);
    check(gone.status === 404, `scratch product still there: ${gone.status}`);
    return "test product: delete refused (has integration) -> archived; unused draft deleted";
  });

  await flow("M. mobile (390 px): list, wizard and detail are usable", async () => {
    await page.setViewport({ width: 390, height: 844 });
    await page.goto(`${BASE}/admin/products`, { waitUntil: "networkidle0" });
    await page.screenshot({ path: path.join(SHOTS, "mobile-list.png"), fullPage: true });
    await page.goto(`${BASE}/admin/products/new`, { waitUntil: "networkidle0" });
    await page.screenshot({ path: path.join(SHOTS, "mobile-wizard.png"), fullPage: true });
    await page.goto(`${BASE}/admin/products/${productId}`, { waitUntil: "networkidle0" });
    await page.screenshot({ path: path.join(SHOTS, "mobile-detail.png"), fullPage: true });
    const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    check(r.scroll <= r.inner, `detail scrolls sideways at 390: ${r.scroll}`);
    await page.setViewport({ width: 1440, height: 900 });
    return "screenshots written";
  });

  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  const axeBad = report.pages.filter((p) => p.axe.length);
  const focusBad = report.pages.filter((p) => p.noFocus.length);
  const h1Bad = report.pages.filter((p) => p.h1.length !== 1);
  const reflowBad = report.reflow.filter((r) => !r.ok);
  const flowsBad = report.flows.filter((f) => !f.ok);
  console.log(JSON.stringify({ pages: report.pages.length, axeBad: axeBad.map((p) => [p.label, p.axe]), focusBad: focusBad.map((p) => [p.label, p.noFocus]), h1Bad: h1Bad.map((p) => [p.label, p.h1]), reflowBad, csp: report.csp, errors: report.errors, flows: report.flows }, null, 2));
  const pass = !axeBad.length && !focusBad.length && !h1Bad.length && !reflowBad.length && !flowsBad.length && !report.csp.length && !report.errors.length;
  console.log(pass ? "BROWSER CHECK PASS" : "BROWSER CHECK FAIL");
  process.exit(pass ? 0 : 1);
})();
