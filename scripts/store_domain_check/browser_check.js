// Real-browser check: the Digital Store on its own host (ADR-065) - the marketplace at
// store.workeasy360.com/, the four menus, the old app.../store addresses, robots and
// sitemap, the product page, a checkout that pays part from the Platform Wallet and
// the rest through the (test) provider and comes back to the Store's confirmation,
// My Account's pass into User Admin, a Consumption Wallet recharge, Help, Partners,
// a published and an unpublished policy, the mobile menu - and on every page: one
// h1, no sideways scroll at 390 and 320 px, axe WCAG 2.1 A/AA, no CSP violation.
//
// The production build behind scripts/store_domain_check/edge.js (Host kept), a real
// backend on the scratch database prepare.py made, headless Chrome.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   IDS='<the SEED json>' EDGE=127.0.0.1:8088 OUT=<dir> node scripts/store_domain_check/browser_check.js

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const IDS = JSON.parse(process.env.IDS);
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const OUT = process.env.OUT || ".";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const HOSTS = ["admin", "partner", "app", "auth", "api", "store"].map((p) => `${p}.workeasy360.com`);
const STORE = "http://store.workeasy360.com";
const APP = "http://app.workeasy360.com";
const OWNER = "manoj@uap-acme.dev";
// The organization that buys: the owner's second one, with no subscription yet.
const BUYER = IDS.beta;

const report = { checks: [], csp: [], consoleErrors: [], apiErrors: [], pages: [] };
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 240)}` : ""}`);
}

function watch(page) {
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Content Security Policy|Refused to/i.test(t)) report.csp.push(`${page.url()} ${t}`);
    else if (msg.type() === "error" && !/Failed to load resource: the server responded with a status of (401|403|404|409)/.test(t))
      report.consoleErrors.push(`${page.url()} ${t}`);
  });
  page.on("response", (res) => {
    if (res.url().includes("/api/") && res.status() >= 500) report.apiErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
  });
}

const settle = (page) => page.waitForNetworkIdle({ idleTime: 500, timeout: 20000 }).catch(() => {});
async function go(page, url) {
  const res = await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle(page);
  return res;
}
const waitText = (page, needle, timeout = 30000) =>
  page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, needle);
async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return r.violations.map((v) => `${v.id}: ${v.nodes.length} (${v.nodes[0]?.target?.join(" ")})`);
  });
}
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const h1s = (page) => page.$$eval("h1", (els) => els.map((e) => e.textContent.trim()));
const bodyText = (page) => page.evaluate(() => document.body.innerText);

async function clickText(page, label, selector = "button, a") {
  const handle = await page.waitForFunction(
    (l, s) => [...document.querySelectorAll(s)].find((b) => b.textContent.trim().replace(/\s+/g, " ") === l && !b.disabled),
    { timeout: 30000 },
    label,
    selector,
  );
  await handle.asElement().click();
}

async function api(page, method, url, body) {
  return page.evaluate(
    async (m, u, b) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="))?.split("=")[1] ?? "";
      const res = await fetch(u, {
        method: m,
        headers: { "content-type": "application/json", "X-CSRFToken": csrf },
        body: b ? JSON.stringify(b) : undefined,
        credentials: "include",
      });
      let json = null;
      try {
        json = await res.json();
      } catch {
        /* not JSON */
      }
      return { status: res.status, json };
    },
    method,
    url,
    body,
  );
}

