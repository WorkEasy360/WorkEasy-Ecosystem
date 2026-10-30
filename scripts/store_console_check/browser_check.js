// Real-browser acceptance of the affiliate Store Console (ADR-055), against the
// production frontend build and a real backend on the scratch database
// prepared by prepare.py (w360_sc_fresh). The brief's final goal, end to end:
//   A  Affiliate: Store Console in the sidebar; My Products empty; Explore lists
//      the Store's products, each Not requested; Request Access to Easy CRM and
//      EasyBook; a second request for Easy CRM is refused; My Requests
//   B  Super Admin: Partners -> Product Requests; review; approve Easy CRM;
//      reject EasyBook with a reason
//   C  Affiliate: Easy CRM is in My Products; generate a link and a custom code
//      in a campaign; EasyBook shows Rejected with the reason
//   D  Wrong access: the affiliate cannot reach the staff API; a link for
//      EasyBook is refused by the server
//   E  Super Admin revokes Easy CRM -> the affiliate cannot make a new link;
//      My Products lists it under "No longer assigned"; Explore says Revoked
//   F  The old My Products address opens the Store Console
//   R  Responsive: the three tabs and the admin queue at 1920-390 px
// On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA, a
// visible focus indicator, exactly one h1. Screen readers: NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3999";
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = process.env.CHECK_PASSWORD;
if (!PASSWORD) throw new Error("CHECK_PASSWORD is required");
const OUT = process.env.OUT || "store_console_report.json";
const SHOTS = path.join(path.dirname(OUT), "screens");
fs.mkdirSync(SHOTS, { recursive: true });
const WIDTHS = [1920, 1440, 1366, 1024, 768, 390];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (cond, msg) => {
  if (!cond) throw new Error(msg);
};

async function newPage(context, width = 1440) {
  const page = await context.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    if (m.text().startsWith("Failed to load resource") && /status of (400|401|403|404|409|429)/.test(m.text())) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function audit(page, label) {
  await sleep(500);
  (await page.evaluate(() => window.__csp || [])).forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 40), visible: ring || el.matches("input,select,textarea") };
    });
    if (info && !info.visible) noFocus.push(`${info.tag} "${info.text}"`);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url().replace(BASE, ""), axe, noFocus, h1 });
}

async function reflow(page, label) {
  for (const width of WIDTHS) {
    await page.setViewport({ width, height: width === 390 ? 844 : 900 });
    await sleep(400);
    const r = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    report.reflow.push({ label, width, ok: r.scroll <= r.inner, scroll: r.scroll });
    if (width === 390 || width === 1440) await page.screenshot({ path: path.join(SHOTS, `${label.replace(/\W+/g, "-")}-${width}.png`), fullPage: true });
  }
  await page.setViewport({ width: 1440, height: 900 });
  await sleep(300);
}

const bodyText = (page) => page.evaluate(() => (document.querySelector("main") || document.body).innerText);

async function waitForText(page, wanted, timeout = 25000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, wanted).catch(async () => {
    throw new Error(`text not found: "${wanted}" on ${page.url()} :: ${(await bodyText(page)).slice(0, 600).replace(/\n+/g, " | ")}`);
  });
}

