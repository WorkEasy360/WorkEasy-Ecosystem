// Real-browser acceptance of the full-stack Store upgrade (ADR-056), against the
// production frontend build and a real backend on the scratch database prepared
// by prepare.py (w360_su_fresh). Harness helpers as scripts/store_console_check.
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
const OUT = process.env.OUT || "store_upgrade_report.json";
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

// --- ADR-056 journeys (appended to the shared harness by build_check.py) ------------------
//   A  Super Admin: Store Overview and Commercial rules render; Easy CRM's
//      affiliate commission set to 30% (recurring, 12 months) from the screen;
//      a white-label price range INR 500-900 for Easy CRM Basic (monthly)
//   B  Super Admin: Easy CRM -> Plans & Pricing; Basic's monthly price gets a
//      10% discount and an INR 250 set-up fee from the Edit price dialog
//   C  Customer: the Store card shows the discounted price struck through; the
//      product page compares the plans; checkout charges price + set-up fee
//      (the server's quote); test payment -> the plan is held
//   D  White-label: Pricing & Plans shows the range; sets its own price 699;
//      the server refuses 100; its quote for the plan is the new price
//   E  Wrong access: a customer and a partner cannot reach admin/store; an
//      affiliate cannot set a price
//   R  Responsive: the new pages at 1920-390 px
const WL = "/partner/white-label";

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run", "--force-prefers-reduced-motion"] });
  const staff = await newPage(await browser.createBrowserContext());
  const buyer = await newPage(await browser.createBrowserContext());
  const wl = await newPage(await browser.createBrowserContext());
  const aff = await newPage(await browser.createBrowserContext());
  const state = {};

  await flow("A Super Admin: Store overview, commercial rules, commission, price range", async () => {
    await signIn(staff, "staff@eco.test", "admin");
    await go(staff, "/admin/store/dashboard");
    await waitForText(staff, "Published products");
    await audit(staff, "Store - Overview");
    await go(staff, "/admin/store/commercial-rules");
    await waitForText(staff, "Easy CRM");
    await audit(staff, "Store - Commercial rules");
    const rules = await api(staff, "GET", "/api/v1/admin/store/commercial-rules?currency=INR");
    check(rules.status === 200, `rules ${rules.status}`);
    const crm = rules.body.products.find((p) => p.code === "easy_crm");
    state.crmId = crm.id;
    state.basic = crm.plans.find((p) => p.code === "easy_crm_basic");
    const levels = Object.keys(state.basic.channels).sort().join(",");
    check(levels === "affiliate,direct,reseller,white_label", `channels ${levels}`);
    check(state.basic.channels.white_label.monthly.earning === "margin", "white-label earns its 25% margin");

    // Edit commission (the first product's button is Easy CRM's: catalog order).
    await staff.evaluate(() => {
      const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2") && s.querySelector("h2").innerText.trim() === "Easy CRM");
      [...section.querySelectorAll("button")].find((b) => b.innerText.trim() === "Edit commission").click();
    });
    await waitForText(staff, "Commission: Easy CRM");
    await type(staff, "Commission %", "30");
    await staff.evaluate(() => [...document.querySelectorAll('[role="dialog"] input[type="checkbox"]')][0].click());
    await type(staff, "For how many months", "12");
    await audit(staff, "Commercial rules - commission dialog");
    await click(staff, "Save commission");
    await waitForText(staff, "Commission for Easy CRM saved");
    const after = await api(staff, "GET", "/api/v1/admin/store/commercial-rules?currency=INR");
    const rule = after.body.products.find((p) => p.code === "easy_crm").affiliate_commission;
    check(rule.rate_bp === 3000 && rule.recurring && rule.recurring_months === 12, `rule ${JSON.stringify(rule)}`);

    // White-label price range for Easy CRM Basic.
    await staff.evaluate(() => {
      const section = [...document.querySelectorAll("section")].find((s) => s.querySelector("h2") && s.querySelector("h2").innerText.trim() === "Easy CRM");
      const row = [...section.querySelectorAll("tbody tr")].find((r) => r.querySelector("th").innerText.startsWith("Basic"));
      const cells = row.querySelectorAll("td");
      [...cells[cells.length - 1].querySelectorAll("button")].find((b) => b.innerText.trim() === "Add range").click();
    });
    await waitForText(staff, "White-label price range: Basic");
    await type(staff, "Lowest price", "500");
    await type(staff, "Highest price", "900");
    await click(staff, "Save range");
    await waitForText(staff, "Price range for Basic saved");
    const ranges = await api(staff, "GET", "/api/v1/admin/store/price-ranges");
    const r = ranges.body.results.find((x) => x.plan_id === state.basic.plan_id && x.experience === "white_label");
    check(r && r.min_minor === 50000 && r.max_minor === 90000 && r.partner_may_set, `range ${JSON.stringify(r)}`);
    return `commission 30% x 12 months; white-label range INR 500-900; affiliate pays ${after.body.products.find((p) => p.code === "easy_crm").plans.find((p) => p.code === "easy_crm_basic").channels.affiliate.monthly.commission_minor} minor`;
  });

  await flow("B Super Admin: Plans & Pricing - discount and set-up fee from the dialog", async () => {
    await go(staff, `/admin/products/${state.crmId}?tab=plans`);
    await waitForText(staff, "Plans & Pricing");
    await waitForText(staff, "Edit price");
    await audit(staff, "Product - Plans & Pricing");
    await staff.evaluate(() => {
      const card = [...document.querySelectorAll("section")].find((s) => s.querySelector("h3") && s.querySelector("h3").innerText.trim() === "Basic");
      [...card.querySelectorAll("button")].find((b) => b.innerText.trim() === "Edit price").click();
    });
    await waitForText(staff, "Edit price: Basic");
    // The monthly fieldset is the dialog's first.
    await staff.evaluate(() => {
      const set = (input, value) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        setter.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      };
      const monthly = document.querySelector('[role="dialog"] fieldset');
      const inputs = monthly.querySelectorAll("input");
      set(inputs[1], "10");
      set(inputs[2], "250");
    });
    await audit(staff, "Plans & Pricing - edit price dialog");
    await click(staff, "Save price");
    await waitForText(staff, "Price of Basic updated");
    await waitForText(staff, "10% off");
    const board = await api(staff, "GET", `/api/v1/admin/store/products/${state.crmId}/plans`);
    const basic = board.body.plans.find((p) => p.code === "easy_crm_basic");
    const monthly = basic.prices.find((p) => p.interval === "monthly" && p.state === "current");
    check(monthly.amount_minor === 99900 && monthly.discount_bp === 1000 && monthly.net_amount_minor === 89910 && monthly.setup_fee_minor === 25000, `price ${JSON.stringify(monthly)}`);
    state.basicVersion = basic.version.id;
    return "Easy CRM Basic monthly: INR 999.00 less 10% = 899.10, set-up INR 250";
  });

  await flow("C Customer: discounted card, product page, comparison, checkout with set-up fee, payment", async () => {
    await signIn(buyer, "buyer@eco.test", "user");
    const orgs = await api(buyer, "GET", "/api/v1/organizations");
    const slug = orgs.body.organizations[0].slug;
    state.slug = slug;
    await go(buyer, `/o/${slug}/store`);
    await waitForText(buyer, "Easy CRM");
    const card = await buyer.evaluate(() => {
      const li = [...document.querySelectorAll('ul[aria-label="Products"] > li')].find((x) => x.innerText.includes("Easy CRM"));
      return li ? li.innerText : "";
    });
    check(card.includes("899.10") && card.includes("10% off"), `card: ${card.replace(/\n+/g, " | ")}`);
    await audit(buyer, "Customer Store (discount shown)");
    await click(buyer, "Easy CRM");
    await buyer.waitForFunction(() => location.pathname.endsWith("/store/easy_crm"), { timeout: 30000 });
    await waitForText(buyer, "Compare plans");
    await waitForText(buyer, "one-time set-up");
    await audit(buyer, "Customer product page");
    const quote = await api(buyer, "POST", `/api/v1/orgs/${slug}/orders/quote`, { plan_version_id: state.basicVersion, billing_interval: "monthly" });
    check(quote.status === 200 && quote.body.amount_due_minor === 89910 + 25000 && quote.body.setup_fee_minor === 25000, `quote ${JSON.stringify(quote.body).slice(0, 300)}`);
    await click(buyer, "Buy now: Basic");
    await buyer.waitForFunction(() => location.pathname.includes("/checkout"), { timeout: 30000 });
    await waitForText(buyer, "Place order and pay");
    await audit(buyer, "Customer checkout (set-up fee)");
    await Promise.all([buyer.waitForNavigation({ timeout: 60000 }).catch(() => null), click(buyer, "Place order and pay")]);
    await buyer.waitForFunction(() => location.pathname.includes("/billing/test-checkout"), { timeout: 60000 });
    await waitForText(buyer, "Pay (succeed)");
    await click(buyer, "Pay (succeed)");
    await buyer.waitForNetworkIdle({ idleTime: 800, timeout: 30000 }).catch(() => {});
    await sleep(2000);
    const subs = await api(buyer, "GET", `/api/v1/orgs/${slug}/subscriptions`);
    const lines = (subs.body.results || []).flatMap((s) => s.items.map((i) => `${i.plan_code}:${s.status}`));
    check(lines.includes("easy_crm_basic:active"), `subscription ${lines}`);
    await go(buyer, `/o/${slug}/store/easy_crm`);
    await waitForText(buyer, "Your plan");
    return `paid INR 1,149.10 (899.10 + 250 set-up); lines ${lines.join(", ")}`;
  });

  await flow("D White-label sets its own price inside the range", async () => {
    await signIn(wl, "wl@eco.test", "partner");
    state.wlId = await partnerId(wl);
    await go(wl, `${WL}/plans`);
    await waitForText(wl, "Pricing & Margins");
    await waitForText(wl, "Set my price");
    await audit(wl, "White-label Pricing & Margins");
    await click(wl, "Set your price: Basic");
    await waitForText(wl, "Your price: Basic");
    await type(wl, "Customer price", "100");
    await click(wl, "Save price");
    await waitForText(wl, "The price must be between");
    await type(wl, "Customer price", "699");
    await click(wl, "Save price");
    await waitForText(wl, "Your price for Basic is now");
    const refused = await api(wl, "POST", `/api/v1/partner/${state.wlId}/own-price`, { plan_version_id: state.basicVersion, interval: "monthly", amount_minor: 150000 });
    check(refused.status === 400, `outside range -> ${refused.status}`);
    const plans = await api(wl, "GET", `/api/v1/partner/${state.wlId}/plans`);
    const line = plans.body.plans.find((p) => p.plan_code === "easy_crm_basic").prices.find((p) => p.interval === "monthly");
    check(line.customer_price_minor === 69900 && line.source === "partner_price_book", `line ${JSON.stringify(line)}`);
    return `own price INR 699 (cost ${line.partner_cost_minor}, margin ${line.margin_minor}); 1,500 refused (400)`;
  });

  await flow("E Wrong access is refused by the server", async () => {
    const cust = await api(buyer, "GET", "/api/v1/admin/store/dashboard");
    check([401, 403].includes(cust.status), `customer -> admin store ${cust.status}`);
    const partner = await api(wl, "GET", "/api/v1/admin/store/commercial-rules");
    check([401, 403].includes(partner.status), `partner -> admin rules ${partner.status}`);
    const range = await api(wl, "POST", "/api/v1/admin/store/price-ranges", { experience: "white_label", plan_id: state.basic.plan_id, interval: "monthly", currency: "INR", min_minor: 1, max_minor: null });
    check([401, 403].includes(range.status), `partner -> set range ${range.status}`);
    await signIn(aff, "aman@eco.test", "partner");
    const affId = await partnerId(aff);
    const own = await api(aff, "POST", `/api/v1/partner/${affId}/own-price`, { plan_version_id: state.basicVersion, interval: "monthly", amount_minor: 60000 });
    check([400, 403, 404].includes(own.status), `affiliate -> own price ${own.status}`);
    return `customer ${cust.status}, partner ${partner.status}/${range.status}, affiliate own price ${own.status}`;
  });

  await flow("R Responsive: the new pages at 1920-390 px", async () => {
    await go(staff, "/admin/store/dashboard");
    await waitForText(staff, "Published products");
    await reflow(staff, "store-overview");
    await go(staff, "/admin/store/commercial-rules");
    await waitForText(staff, "Easy CRM");
    await reflow(staff, "commercial-rules");
    await go(staff, `/admin/products/${state.crmId}?tab=plans`);
    await waitForText(staff, "Edit price");
    await reflow(staff, "plans-pricing");
    await go(buyer, `/o/${state.slug}/store/easy_crm`);
    await waitForText(buyer, "Compare plans");
    await reflow(buyer, "product-page");
    await go(wl, `${WL}/plans`);
    await waitForText(wl, "Pricing & Margins");
    await reflow(wl, "partner-pricing");
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
