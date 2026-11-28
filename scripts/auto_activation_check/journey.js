// ADR-078 automatic activation, in Chrome, on the deployable build (run by run.py on the stack that
// Accounts/e2e/run_universal_acceptance.py keeps up):
//
//   NODE_PATH=<node_modules with puppeteer-core> node journey.js <auto-context.json> <phase>
//
// Phases: trial | trial-second | paid | concurrent | existing | admin. Every check is recorded PASS/FAIL with
// what the page said. Nothing is mocked: Chrome maps *.workeasy360.com to the local production edge; Easy CRM
// is the real product (its Django backend and Next.js frontend); payment is the development test provider's
// hosted page, applied through the signed-webhook path.

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const ctx = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const PHASE = process.argv[3];
const OUT = ctx.out;
const RESULTS = path.join(path.dirname(ctx.results), `journey-${PHASE}.json`);
const O = ctx.origins;
const ACCOUNTS = ctx.accounts;
const STORE = ctx.store; // {crm: {code, slug, title, origin, trial_days}, accounts: {...}}
const result = { phase: PHASE, checks: [], data: {}, console_errors: [], axe: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const AXE = fs.existsSync(ctx.axe) ? fs.readFileSync(ctx.axe, "utf8") : null;

function check(step, ok, detail = "") {
  result.checks.push({ step, ok: Boolean(ok), detail: String(detail).slice(0, 1500) });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${step}${detail ? ` - ${String(detail).slice(0, 300)}` : ""}`);
  return Boolean(ok);
}
function save() {
  fs.writeFileSync(RESULTS, JSON.stringify(result, null, 2));
}
async function waitFor(predicate, { timeout = 60000, interval = 400 } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    let value = null;
    try {
      value = await predicate();
    } catch {
      value = null;
    }
    if (value) return value;
    if (Date.now() > deadline) return null;
    await sleep(interval);
  }
}
async function text(page) {
  for (let i = 0; i < 20; i += 1) {
    try {
      return await page.evaluate(() => (document.body ? document.body.innerText : ""));
    } catch {
      await sleep(250);
    }
  }
  return "";
}
const waitText = (page, needle, timeout = 60000) =>
  waitFor(async () => {
    const t = await text(page);
    return needle instanceof RegExp ? needle.test(t) : t.includes(needle);
  }, { timeout });
async function shot(page, name) {
  await sleep(400);
  await page.screenshot({ path: path.join(OUT, `${PHASE}-${name}.png`), fullPage: true });
}
const pathOf = (page) => new URL(page.url()).pathname;
const onAccountsLogin = (page) => page.url().startsWith(`${ACCOUNTS}/login`);
const onChooser = (page) => page.url().startsWith(`${ACCOUNTS}/product-access`);

function watch(page) {
  page.on("console", (msg) => {
    if (msg.type() === "error") result.console_errors.push(`${page.url()} ${msg.text()}`.slice(0, 300));
  });
  page.on("pageerror", (err) => result.console_errors.push(`${page.url()} pageerror ${String(err)}`.slice(0, 300)));
}
async function launch(browser, viewport = { width: 1280, height: 900 }) {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(90000);
  page.setDefaultTimeout(60000);
  await page.setViewport(viewport);
  watch(page);
  return { context, page };
}

/** axe WCAG 2.1 A/AA, one h1, no sideways scroll - on the page as it stands. */
async function audit(page, label) {
  const facts = await page.evaluate(() => ({
    h1: document.querySelectorAll("h1").length,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }));
  let violations = [];
  if (AXE) {
    await page.evaluate(AXE);
    violations = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
      return r.violations.map((v) => `${v.id} (${v.nodes.length})`);
    });
  }
  result.axe.push({ label, url: page.url(), ...facts, violations });
  check(`${label}: axe WCAG 2.1 A/AA clean, one h1, no sideways scroll`, violations.length === 0 && facts.h1 === 1 && !facts.overflow,
    JSON.stringify({ violations, ...facts }));
}

/** Click the first enabled button or link whose accessible name or text matches. */
async function press(page, matcher, { timeout = 60000 } = {}) {
  const source = matcher instanceof RegExp ? matcher.source : matcher;
  const isRe = matcher instanceof RegExp;
  const handle = await page.waitForFunction(
    (src, re) => {
      const test = (s) => (re ? new RegExp(src).test(s) : s === src || s.startsWith(src));
      return [...document.querySelectorAll("button, a")].find((el) => {
        if (el.disabled || el.getAttribute("aria-disabled") === "true") return false;
        const name = (el.getAttribute("aria-label") || el.innerText || "").replace(/\s+/g, " ").trim();
        return test(name) || test((el.innerText || "").replace(/\s+/g, " ").trim());
      }) || null;
    },
    { timeout },
    source,
    isRe,
  );
  const el = handle.asElement();
  await el.evaluate((node) => node.scrollIntoView({ block: "center" }));
  await el.click();
}
async function hasControl(page, matcher) {
  return page.evaluate((src) => {
    const re = new RegExp(src);
    return [...document.querySelectorAll("button, a")].some((el) => re.test((el.getAttribute("aria-label") || el.innerText || "").replace(/\s+/g, " ").trim()));
  }, matcher.source);
}

/** Sign in as a customer does from the Store: the sign-in page (WorkEasy360 Accounts under ADR-071), then back to
 * the Store page asked for. */
async function signInTo(page, person, storePath) {
  await page.goto(`${O.app}/login?next=${encodeURIComponent(storePath)}`, { waitUntil: "networkidle2" });
  await waitFor(() => onAccountsLogin(page) || page.url().startsWith(`${O.app}/login`) || pathOf(page).startsWith("/store"), { timeout: 90000 });
  if (!pathOf(page).startsWith("/store") || onAccountsLogin(page)) {
    await page.waitForSelector('input[type="email"]', { timeout: 60000 });
    await page.type('input[type="email"]', person.email);
    await page.type('input[type="password"]', ctx.password);
    await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle2", timeout: 90000 }).catch(() => null),
      page.click('button[type="submit"]'),
    ]);
  }
  const back = await waitFor(() => page.url().startsWith(O.app) && pathOf(page).startsWith("/store"), { timeout: 90000 });
  if (!back) await page.goto(`${O.app}${storePath}`, { waitUntil: "networkidle2" });
  return page.url();
}

/** From Open Product to wherever the flow stops; Continue on the access chooser (Ask once) when it asks. */
async function enterProduct(page, product) {
  const settle = () =>
    waitFor(async () => {
      if (onChooser(page)) return (await text(page)).includes("Continue as") ? "chooser" : null;
      if (onAccountsLogin(page)) return "login";
      if (page.url().startsWith(product.origin)) {
        if (/\/(api|auth)\//.test(pathOf(page))) return null;
        const t = await text(page);
        return t.trim().length > 20 && !/Loading|Compiling/.test(t.slice(0, 200)) ? "product" : null;
      }
      return null;
    }, { timeout: 120000, interval: 500 });
  let where = await settle();
  let chooser = false;
  if (where === "chooser") {
    chooser = true;
    await press(page, /^Continue as /);
    where = await settle();
  }
  const session = where === "product" ? await productSession(page, product) : null;
  return { where, chooser, session, url: page.url() };
}
async function productSession(page, product) {
  const probe = product.code === "easy_crm" ? "/api/v1/session/" : "/app/";
  return page.evaluate(async (url) => {
    const r = await fetch(url, { credentials: "include", headers: { Accept: "application/json, text/html" }, redirect: "manual" });
    let body = "";
    try {
      body = await r.text();
    } catch {
      body = "";
    }
    return { status: r.status, body: body.slice(0, 1500) };
  }, probe);
}

// --- phases ---------------------------------------------------------------------------------------------------

/** A brand-new customer (an account, no organization): product page -> Start Free Trial -> Open Product -> CRM. */
async function phaseTrial(browser) {
  const neha = ctx.people.neha;
  const crm = STORE.crm;
  const { page } = await launch(browser);
  await signInTo(page, neha, `/store/product/${crm.slug}`);
  const startLabel = new RegExp(`^Start ${crm.trial_days}-day Free Trial: ${crm.title}$`);
  const offered = await waitFor(() => hasControl(page, startLabel), { timeout: 60000 });
  await shot(page, "1-product-page-start-trial");
  check("T.1 signed in, no organization: the product page offers Start Free Trial (one click, no checkout)", offered,
    (await text(page)).slice(0, 400));
  await audit(page, "T.1 product page (signed in)");

  await press(page, startLabel);
  const opened = await waitFor(() => hasControl(page, new RegExp(`^Open Product: ${crm.title}$`)), { timeout: 90000 });
  await shot(page, "2-open-product");
  const t = await text(page);
  check("T.2 Start Free Trial creates the workspace, starts the trial, provisions and seats: Open Product", opened, t.slice(0, 500));
  check("T.3 the trial's days are shown", /Free trial · \d+ days? left/.test(t), (t.match(/Free trial · [^\n]*/) || [""])[0]);
  check("T.4 no technical words reach the customer (seat, entitlement, provisioning, integration)",
    !/entitlement|provisioning|integration|seat pool|launch_state/i.test(t), "");

  await press(page, new RegExp(`^Open Product: ${crm.title}$`));
  const entered = await enterProduct(page, crm);
  await shot(page, "3-in-easy-crm");
  result.data.trial_entry = entered;
  check("T.5 Open Product enters Easy CRM through WorkEasy360 sign-in - no product password", entered.where === "product",
    JSON.stringify(entered).slice(0, 600));
  const s = entered.session || {};
  check("T.6 Easy CRM's own session is Neha's (its protected API answers for her)", s.status === 200 && s.body.includes(neha.email),
    `${s.status} ${String(s.body).slice(0, 300)}`);

  // The Store remembers: the card now says Open Product.
  await page.goto(`${O.app}/store`, { waitUntil: "networkidle2" });
  const card = await waitFor(() => hasControl(page, new RegExp(`^Open Product: ${crm.title}$`)), { timeout: 60000 });
  await shot(page, "4-store-card-open");
  check("T.7 the Store's product card now offers Open Product", card, "");
  await audit(page, "T.7 Store home (signed in, a trial running)");
}

/** The same customer trials a second product from the Store's card. */
async function phaseTrialSecond(browser) {
  const neha = ctx.people.neha;
  const other = STORE.accounts;
  const { page } = await launch(browser);
  await signInTo(page, neha, "/store/products");
  const startLabel = new RegExp(`^Start ${other.trial_days}-day Free Trial: ${other.title}$`);
  const offered = await waitFor(() => hasControl(page, startLabel), { timeout: 60000 });
  check("S.1 a second product's card offers Start Free Trial", offered, "");
  await press(page, startLabel);
  const opened = await waitFor(() => hasControl(page, new RegExp(`^Open Product: ${other.title}$`)), { timeout: 90000 });
  await shot(page, "1-second-product-open");
  check("S.2 the second trial is ready to open, in the same workspace", opened, "");
  await press(page, new RegExp(`^Open Product: ${other.title}$`));
  const entered = await enterProduct(page, other);
  await shot(page, "2-in-second-product");
  check("S.3 Open Product enters the second product with no password", entered.where === "product", JSON.stringify(entered).slice(0, 400));
}

/** A brand-new customer buys: product page -> Buy Now -> checkout (workspace, billing) -> the provider's page ->
 * confirmation -> Open Product -> CRM. */
async function phasePaid(browser) {
  const omar = ctx.people.omar;
  const crm = STORE.crm;
  const { page } = await launch(browser);
  await signInTo(page, omar, `/store/product/${crm.slug}`);
  await waitText(page, "Buy Now");
  await press(page, /Buy Now/);
  await waitFor(() => pathOf(page).startsWith("/store/checkout"), { timeout: 60000 });
  await waitText(page, "Account & Organization");
  await shot(page, "1-checkout-no-organization");
  const noOrg = await text(page);
  check("P.1 the checkout never sends a customer with no organization to User Admin", !/Create one in User Admin/.test(noOrg) && /Create my workspace/.test(noOrg),
    noOrg.slice(0, 500));
  await press(page, "Create my workspace");
  const hasOrg = await waitFor(async () => /Current Organization/.test(await text(page)), { timeout: 60000 });
  check("P.2 one click creates the workspace (the buyer is its owner)", hasOrg, "");

  // Billing details, then the server's quote.
  await waitFor(async () => (await page.$$('input[autocomplete="organization"], input[type="email"]')).length > 0, { timeout: 60000 });
  const nameField = await page.$('input[autocomplete="organization"]');
  if (nameField) {
    await nameField.click({ clickCount: 3 });
    await nameField.type("Omar Traders");
  }
  await press(page, /Save Billing Details/);
  const priced = await waitFor(async () => /Total Amount/.test(await text(page)), { timeout: 90000 });
  check("P.3 billing saved and the server's quote shown", priced, "");
  await shot(page, "2-checkout-quoted");
  await press(page, /^Pay /);
  const atProvider = await waitFor(() => pathOf(page).startsWith("/billing/test-checkout"), { timeout: 90000 });
  check("P.4 Pay opens the payment provider's page (development test provider)", atProvider, page.url());
  await shot(page, "3-provider-page");
  await press(page, "Pay (succeed)");
  const done = await waitFor(async () => /Test payment succeeded|Payment successful/i.test(await text(page)), { timeout: 120000 });
  await shot(page, "4-confirmation");
  check("P.5 the verified payment is confirmed by the server", done, (await text(page)).slice(0, 400));

  // The product page: Open Product, with no seat to assign.
  await page.goto(`${O.app}/store/product/${crm.slug}`, { waitUntil: "networkidle2" });
  const open = await waitFor(() => hasControl(page, new RegExp(`^Open Product: ${crm.title}$`)), { timeout: 90000 });
  await shot(page, "5-product-page-open");
  check("P.6 after the purchase the product page says Open Product - the buyer was seated automatically", open, (await text(page)).slice(0, 400));
  await press(page, new RegExp(`^Open Product: ${crm.title}$`));
  const entered = await enterProduct(page, crm);
  await shot(page, "6-in-easy-crm");
  result.data.paid_entry = entered;
  const s = entered.session || {};
  check("P.7 Open Product enters Easy CRM as the buyer, no product password", entered.where === "product" && s.status === 200 && s.body.includes(omar.email),
    `${entered.where} ${s.status} ${String(s.body).slice(0, 300)}`);
}

/** Two tabs press Start Free Trial at the same moment. */
async function phaseConcurrent(browser) {
  const pia = ctx.people.pia;
  const crm = STORE.crm;
  const { context, page } = await launch(browser);
  await signInTo(page, pia, `/store/product/${crm.slug}`);
  const second = await context.newPage();
  watch(second);
  await second.setViewport({ width: 1280, height: 900 });
  await second.goto(`${O.app}/store/product/${crm.slug}`, { waitUntil: "networkidle2" });
  const startLabel = new RegExp(`^Start ${crm.trial_days}-day Free Trial: ${crm.title}$`);
  await waitFor(() => hasControl(page, startLabel));
  await waitFor(() => hasControl(second, startLabel));
  // Both buttons found first, then pressed in the same instant.
  const find = (p) =>
    p.waitForFunction(
      (src) => [...document.querySelectorAll("button")].find((b) => !b.disabled && new RegExp(src).test(b.getAttribute("aria-label") || "")) || null,
      { timeout: 60000 },
      startLabel.source,
    );
  const [one, two] = [await find(page), await find(second)];
  await Promise.all([one.evaluate((b) => b.click()), two.evaluate((b) => b.click())]);
  const both = await Promise.all(
    [page, second].map((p) => waitFor(() => hasControl(p, new RegExp(`^Open Product: ${crm.title}$`)), { timeout: 120000 })),
  );
  await shot(page, "1-tab-one");
  check("C.1 two simultaneous Start Free Trial clicks both end at Open Product (no error shown)", both.every(Boolean),
    JSON.stringify(both));
}

/** Existing customers are unchanged: Ravi's paid CRM opens; Priya (nothing bought) is offered the trial. */
async function phaseExisting(browser) {
  const crm = STORE.crm;
  const { page } = await launch(browser);
  // Ravi belongs to two organizations; the Store works for the one he picked (remembered per tab), and says so.
  await page.evaluateOnNewDocument((slug) => {
    try {
      window.sessionStorage.setItem("w360.store-org", slug);
    } catch {
      /* storage refused */
    }
  }, ctx.people.ravi.slug);
  await signInTo(page, ctx.people.ravi, `/store/product/${crm.slug}`);
  const named = await waitText(page, /For Acme Traders/, 60000);
  check("E.0 with several organizations, the product page names the one it is for", named, "");
  const open = await waitFor(() => hasControl(page, new RegExp(`^Open Product: ${crm.title}$`)), { timeout: 60000 });
  check("E.1 an existing customer's paid product shows Open Product, not a trial", open && !(await hasControl(page, /Free Trial:/)), "");
  await press(page, new RegExp(`^Open Product: ${crm.title}$`));
  const entered = await enterProduct(page, crm);
  check("E.2 and it still opens", entered.where === "product", JSON.stringify(entered).slice(0, 300));
  const other = await launch(browser);
  await signInTo(other.page, ctx.people.priya, `/store/product/${crm.slug}`);
  const offered = await waitFor(() => hasControl(other.page, /Free Trial:/), { timeout: 60000 });
  check("E.3 an existing organization that bought nothing is offered the trial", offered, "");
}

/** Super Admin: the trial policy and the default product roles, on the product's Availability tab. */
async function phaseAdmin(browser) {
  const { page } = await launch(browser);
  await page.goto(`${O.admin}/admin/products/${STORE.crm.catalog_id}?tab=availability`, { waitUntil: "networkidle2" });
  if (await page.$('input[type="email"]')) {
    await page.type('input[type="email"]', ctx.staff);
    await page.type('input[type="password"]', ctx.password);
    await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }).catch(() => null), page.click('button[type="submit"]')]);
    await page.goto(`${O.admin}/admin/products/${STORE.crm.catalog_id}?tab=availability`, { waitUntil: "networkidle2" });
  }
  const shown = await waitText(page, "Default product roles", 90000);
  await waitText(page, "Repeat trials", 60000);
  await shot(page, "1-availability");
  const t = await text(page);
  check("A.1 the Availability tab shows the trial policy (repeat, who, per person) and the default product roles",
    shown && /Once per organization/.test(t) && /Any organization/.test(t) && /platform default/.test(t), t.slice(0, 600));
  await audit(page, "A.1 Product Manager, Availability");
  await press(page, "Edit roles");
  await waitFor(async () => (await page.$('[role="dialog"]')) !== null);
  const member = await page.evaluateHandle(() => {
    const label = [...document.querySelectorAll('[role="dialog"] label')].find((l) => l.innerText.trim() === "Member");
    return label ? document.getElementById(label.htmlFor) : null;
  });
  await member.asElement().click({ clickCount: 3 });
  await member.asElement().type("sales_rep");
  await press(page, "Save roles");
  const saved = await waitText(page, "Default roles for", 30000);
  await shot(page, "2-roles-saved");
  check("A.2 Super Admin sets Easy CRM's member role to its own sales_rep", saved && /sales_rep/.test(await text(page)), "");
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: ctx.chrome,
    headless: true,
    timeout: 120000,
    protocolTimeout: 600000,
    defaultViewport: { width: 1280, height: 900 },
    args: [
      `--host-resolver-rules=${ctx.host_rules.join(", ")}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
      "--ignore-certificate-errors",
    ],
  });
  try {
    if (PHASE === "trial") await phaseTrial(browser);
    else if (PHASE === "trial-second") await phaseTrialSecond(browser);
    else if (PHASE === "paid") await phasePaid(browser);
    else if (PHASE === "concurrent") await phaseConcurrent(browser);
    else if (PHASE === "existing") await phaseExisting(browser);
    else if (PHASE === "admin") await phaseAdmin(browser);
    else throw new Error(`unknown phase ${PHASE}`);
  } catch (err) {
    check(`phase ${PHASE} completed without an error`, false, String(err && err.stack ? err.stack : err));
  } finally {
    save();
    await browser.close();
  }
  process.exit(result.checks.every((c) => c.ok) ? 0 : 1);
})();