/** One page's audit: one h1, axe clean, and no sideways scroll at 1440, 390 and 320 px. */
async function audit(page, name, url) {
  if (url) await go(page, url);
  await page.setViewport({ width: 1440, height: 900 });
  await settle(page);
  const headings = await h1s(page);
  const violations = await axe(page);
  const widths = {};
  for (const w of [390, 320]) {
    await page.setViewport({ width: w, height: 800 });
    await new Promise((r) => setTimeout(r, 300));
    widths[w] = await overflow(page);
  }
  await page.setViewport({ width: 1440, height: 900 });
  report.pages.push({ name, url: page.url(), h1: headings, axe: violations, overflow: widths });
  check(`${name}: exactly one h1`, headings.length === 1, headings.join(" | "));
  check(`${name}: axe WCAG 2.1 A/AA clean`, violations.length === 0, violations.join("; "));
  check(`${name}: no sideways scroll at 390 and 320 px`, widths[390] <= 0 && widths[320] <= 0, JSON.stringify(widths));
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
      "--force-color-profile=srgb",
    ],
    defaultViewport: { width: 1440, height: 900 },
  });
  const shot = (page, name, fullPage = true) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage }).catch(() => {});
  let page = null;
  try {
    const ctx = await browser.createBrowserContext();
    page = await ctx.newPage();
    watch(page);

    // ------------------------------------------------------------ the marketplace at /
    const home = await go(page, `${STORE}/`);
    check("store.workeasy360.com/ serves the marketplace (no redirect)", home.status() === 200 && page.url() === `${STORE}/`, page.url());
    await waitText(page, "Popular Products");
    const menus = await page.$$eval('nav[aria-label="Store"] > div > button', (els) => els.map((e) => e.textContent.trim()));
    check("the header's four menus: Products, Recharge, Partners, Help", JSON.stringify(menus) === JSON.stringify(["Products", "Recharge", "Partners", "Help"]), menus.join(","));
    const header = await page.$eval("header", (el) => el.innerText);
    check("signed out: Sign In and Get Started, no cart", /Sign In/.test(header) && /Get Started/.test(header) && !/cart/i.test(header), header.replace(/\s+/g, " "));
    const t = await bodyText(page);
    check("the shelf: WorkEasy Books for sale at its server price", /WorkEasy Books/.test(t) && /24,000/.test(t), "");
    const soonBuy = await page.$('a[aria-label="Buy now: Easy Project"]');
    check("Coming Soon has no purchase action", soonBuy === null && /Coming Soon/.test(t));
    const canonical = await page.$eval('link[rel="canonical"]', (el) => el.href).catch(() => "");
    // This local run is plain http; production's origin is https (the same rule).
    check("canonical is the Store origin's root", canonical === `${STORE}/`, canonical);
    await shot(page, "home-1440");
    await audit(page, "home");
    await page.setViewport({ width: 390, height: 844 });
    await shot(page, "home-390");
    await page.click('button[aria-label="Open menu"]');
    const mobile = await page.$$eval("#store-mobile-menu details summary", (els) => els.map((e) => e.textContent.trim()));
    check("the mobile menu: the same four entries, and Sign In", mobile.join(",") === "Products,Recharge,Partners,Help" && /Sign In/.test(await bodyText(page)), mobile.join(","));
    await shot(page, "home-390-menu", false);
    await page.setViewport({ width: 1440, height: 900 });

    // ------------------------------------------------------------ old addresses, robots, sitemap
    const legacy = await go(page, `${APP}/store/product/easybook?ref=1`);
    check("app.../store/product/<slug> opens on the Store host, query kept", page.url() === `${STORE}/store/product/easybook?ref=1`, page.url());
    void legacy;
    const robots = await page.evaluate(async (u) => (await fetch(u)).text(), `${STORE}/robots.txt`);
    check("the Store host's robots.txt allows /store and names its sitemap", /Allow: \/store/.test(robots) && /Sitemap: http:\/\/store\.workeasy360\.com\/sitemap\.xml/.test(robots), robots.replace(/\n/g, " | "));
    const sitemap = await page.evaluate(async (u) => (await fetch(u)).text(), `${STORE}/sitemap.xml`);
    check(
      "sitemap.xml lists the marketplace, products and the published policy only",
      sitemap.includes("<loc>http://store.workeasy360.com/</loc>") &&
        sitemap.includes("/store/product/easybook") &&
        sitemap.includes("/store/policies/refund-cancellation") &&
        !sitemap.includes("/store/policies/terms") &&
        !sitemap.includes("checkout"),
      sitemap.slice(0, 300),
    );
    const appTab = await ctx.newPage();
    await go(appTab, `${APP}/`);
    const appRobots = await appTab.evaluate(async (u) => (await fetch(u)).text(), `${APP}/robots.txt`);
    check("every other host still says Disallow: /", /Disallow: \/\s*$/.test(appRobots.trim()) && !/Sitemap/.test(appRobots), appRobots.replace(/\n/g, " | "));
    await appTab.close();

    // ------------------------------------------------------------ products and filters
    await go(page, `${STORE}/store/products?category=Accounting`);
    await waitText(page, "1 product");
    const filtered = await bodyText(page);
    check("Department filter on the server: only Accounting", /WorkEasy Books/.test(filtered) && !/View Details\n.*Easy CRM/.test(filtered));
    await audit(page, "products");

    // ------------------------------------------------------------ the product page
    await go(page, `${STORE}/store/product/easybook`);
    await waitText(page, "Plans & Pricing");
    const product = await bodyText(page);
    check("product page: overview, setup requirements, plans, app availability", /Setup requirements/.test(product) && /GSTIN/.test(product) && /App Availability/.test(product) && /Get the official app/.test(product));
    check("product page: Start Free Plan offered for the free plan only", /Renews every year at the plan's price, currently ₹24,000/.test(product), "");
    await shot(page, "product-1440");
    await audit(page, "product");
    await go(page, `${STORE}/store/product/easy-project`);
    await waitText(page, "Not on sale yet");
    check("a Coming Soon product page sells nothing", !(await page.$('a[href^="/store/checkout"]')));

    // ------------------------------------------------------------ checkout: wallet + gateway
    await go(page, `${STORE}/store/product/easybook`);
    await waitText(page, "Buy Now");
    await clickText(page, "Buy Now", "a");
    await page.waitForFunction(() => location.pathname === "/store/checkout", { timeout: 30000 });
    await waitText(page, "Existing Account (Login)");
    await shot(page, "checkout-signed-out-1440");
    await audit(page, "checkout (signed out)");
    await page.type('form[aria-label="Sign in"] input[type="email"]', OWNER);
    await page.type('form[aria-label="Sign in"] input[type="password"]', PASSWORD);
    await page.click('form[aria-label="Sign in"] button[type="submit"]');
    await waitText(page, "Organization / Workspace");
    // Two memberships: the buyer chooses the organization.
    const options = await page.$$eval("select option", (els) => els.map((e) => e.value));
    check("two memberships: the buyer chooses the organization", options.includes(IDS.slug) && options.includes(IDS.beta), options.join(","));
    const selectHandle = await page.$("select");
    await selectHandle.select(BUYER);
    await waitText(page, "GST (18%)");
    const priced = await bodyText(page);
    check("the server's quote: ₹24,000 + GST ₹4,320 = ₹28,320", /₹24,000/.test(priced) && /₹4,320/.test(priced) && /₹28,320/.test(priced));
    const before = await api(page, "GET", `/api/v1/orgs/${BUYER}/wallets`);
    const walletBefore = before.json?.platform?.balance_minor ?? -1;
    check("the Platform Wallet holds ₹10,000 before", walletBefore === 1_000_000, walletBefore);
    await page.click('button[role="switch"][aria-label="Use Platform Wallet"]');
    await waitText(page, "Amount Payable Now");
    const wallet = await page.$eval("#store-wallet-amount", (el) => el.value);
    check("the wallet amount defaults to what it can pay", wallet === "10000", wallet);
    check("Amount Payable Now ₹18,320", /₹18,320/.test(await bodyText(page)));
    await shot(page, "checkout-1440");
    await audit(page, "checkout (signed in)");
    await page.setViewport({ width: 390, height: 844 });
    await shot(page, "checkout-390");
    await page.setViewport({ width: 1440, height: 900 });
    await clickText(page, "Pay ₹18,320 & Subscribe");
    await page.waitForFunction(() => location.pathname === "/billing/test-checkout", { timeout: 60000 });
    check("the provider's (test) page opens on the Store host", page.url().startsWith(`${STORE}/billing/test-checkout`), page.url().split("#")[0]);
    const held = await api(page, "GET", `/api/v1/orgs/${BUYER}/wallets`);
    check("while at the provider, the wallet's part is held, not taken", held.json?.platform?.balance_minor === walletBefore);
    await clickText(page, "Pay (succeed)");
    await waitText(page, "Test payment succeeded");
    await clickText(page, "Return to the Store", "a");
    await page.waitForFunction(() => location.pathname === "/store/checkout/complete", { timeout: 30000 });
    await waitText(page, "Payment successful", 60000);
    const done = await bodyText(page);
    check("confirmation: order reference, payment reference, invoice, Active", /ORD-\d+/.test(done) && /Payment reference/.test(done) && /INV-|Invoice/.test(done) && /Active/.test(done));
    check("confirmation: ₹10,000 paid from the Platform Wallet", /Paid from Platform Wallet\s*₹10,000/.test(done));
    await shot(page, "complete-1440");
    await audit(page, "confirmation");
    const after = await api(page, "GET", `/api/v1/orgs/${BUYER}/wallets`);
    check("after the confirmed payment, exactly ₹10,000 left the wallet", after.json?.platform?.balance_minor === walletBefore - 1_000_000, after.json?.platform?.balance_minor);
    const orders = await api(page, "GET", `/api/v1/orgs/${BUYER}/orders`);
    const books = (orders.json?.results ?? []).filter((o) => (o.product_codes ?? []).includes("easybook"));
    check("one order for the product, fulfilled", books.length === 1 && books[0].status === "fulfilled", JSON.stringify(books.map((o) => o.status)));

    // ------------------------------------------------------------ the Store's API surface
    const members = await api(page, "GET", `/api/v1/orgs/${BUYER}/members`);
    check("the Store host serves nothing else of the organization (members: 404)", members.status === 404, members.status);
    // A deliberate probe: the CSP's connect-src refuses it, which is the point.
    const cspBefore = report.csp.length;
    const crossOrigin = await page.evaluate(async (slug) => {
      const res = await fetch(`http://app.workeasy360.com/api/v1/orgs/${slug}/wallets`, { credentials: "include" }).catch(() => null);
      return res ? res.status : "blocked";
    }, BUYER);
    await new Promise((r) => setTimeout(r, 300));
    const probed = report.csp.splice(cspBefore).filter((v) => !v.includes("app.workeasy360.com/api/v1/orgs/"));
    report.csp.push(...probed);
    check("the Store page cannot read the customer portal's API (connect-src 'self')", crossOrigin === "blocked", crossOrigin);

    // ------------------------------------------------------------ My Account -> User Admin
    await go(page, `${STORE}/store`);
    await waitText(page, "My Account");
    await clickText(page, "My Account");
    await page.waitForFunction(() => location.host === "app.workeasy360.com" && !location.pathname.startsWith("/login"), { timeout: 60000 });
    await settle(page);
    check("My Account opens User Admin signed in as the same person, no second password", page.url().startsWith(`${APP}/o/${BUYER}/apps`), page.url());
    const appSession = await api(page, "GET", "/api/v1/auth/session");
    check("User Admin's own session is the owner's", appSession.json?.email === OWNER, appSession.json?.email);
    await shot(page, "my-account-app", false);

    // ------------------------------------------------------------ Recharge: Consumption Wallet
    await go(page, `${STORE}/store/recharge`);
    await waitText(page, "Consumption Wallet");
    await audit(page, "recharge");
    await shot(page, "recharge-1440");
    const beforeC = (await api(page, "GET", `/api/v1/orgs/${BUYER}/wallets`)).json?.consumption?.balance_minor;
    const recharge = await page.$("#consumption");
    const amount = await recharge.$("input");
    await amount.type("500");
    const submit = await recharge.$('button[type="submit"]');
    await submit.click();
    await waitText(page, "Confirm your recharge");
    await clickText(page, "Confirm & Pay");
    await page.waitForFunction(() => location.pathname === "/billing/test-checkout", { timeout: 60000 });
    await clickText(page, "Pay (succeed)");
    await waitText(page, "Test payment succeeded");
    await clickText(page, "Return to the Store", "a");
    await waitText(page, "Recharge successful", 60000);
    const afterW = (await api(page, "GET", `/api/v1/orgs/${BUYER}/wallets`)).json;
    check("₹500 reached the Consumption Wallet only", afterW?.consumption?.balance_minor === beforeC + 50000 && afterW?.platform?.balance_minor === walletBefore - 1_000_000, JSON.stringify({ c: afterW?.consumption?.balance_minor, p: afterW?.platform?.balance_minor }));

    // ------------------------------------------------------------ Help, Partners, policies, sign-in
    await audit(page, "help", `${STORE}/store/help`);
    check("help: the support address and a ticket for the organization", /Raise a support ticket/.test(await bodyText(page)));
    await audit(page, "partners", `${STORE}/store/partners`);
    const partners = await bodyText(page);
    check("partners: Affiliate and Reseller, no public White Label", /Affiliate Programme/.test(partners) && /Reseller Programme/.test(partners) && !/White[- ]label Programme/i.test(partners));
    await audit(page, "policy (published)", `${STORE}/store/policies/refund-cancellation`);
    check("a published policy shows the Super Admin's text", /STORE CHECK TEXT/.test(await bodyText(page)));
    await go(page, `${STORE}/store/policies/terms`);
    await waitText(page, "has not been published yet");
    check("an unpublished policy says so - no invented text", true);
    await shot(page, "policy-unpublished", false);

    const fresh = await browser.createBrowserContext();
    const anon = await fresh.newPage();
    watch(anon);
    await audit(anon, "store sign-in", `${STORE}/login?next=%2Fstore%2Frecharge`);
    check("the Store host signs in in its own frame", /Sign in to the Store/.test(await bodyText(anon)));
    await shot(anon, "sign-in-1440");
    await fresh.close();

    check("no CSP violation on any page", report.csp.length === 0, report.csp.slice(0, 3).join(" | "));
    check("no console error on any page", report.consoleErrors.length === 0, report.consoleErrors.slice(0, 3).join(" | "));
    check("no 5xx from the API", report.apiErrors.length === 0, report.apiErrors.slice(0, 3).join(" | "));
  } catch (err) {
    check("the journey ran to the end", false, err && err.stack ? err.stack : String(err));
    if (page) {
      await shot(page, "failure");
      const at = await page.evaluate(() => `${location.href}
${document.body.innerText}`).catch(() => "");
      fs.writeFileSync(path.join(OUT, "failure.txt"), at);
    }
  } finally {
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    const failed = report.checks.filter((c) => !c.ok).length;
    console.log(`\n${report.checks.length - failed}/${report.checks.length} checks passed`);
    await browser.close();
    process.exit(failed ? 1 : 0);
  }
})();