async function click(page, label, { exact = true, timeout = 15000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const ok = await page.evaluate(
      (label, exact) => {
        const el = [...document.querySelectorAll("button, a")].find((c) => {
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
      label,
      exact,
    );
    if (ok) return;
    await sleep(250);
  }
  throw new Error(`nothing to click for "${label}" on ${page.url()}`);
}

async function field(page, label) {
  const id = await page.evaluate((label) => {
    const l = [...document.querySelectorAll("label")].find((x) => x.innerText.trim().startsWith(label));
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

async function choose(page, label, value) {
  await page.select(await field(page, label), value);
}

function api(page, method, url, body) {
  return page.evaluate(
    async (method, url, body) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf ? decodeURIComponent(csrf) : "", Accept: "application/json", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data = null;
      try {
        data = await r.json();
      } catch {}
      return { status: r.status, body: data };
    },
    method,
    url,
    body,
  );
}

async function signIn(page, email, mode) {
  await page.goto(`${BASE}/login?mode=${mode}`, { waitUntil: "networkidle0" });
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 30000 }).catch(() => {});
}

async function go(page, route) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle0" });
}

async function flow(name, fn) {
  const started = Date.now();
  try {
    const detail = await fn();
    report.flows.push({ name, ok: true, ms: Date.now() - started, detail });
    console.log("PASS", name, detail || "");
  } catch (e) {
    report.flows.push({ name, ok: false, ms: Date.now() - started, error: String(e.message || e).slice(0, 1500) });
    console.log("FAIL", name, String(e.message || e).slice(0, 1500));
  }
}

const AFF = "/partner/affiliate";

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run", "--force-prefers-reduced-motion"] });
  const affCtx = await browser.createBrowserContext();
  const staffCtx = await browser.createBrowserContext();
  const aff = await newPage(affCtx);
  const staff = await newPage(staffCtx);
  const state = {};

  await flow("A Affiliate explores the Store and requests access", async () => {
    await signIn(aff, "aman@eco.test", "partner");
    await go(aff, `${AFF}/dashboard`);
    const sidebar = await aff.evaluate(() => [...document.querySelectorAll("nav a")].map((a) => a.innerText.trim()));
    check(sidebar.includes("Store Console") && !sidebar.includes("My Products"), `sidebar ${JSON.stringify(sidebar)}`);
    for (const kept of ["Referral Links", "Referral Codes", "Campaigns", "Marketing Materials", "Leads & Referrals", "Conversions", "Commissions", "Payouts", "Reports & Analytics", "Profile & Settings", "Support"])
      check(sidebar.includes(kept), `missing ${kept}`);
    await click(aff, "Store Console");
    await waitForText(aff, "No approved products yet");
    await audit(aff, "Store Console - My Products (empty)");
    await click(aff, "Explore Products", { exact: false });
    await waitForText(aff, "Easy CRM");
    const store = await api(aff, "GET", `/api/v1/partner/${await partnerId(aff)}/affiliate/store`);
    state.partnerId = await partnerId(aff);
    check(store.status === 200, `store ${store.status}`);
    const codes = store.body.products.map((p) => `${p.code}:${p.status}`).sort();
    check(codes.length === 4 && codes.every((c) => c.endsWith(":not_requested")), `products ${codes}`);
    await audit(aff, "Store Console - Explore");
    for (const [name, code] of [
      ["Easy CRM", "easy_crm"],
      ["EasyBook", "easybook"],
    ]) {
      const product = store.body.products.find((p) => p.code === code);
      await click(aff, `Request access to ${product.name}`);
      await waitForText(aff, `Request access to ${product.name}`);
      await choose(aff, "Promotion channel", "website");
      await type(aff, "Target audience", "Small businesses in India");
      await type(aff, "How you will promote it", `A ${name} review on my blog and a monthly newsletter.`);
      if (code === "easy_crm") await audit(aff, "Store Console - Request form");
      await click(aff, "Send request");
      await waitForText(aff, `Request sent for ${product.name}`);
    }
    const again = await api(aff, "POST", `/api/v1/partner/${state.partnerId}/affiliate/product-requests`, {
      product_code: "easy_crm",
      channel: "website",
      audience: "Small businesses",
      strategy: "The same request a second time.",
    });
    check(again.status === 409, `duplicate request -> ${again.status}`);
    await click(aff, "My Requests", { exact: false });
    await waitForText(aff, "Pending approval");
    await audit(aff, "Store Console - My Requests");
    return "2 requests pending, duplicate refused (409)";
  });

  await flow("B Super Admin reviews: approves one, rejects one with a reason", async () => {
    await signIn(staff, "staff@eco.test", "admin");
    await go(staff, "/admin/partners");
    await click(staff, "Product Requests", { exact: false });
    await waitForText(staff, "Aman Affiliates");
    await audit(staff, "Admin - Product Requests queue");
    const queue = await api(staff, "GET", "/api/v1/admin/partner-product-requests?status=pending");
    check(queue.body.counts.pending === 2, `pending ${JSON.stringify(queue.body.counts)}`);
    const byCode = Object.fromEntries(queue.body.items.map((r) => [r.product_code, r]));
    await go(staff, `/admin/partners/product-requests?request=${byCode.easy_crm.id}`);
    await waitForText(staff, "What the affiliate wrote");
    await audit(staff, "Admin - Review request");
    await click(staff, "Approve");
    await waitForText(staff, "Approved: Aman Affiliates may promote");
    await go(staff, `/admin/partners/product-requests?request=${byCode.easybook.id}`);
    await waitForText(staff, "What the affiliate wrote");
    await click(staff, "Reject");
    await type(staff, "Reason", "Your audience does not match accounting buyers yet.");
    await click(staff, "Reject request");
    await waitForText(staff, "Request rejected.");
    const twice = await api(staff, "POST", `/api/v1/admin/partner-product-requests/${byCode.easy_crm.id}/approve`, {});
    check(twice.status === 409, `second approval -> ${twice.status}`);
    state.crmRequest = byCode.easy_crm.id;
    return "approved Easy CRM, rejected EasyBook, a second approval 409";
  });

  await flow("C Affiliate promotes the approved product; sees the rejection reason", async () => {
    await go(aff, `${AFF}/store-console`);
    await waitForText(aff, "Approved for you on");
    await click(aff, "Generate link");
    await aff.waitForFunction(() => /\/r\/[A-Z0-9]+/.test(document.body.innerText), { timeout: 20000 });
    const campaign = await api(aff, "POST", `/api/v1/partner/${state.partnerId}/campaigns`, { name: "CRM November" });
    check(campaign.status === 201, `campaign ${campaign.status}`);
    const code = await api(aff, "POST", `/api/v1/partner/${state.partnerId}/referrals`, {
      link_kind: "product",
      product_code: "easy_crm",
      campaign_id: campaign.body.id,
      code: "AMANCRM",
    });
    check(code.status === 201 && code.body.code === "AMANCRM", `custom code ${code.status} ${JSON.stringify(code.body).slice(0, 200)}`);
    await go(aff, `${AFF}/store-console`);
    await waitForText(aff, "AMANCRM");
    await audit(aff, "Store Console - My Products (approved)");
    await go(aff, `${AFF}/store-console/explore`);
    await waitForText(aff, "Reason: Your audience does not match accounting buyers yet.");
    return "link + campaign code AMANCRM made; rejection reason shown";
  });

  await flow("D Wrong access is refused by the server", async () => {
    const queue = await api(aff, "GET", "/api/v1/admin/partner-product-requests");
    check([401, 403, 404].includes(queue.status), `affiliate -> staff queue ${queue.status}`);
    const approve = await api(aff, "POST", `/api/v1/admin/partner-product-requests/${state.crmRequest}/approve`, {});
    check([401, 403, 404].includes(approve.status), `affiliate -> approve ${approve.status}`);
    const books = await api(aff, "POST", `/api/v1/partner/${state.partnerId}/referrals`, { link_kind: "product", product_code: "easybook" });
    check(books.status === 400, `rejected product link -> ${books.status}`);
    return `queue ${queue.status}, approve ${approve.status}, EasyBook link ${books.status}`;
  });

  await flow("E Revocation stops new links and keeps history", async () => {
    await go(staff, `/admin/partners/product-requests?status=approved&request=${state.crmRequest}`);
    await waitForText(staff, "What the affiliate wrote");
    await click(staff, "Revoke permission");
    await type(staff, "Reason", "Campaign period ended.");
    await click(staff, "Revoke permission");
    await waitForText(staff, "Permission revoked.");
    const link = await api(aff, "POST", `/api/v1/partner/${state.partnerId}/referrals`, { link_kind: "product", product_code: "easy_crm" });
    check(link.status === 400, `link after revoke -> ${link.status}`);
    await go(aff, `${AFF}/store-console`);
    await waitForText(aff, "No longer assigned");
    await waitForText(aff, "AMANCRM");
    await go(aff, `${AFF}/store-console/explore`);
    await waitForText(aff, "Reason: Campaign period ended.");
    await go(aff, `${AFF}/store-console/requests`);
    await waitForText(aff, "Revoked");
    return "new link 400; old links listed under No longer assigned";
  });

  await flow("F The old My Products address opens the Store Console", async () => {
    await go(aff, `${AFF}/my-products`);
    await aff.waitForFunction(() => location.pathname.endsWith("/store-console"), { timeout: 15000 });
    return aff.url().replace(BASE, "");
  });

  await flow("R Responsive: 1920-390 px", async () => {
    for (const [page, route, label] of [
      [aff, `${AFF}/store-console`, "my-products"],
      [aff, `${AFF}/store-console/explore`, "explore"],
      [aff, `${AFF}/store-console/requests`, "requests"],
      [staff, "/admin/partners/product-requests?status=all", "admin-queue"],
      [staff, `/admin/partners/product-requests?status=all&request=${state.crmRequest}`, "admin-review"],
    ]) {
      await go(page, route);
      await reflow(page, label);
    }
    const bad = report.reflow.filter((r) => !r.ok);
    check(bad.length === 0, `sideways scroll: ${JSON.stringify(bad)}`);
    // The sidebar is reachable on a phone.
    await aff.setViewport({ width: 390, height: 844 });
    await go(aff, `${AFF}/store-console/explore`);
    const menu = await aff.evaluate(() => !![...document.querySelectorAll("button")].find((b) => /menu/i.test(b.getAttribute("aria-label") || b.innerText)));
    await aff.setViewport({ width: 1440, height: 900 });
    check(menu, "no menu button at 390 px");
    return `${report.reflow.length} viewport checks`;
  });

  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  const axeBad = report.pages.filter((p) => p.axe.length);
  const focusBad = report.pages.filter((p) => p.noFocus.length);
  const h1Bad = report.pages.filter((p) => p.h1.length !== 1);
  const reflowBad = report.reflow.filter((r) => !r.ok);
  const flowsBad = report.flows.filter((f) => !f.ok);
  console.log(
    JSON.stringify(
      {
        pages: report.pages.length,
        axeBad: axeBad.map((p) => [p.label, p.axe]),
        focusBad: focusBad.map((p) => [p.label, p.noFocus]),
        h1Bad: h1Bad.map((p) => [p.label, p.h1]),
        reflowBad,
        csp: report.csp,
        errors: report.errors,
        flows: report.flows.map((f) => [f.name, f.ok, f.detail || f.error]),
      },
      null,
      2,
    ),
  );
  const pass = !axeBad.length && !focusBad.length && !h1Bad.length && !reflowBad.length && !flowsBad.length && !report.csp.length && !report.errors.length;
  console.log(pass ? "BROWSER CHECK PASS" : "BROWSER CHECK FAIL");
  process.exit(pass ? 0 : 1);
})();

async function partnerId(page) {
  const portal = await api(page, "GET", "/api/v1/partner/portal");
  const p = (portal.body.partners || portal.body.results || [])[0] || portal.body.partner || portal.body;
  return p.partner_id || p.id;
}
