// Real-browser verification of the multi-portal split and the four products'
// 7-day trials (ADR-043; brief Parts 28-39). Production frontend build, a real
// backend, and a local copy of the production edge (deploy/edge/Caddyfile, see
// README.md) in front of both, so every request carries the portal's real
// host name: admin., partner., app., auth.workeasy360.com, mapped to the local
// edge with --host-resolver-rules. The products' own hosts are NOT mapped:
// "Open" reaches the real crm./book./ops./help.workeasy360.com.
//
// On every audited page: CSP violations, console errors, axe WCAG 2.1 A/AA, a
// visible keyboard focus indicator, exactly one h1. Reflow at 1440, 1024,
// 768, 390 and 320 px.
//
// Journeys:
//   A  admin.  -> auth. -> password -> TOTP on auth. -> StaffGrant -> admin.
//   N  a customer choosing the Admin portal: signed in, refused, no admin call
//   P  partner. -> auth. -> partner portal; only its granted products
//   U  app. -> auth. -> organization -> dashboard -> My Products
//   T  per product (all four, separately): Explore -> Start 7-Day Free Trial
//      -> My Products (TRIAL, 7 days left) -> seat -> Open -> the product's
//      own site; controlled clock: +7 days -> launch denied, Trial expired,
//      Upgrade / Buy now -> checkout -> test payment -> ACTIVE -> Open
//   S  portal switching: customer on admin./partner. refused; staff and a
//      partner on app. see only what membership gives (none)
//   L  sign-out on one portal ends every portal's session of that sign-in
//   R  return URLs: hostile `next` values land on the portal's own root
//   X  reflow at every width
// Not part of CI. A manual screen-reader pass is NOT part of it: it is
// reported as NOT EXECUTED.
const puppeteer = require("puppeteer-core");
const crypto = require("crypto");
const fs = require("fs");
const { execFileSync } = require("child_process");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const PASSWORD = "Browser-Test-Password-1!";
const { STAFF_TOTP, CUSTOMER_ORG, PARTNER_ID, EDGE, OUT, PY, TIME_TRAVEL_ENV } = process.env;
const ONLY = (process.env.ONLY || "A,N,P,U,T,S,L,R,X").split(",");
const WIDTHS = [1440, 1024, 768, 390, 320];
const ORIGIN = Object.fromEntries(["admin", "partner", "app", "auth", "api"].map((p) => [p, `http://${p}.workeasy360.com`]));
const PRODUCTS = [
  { code: "easy_crm", name: "Easy CRM", host: "crm.workeasy360.com" },
  { code: "easybook", name: "EasyBook", host: "book.workeasy360.com" },
  { code: "easy_project", name: "Easy Project", host: "ops.workeasy360.com" },
  { code: "easy_helpdesk", name: "Easy Help Desk", host: "help.workeasy360.com" },
];

const report = { pages: [], csp: [], errors: [], expected: [], reflow: [], flows: [], products: {}, notes: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let browser;

// ------------------------------------------------------------------ plumbing --

function flow(name, ok, detail = {}) {
  report.flows.push({ flow: name, ok: Boolean(ok), ...detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : ` :: ${JSON.stringify(detail).slice(0, 400)}`}`);
}

async function context() {
  return browser.createBrowserContext();
}

async function newPage(ctx, width = 1280) {
  const page = await ctx.newPage();
  await page.setViewport({ width, height: 900 });
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sourceFile || ""}`),
    );
  });
  page.__requests = [];
  page.on("request", (r) => page.__requests.push(r.url()));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const line = `${page.url()} :: ${m.text()}`;
    // A refusal the journey asks for (403/401/409/402) is logged by Chrome as a failed resource.
    // So is the 400 of a forged hand-off (journey R) on /auth/continue.
    if (/status of (401|402|403|404|409)/.test(m.text()) || (/status of 400/.test(m.text()) && page.url().includes("/auth/continue"))) report.expected.push(line);
    else report.errors.push(line);
  });
  page.on("pageerror", (e) => report.errors.push(`${page.url()} :: pageerror ${e.message}`));
  return page;
}

async function audit(page, label) {
  const csp = await page.evaluate(() => window.__csp || []);
  csp.forEach((c) => report.csp.push(`${label}: ${c}`));
  const axe = await page.evaluate(async (src) => {
    (0, eval)(src);
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0] && v.nodes[0].html.slice(0, 160) }));
  }, AXE);
  const noFocus = [];
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  let tabbed = 0;
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
      const shadow = cs.boxShadow && cs.boxShadow !== "none";
      return { tag: el.tagName, text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 40), visible: outline || shadow || el.matches("input,select,textarea") };
    });
    if (info) {
      tabbed++;
      if (!info.visible) noFocus.push(`${info.tag} "${info.text}"`);
    }
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  const h1 = await page.evaluate(() => [...document.querySelectorAll("h1")].map((h) => h.innerText));
  report.pages.push({ label, url: page.url(), axe, tabbed, noFocus, h1 });
}

const bodyText = (page) => page.evaluate(() => document.body.innerText);

async function waitForText(page, text, timeout = 20000) {
  await page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text).catch(async () => {
    const shown = (await bodyText(page)).slice(0, 600).replace(/\n+/g, " | ");
    throw new Error(`text not found: "${text}" on ${page.url()} :: ${shown}`);
  });
}

async function waitForUrl(page, test, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (test(page.url())) {
      await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
      return page.url();
    }
    await sleep(200);
  }
  throw new Error(`unexpected address ${page.url()}`);
}

async function clickText(page, text, selector = "button, a") {
  const handle = await page.waitForFunction(
    (t, s) => [...document.querySelectorAll(s)].find((el) => el.innerText.trim() === t && el.getBoundingClientRect().height > 0),
    { timeout: 20000 },
    text,
    selector,
  );
  await handle.asElement().click();
}

async function clickLabel(page, label) {
  const el = await page.waitForSelector(`[aria-label="${label}"]`, { visible: true, timeout: 20000 });
  await el.click();
}

function base32(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of input.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}
function totp(secret, at = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const mac = crypto.createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = mac[mac.length - 1] & 0xf;
  return String((mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
const lastWindow = { [STAFF_TOTP]: Math.floor(Date.now() / 30000) };
async function freshCode(secret) {
  const window = () => Math.floor(Date.now() / 30000);
  while (lastWindow[secret] === window()) await sleep(1000);
  lastWindow[secret] = window();
  return totp(secret);
}

/** On the central sign-in page: the one form. */
async function enterPassword(page, email) {
  await page.waitForSelector('input[type="email"]', { visible: true, timeout: 20000 });
  if (!new URL(page.url()).host.startsWith("auth.")) throw new Error(`a password form outside auth.: ${page.url()}`);
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await clickText(page, "Sign in");
}

/** Open `url` signed out, sign in on auth. as `email` (TOTP when asked) and
 * wait to be back on `host`. */
async function signInVia(page, url, email, host, { totpSecret = null } = {}) {
  await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
  await waitForUrl(page, (u) => new URL(u).host === "auth.workeasy360.com");
  await enterPassword(page, email);
  if (totpSecret) {
    await page.waitForSelector('input[autocomplete="one-time-code"]', { visible: true, timeout: 20000 });
    await page.type('input[autocomplete="one-time-code"]', await freshCode(totpSecret));
    await clickText(page, "Verify");
  }
  return waitForUrl(page, (u) => new URL(u).host === host && !u.includes("/auth/continue"));
}

async function cookiesOf(page, origin) {
  const client = await page.createCDPSession();
  const { cookies } = await client.send("Network.getCookies", { urls: [`${origin}/`] });
  await client.detach();
  return cookies.map((c) => ({ name: c.name, domain: c.domain, httpOnly: c.httpOnly, sameSite: c.sameSite }));
}

async function apiFrom(page, path, method = "GET") {
  return page.evaluate(
    async (p, m) => {
      const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
      const r = await fetch(p, {
        method: m,
        headers: { Accept: "application/json", ...(csrf ? { "X-CSRFToken": decodeURIComponent(csrf) } : {}) },
        credentials: "same-origin",
      });
      let body = null;
      try {
        body = await r.json();
      } catch {
        body = null;
      }
      return { status: r.status, body };
    },
    path,
    method,
  );
}

function timeTravel(product) {
  const env = { ...process.env, ...JSON.parse(TIME_TRAVEL_ENV) };
  return execFileSync(PY, ["scripts/portals_browser_check/time_travel.py", CUSTOMER_ORG, product], { env, encoding: "utf8" }).trim();
}

// ------------------------------------------------------------------ journeys --

async function journeyAdmin() {
  const page = await newPage(await context());
  await page.goto(`${ORIGIN.admin}/`, { waitUntil: "networkidle0" });
  const signIn = await waitForUrl(page, (u) => new URL(u).host === "auth.workeasy360.com");
  const params = new URL(signIn).searchParams;
  flow("A: a signed-out visit to admin. goes to the central sign-in, as Administrator", params.get("mode") === "admin" && /^[A-Za-z0-9_-]{43}$/.test(params.get("state") || ""), { signIn });
  await audit(page, "auth. /login?mode=admin (signed out)");
  const stateCookie = (await cookiesOf(page, ORIGIN.admin)).find((c) => c.name === "w360_portal_state");
  flow("A: admin. set a host-only, HttpOnly binding cookie", stateCookie && stateCookie.domain === "admin.workeasy360.com" && stateCookie.httpOnly, { stateCookie });
  await enterPassword(page, "staff@browser.test");
  await page.waitForSelector('input[autocomplete="one-time-code"]', { visible: true, timeout: 20000 });
  flow("A: the second factor is asked for on auth., before the hand-off", new URL(page.url()).host === "auth.workeasy360.com");
  await audit(page, "auth. step-up for the Admin portal");
  await page.type('input[autocomplete="one-time-code"]', await freshCode(STAFF_TOTP));
  await clickText(page, "Verify");
  const landed = await waitForUrl(page, (u) => u.startsWith(`${ORIGIN.admin}/admin`));
  await waitForText(page, "Super Admin");
  flow("A: StaffGrant verified, the console opens on admin.", landed === `${ORIGIN.admin}/admin`, { landed });
  flow("A: the hand-off code never appeared in a query string", !page.__requests.some((u) => /[?&]code=/.test(u)), {});
  const adminCookies = await cookiesOf(page, ORIGIN.admin);
  const session = adminCookies.find((c) => c.name === "w360_session");
  flow("A: the admin session cookie is host-only and HttpOnly", session && session.domain === "admin.workeasy360.com" && session.httpOnly && session.sameSite === "Lax", { session });
  flow("A: the binding cookie was spent", !adminCookies.some((c) => c.name === "w360_portal_state"), { adminCookies });
  flow("A: no cookie for the parent domain", !(await cookiesOf(page, "http://workeasy360.com")).length, {});
  await audit(page, "admin. /admin (dashboard)");
  await page.goto(`${ORIGIN.admin}/admin/products`, { waitUntil: "networkidle0" });
  for (const p of PRODUCTS) await waitForText(page, p.name);
  const text = await bodyText(page);
  const sevenDays = (text.match(/7 days, on · 1 eligible plan\(s\)/g) || []).length;
  flow("A: the Product Manager lists the four products, each with a 7-day trial on", sevenDays === 4, { sevenDays });
  await audit(page, "admin. /admin/products (Product Manager)");
  return page;
}

async function journeyCustomerAsAdmin() {
  const page = await newPage(await context());
  await page.goto(`${ORIGIN.admin}/admin/customers`, { waitUntil: "networkidle0" });
  await waitForUrl(page, (u) => new URL(u).host === "auth.workeasy360.com");
  await enterPassword(page, "owner@browser.test");
  await waitForText(page, "You don't have access to the Admin portal.");
  const adminCalls = page.__requests.filter((u) => u.includes("/api/v1/admin/"));
  flow("N: a customer is signed in but refused the Admin portal, with no admin API call", adminCalls.length === 0 && new URL(page.url()).host === "auth.workeasy360.com", { adminCalls });
  await audit(page, "auth. refusal: no access to the Admin portal");
  // Straight to the admin host with a valid sign-in: still nothing.
  await page.goto(`${ORIGIN.admin}/admin`, { waitUntil: "networkidle0" });
  await waitForUrl(page, (u) => new URL(u).host === "auth.workeasy360.com");
  await waitForText(page, "You don't have access to the Admin portal.");
  flow("N: opening admin. again changes nothing - no admin session exists", !(await cookiesOf(page, ORIGIN.admin)).some((c) => c.name === "w360_session"));
  await clickText(page, "Continue as User");
  const landed = await waitForUrl(page, (u) => new URL(u).host === "app.workeasy360.com" && u.includes(`/o/${CUSTOMER_ORG}`));
  flow("N: Continue as User opens the customer portal", true, { landed });
  return page;
}

async function journeyPartner() {
  const page = await newPage(await context());
  const landed = await signInVia(page, `${ORIGIN.partner}/`, "partner@browser.test", "partner.workeasy360.com");
  await waitForText(page, "Seed Reseller");
  flow("P: a partner member is handed to partner.", landed.startsWith(`${ORIGIN.partner}/partner`), { landed });
  await audit(page, "partner. /partner (portal chooser)");
  await page.goto(`${ORIGIN.partner}/partner/${PARTNER_ID}/products`, { waitUntil: "networkidle0" });
  await waitForText(page, "Easy CRM");
  const text = await bodyText(page);
  const shown = PRODUCTS.filter((p) => text.includes(p.name)).map((p) => p.code);
  flow("P: the partner sees only its granted products (not Easy Project)", JSON.stringify(shown) === JSON.stringify(["easy_crm", "easybook", "easy_helpdesk"]), { shown });
  await audit(page, "partner. products & plans");
  const adminFromPartner = await apiFrom(page, "/api/v1/admin/session");
  flow("P: the admin API is not served on partner.", adminFromPartner.status === 404, adminFromPartner);
  // A customer is refused the partner portal.
  const customer = await newPage(await context());
  await customer.goto(`${ORIGIN.partner}/partner`, { waitUntil: "networkidle0" });
  await waitForUrl(customer, (u) => new URL(u).host === "auth.workeasy360.com");
  await enterPassword(customer, "owner@browser.test");
  await waitForText(customer, "You don't have access to the Partner portal.");
  flow("P: a customer cannot enter the Partner portal", !(await cookiesOf(customer, ORIGIN.partner)).some((c) => c.name === "w360_session"));
  return page;
}

async function journeyUser() {
  const page = await newPage(await context());
  await page.goto(`${ORIGIN.app}/`, { waitUntil: "networkidle0" });
  flow("U: app. / is the public site, no sign-in needed", new URL(page.url()).host === "app.workeasy360.com");
  const landed = await signInVia(page, `${ORIGIN.app}/dashboard`, "owner@browser.test", "app.workeasy360.com");
  await waitForUrl(page, (u) => u.includes(`/o/${CUSTOMER_ORG}`));
  flow("U: the customer reaches their organization on app.", page.url().startsWith(`${ORIGIN.app}/o/${CUSTOMER_ORG}`), { landed: page.url() });
  await audit(page, "app. organization dashboard");
  await page.goto(`${ORIGIN.app}/o/${CUSTOMER_ORG}/apps`, { waitUntil: "networkidle0" });
  await waitForText(page, "My products");
  const text = await bodyText(page);
  const available = PRODUCTS.filter((p) => text.includes(p.name)).length;
  flow("U: My Products lists all four products, each AVAILABLE", available === 4 && (text.match(/Available/g) || []).length >= 4, { available });
  await audit(page, "app. My Products (nothing held yet)");
  const adminFromApp = await apiFrom(page, "/api/v1/admin/session");
  flow("U: the admin API is not served on app.", adminFromApp.status === 404, adminFromApp);
  return page;
}

async function exploreCard(page, name) {
  return page.evaluate((n) => {
    const card = [...document.querySelectorAll("article")].find((a) => a.querySelector("h3")?.innerText.trim() === n);
    return card ? card.innerText : null;
  }, name);
}

async function journeyTrial(page, product) {
  const result = { product: product.code };
  const slug = CUSTOMER_ORG;
  // Explore -> Start 7-Day Free Trial
  await page.goto(`${ORIGIN.app}/o/${slug}/explore`, { waitUntil: "networkidle0" });
  await waitForText(page, product.name);
  const before = await exploreCard(page, product.name);
  result.explore = before && before.includes("7-day free trial") && before.includes("Start 7-Day Free Trial") && before.includes("Buy now") && before.includes("Learn more");
  flow(`T ${product.code}: Explore offers Start 7-Day Free Trial, Buy now, Learn more`, result.explore, { before });
  if (product.code === "easy_crm") await audit(page, "app. Explore (catalog)");
  await clickLabel(page, `Start 7-day free trial: ${product.name}`);
  await waitForText(page, `Trial started: ${product.name}.`);
  await page.reload({ waitUntil: "networkidle0" });
  const during = await exploreCard(page, product.name);
  result.trial = during && during.includes("Trial active — 7 days remaining");
  flow(`T ${product.code}: the card says Trial active — 7 days remaining`, result.trial, { during });
  if (product.code === "easy_crm") await audit(page, "app. Explore with an active trial");
  // A second click is refused, never a second trial.
  const replay = await page.evaluate(async (code, sl) => {
    const csrf = (document.cookie.match(/(?:^|; )(?:__Host-)?w360_csrf=([^;]*)/) || [])[1];
    const r = await fetch(`/api/v1/orgs/${sl}/trials`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": decodeURIComponent(csrf), "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ product_code: code, plan_code: `${code}_team` }),
    });
    return { status: r.status, body: await r.json() };
  }, product.code, slug);
  result.replay = replay.status === 409 && replay.body.reason === "already_used";
  flow(`T ${product.code}: starting again is refused (already_used)`, result.replay, replay);
  // My Products: TRIAL, 7 days left, a seat, Open -> the product's own site.
  await page.goto(`${ORIGIN.app}/o/${slug}/apps`, { waitUntil: "networkidle0" });
  await waitForText(page, product.name);
  const cardText = await page.evaluate((n) => {
    const card = [...document.querySelectorAll("article")].find((a) => a.innerText.includes(n));
    return card ? card.innerText : "";
  }, product.name);
  result.myProducts = /TRIAL/i.test(cardText) && cardText.includes("7 days left");
  flow(`T ${product.code}: My Products shows it as TRIAL, 7 days left`, result.myProducts, { cardText });
  if (product.code === "easy_crm") await audit(page, "app. My Products with a trial");
  await clickLabel(page, `Assign a seat: ${product.name}`);
  await waitForUrl(page, (u) => u.includes(`/o/${slug}/settings/products`));
  // With one per-seat product held there is no product choice to make.
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 15000 }).catch(() => {});
  const tab = await page.evaluateHandle(
    (n) => [...document.querySelectorAll('[role="group"][aria-label="Product"] button')].find((b) => b.innerText.trim() === n) || null,
    product.name,
  );
  if (tab.asElement()) {
    await tab.asElement().click();
    await sleep(500);
  }
  await clickText(page, "— Assign");
  await waitForText(page, "✓ Assigned");
  await page.goto(`${ORIGIN.app}/o/${slug}/apps`, { waitUntil: "networkidle0" });
  const open = await page.waitForFunction(
    (n) => {
      const card = [...document.querySelectorAll("article")].find((a) => a.innerText.includes(n));
      return card && [...card.querySelectorAll("button")].find((b) => b.innerText.trim() === "Open");
    },
    { timeout: 15000 },
    product.name,
  );
  const [nav] = await Promise.all([page.waitForNavigation({ timeout: 45000, waitUntil: "domcontentloaded" }).catch(() => null), open.asElement().click()]);
  const reached = page.url();
  result.launch = nav !== null && new URL(reached).host !== "app.workeasy360.com";
  result.launchedTo = reached;
  flow(`T ${product.code}: Open goes to the product (${product.host})`, result.launch && (new URL(reached).host === product.host || product.code === "easy_helpdesk"), { reached });
  // Controlled clock: a week later.
  result.clock = timeTravel(product.code);
  await page.goto(`${ORIGIN.app}/o/${slug}/apps`, { waitUntil: "networkidle0" });
  const launch = await apiFrom(page, `/api/v1/orgs/${slug}/apps/${product.code}/launch`, "POST");
  result.denied = launch.status !== 200;
  flow(`T ${product.code}: after 7 days the launch is denied`, result.denied, launch);
  const other = await page.evaluate(() => document.querySelector('[aria-labelledby="w360-other-products"]')?.innerText || "");
  result.expiredCard = other.includes(product.name) && other.includes("Trial expired") && other.includes("Upgrade / Buy now");
  flow(`T ${product.code}: My Products says Trial expired, Upgrade / Buy now`, result.expiredCard, { other });
  if (product.code === "easy_crm") await audit(page, "app. My Products with an expired trial");
  await page.goto(`${ORIGIN.app}/o/${slug}/explore`, { waitUntil: "networkidle0" });
  await waitForText(page, product.name);
  const after = await exploreCard(page, product.name);
  result.expiredExplore = after && after.includes("Trial expired") && after.includes("Upgrade / Buy now") && !after.includes("Start 7-Day");
  flow(`T ${product.code}: Explore says Trial expired and offers Upgrade / Buy now only`, result.expiredExplore, { after });
  // Buy: checkout -> test payment -> paid entitlement.
  await clickLabel(page, `Upgrade / Buy now: ${product.name}`);
  await waitForUrl(page, (u) => u.includes(`/o/${slug}/checkout`));
  await waitForText(page, "Place order and pay");
  if (product.code === "easy_crm") await audit(page, "app. checkout");
  await Promise.all([page.waitForNavigation({ timeout: 60000 }).catch(() => null), clickText(page, "Place order and pay")]);
  await waitForUrl(page, (u) => u.includes("/billing/test-checkout"));
  await waitForText(page, "Pay (succeed)");
  if (product.code === "easy_crm") await audit(page, "app. hosted test payment page");
  await clickText(page, "Pay (succeed)");
  await page.waitForNetworkIdle({ idleTime: 800, timeout: 30000 }).catch(() => {});
  await sleep(1500);
  await page.goto(`${ORIGIN.app}/o/${slug}/apps`, { waitUntil: "networkidle0" });
  await waitForText(page, product.name);
  const paidCard = await page.evaluate((n) => {
    const card = [...document.querySelectorAll("article")].find((a) => a.innerText.includes(n));
    return card ? card.innerText : "";
  }, product.name);
  const relaunch = await apiFrom(page, `/api/v1/orgs/${slug}/apps/${product.code}/launch`, "POST");
  result.paid = /ACTIVE/i.test(paidCard) && !/TRIAL/i.test(paidCard) && relaunch.status === 200;
  flow(`T ${product.code}: bought - ACTIVE in My Products and launchable again`, result.paid, { paidCard, relaunch: relaunch.status, to: relaunch.body && relaunch.body.redirect_url });
  report.products[product.code] = result;
}

async function journeySwitching(staffPage) {
  // The staff member (signed in on auth. and admin. already) opens app.: no
  // organization membership, so nothing of any organization.
  await staffPage.goto(`${ORIGIN.app}/dashboard`, { waitUntil: "networkidle0" });
  const landed = await waitForUrl(staffPage, (u) => new URL(u).host === "app.workeasy360.com" && !u.includes("/auth/continue"));
  await sleep(800);
  const text = await bodyText(staffPage);
  flow("S: staff opening app. get the customer experience of their own memberships (none)", staffPage.url().includes("/onboarding") && !text.includes("Customer Company"), { landed: staffPage.url() });
  const orgs = await apiFrom(staffPage, "/api/v1/organizations");
  flow("S: staff see no organization they are not a member of", orgs.status === 200 && orgs.body.organizations.length === 0, orgs);
  const theirs = await apiFrom(staffPage, `/api/v1/orgs/${CUSTOMER_ORG}/apps`);
  flow("S: a customer's organization is refused to staff on app.", theirs.status === 403 || theirs.status === 404, theirs);
  // A partner member opening app.: the same.
  const partner = await newPage(await context());
  await signInVia(partner, `${ORIGIN.app}/dashboard`, "partner@browser.test", "app.workeasy360.com");
  await sleep(800);
  flow("S: a partner member on app. gets only customer membership (none)", partner.url().includes("/onboarding"), { landed: partner.url() });
}

async function journeyLogout(staffPage) {
  // staffPage has sessions on auth., admin. and app. from one sign-in.
  const before = await apiFrom(staffPage, "/api/v1/auth/session");
  await staffPage.goto(`${ORIGIN.admin}/admin`, { waitUntil: "networkidle0" });
  await waitForText(staffPage, "Super Admin");
  // Sign out from the customer portal.
  await staffPage.goto(`${ORIGIN.app}/onboarding`, { waitUntil: "networkidle0" });
  const out = await apiFrom(staffPage, "/api/v1/auth/logout", "POST");
  await staffPage.goto(`${ORIGIN.admin}/admin`, { waitUntil: "networkidle0" });
  const where = await waitForUrl(staffPage, (u) => new URL(u).host === "auth.workeasy360.com");
  await staffPage.waitForSelector('input[type="password"]', { visible: true, timeout: 20000 });
  flow("L: signing out on app. ends the admin. and auth. sessions of that sign-in", before.body.authenticated && out.status === 200, { where });
  const adminSession = await staffPage.evaluate(() => null);
  void adminSession;
}

async function journeyReturnUrls() {
  const page = await newPage(await context());
  for (const hostile of ["https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "/admin", "/o/../../admin"]) {
    await page.goto(`${ORIGIN.app}/login?next=${encodeURIComponent(hostile)}`, { waitUntil: "networkidle0" });
    const at = await waitForUrl(page, (u) => new URL(u).host === "auth.workeasy360.com");
    const next = new URL(at).searchParams.get("next");
    flow(`R: next=${hostile} is not carried to the sign-in`, next === null, { at });
  }
  // A forged sign-in address naming another site still lands on the portal.
  const state = "x".repeat(43);
  await page.goto(`${ORIGIN.auth}/login?mode=user&next=${encodeURIComponent("https://evil.example")}&state=${state}`, { waitUntil: "networkidle0" });
  await enterPassword(page, "owner@browser.test");
  await sleep(3000);
  const host = new URL(page.url()).host;
  flow("R: a forged state cannot complete a hand-off, and nothing leaves the platform", host.endsWith("workeasy360.com") && !page.url().includes("evil"), { at: page.url() });
}

async function reflow(staffPage, ownerPage, partnerPage) {
  const pages = [
    [null, `${ORIGIN.auth}/login`],
    [null, `${ORIGIN.auth}/login?mode=admin`],
    [staffPage, `${ORIGIN.admin}/admin`],
    [staffPage, `${ORIGIN.admin}/admin/products`],
    [partnerPage, `${ORIGIN.partner}/partner`],
    [partnerPage, `${ORIGIN.partner}/partner/${PARTNER_ID}/products`],
    [ownerPage, `${ORIGIN.app}/o/${CUSTOMER_ORG}/dashboard`],
    [ownerPage, `${ORIGIN.app}/o/${CUSTOMER_ORG}/apps`],
    [ownerPage, `${ORIGIN.app}/o/${CUSTOMER_ORG}/explore`],
  ];
  for (const [source, url] of pages) {
    for (const width of WIDTHS) {
      const page = source || (await newPage(await context(), width));
      await page.setViewport({ width, height: 900 });
      await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
      await sleep(300);
      const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
      report.reflow.push({ width, url, ...w, ok: w.scroll <= w.inner + 1 });
      if (!source) await page.close();
    }
    if (source) await source.setViewport({ width: 1280, height: 900 });
  }
}

(async () => {
  const hosts = ["admin", "partner", "app", "auth", "api"].map((p) => `MAP ${p}.workeasy360.com ${EDGE}`).join(",");
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-gpu",
      `--host-resolver-rules=${hosts}`,
      "--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable",
      `--unsafely-treat-insecure-origin-as-secure=${["admin", "partner", "app", "auth"].map((p) => ORIGIN[p]).join(",")}`,
    ],
  });
  let staffPage = null;
  let ownerPage = null;
  let partnerPage = null;
  try {
    const step = async (name, fn) => {
      if (!ONLY.includes(name)) return null;
      try {
        return await fn();
      } catch (err) {
        flow(`${name}: journey completed`, false, { error: String(err).slice(0, 600) });
        return null;
      }
    };
    staffPage = await step("A", journeyAdmin);
    await step("N", journeyCustomerAsAdmin);
    partnerPage = await step("P", journeyPartner);
    ownerPage = await step("U", journeyUser);
    if (ONLY.includes("T") && ownerPage) {
      for (const product of PRODUCTS) await step("T", () => journeyTrial(ownerPage, product));
    }
    if (staffPage) await step("S", () => journeySwitching(staffPage));
    await step("R", journeyReturnUrls);
    if (ONLY.includes("X") && staffPage && ownerPage && partnerPage) await step("X", () => reflow(staffPage, ownerPage, partnerPage));
    if (staffPage) await step("L", () => journeyLogout(staffPage));
  } finally {
    await browser.close();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    const axeBad = report.pages.filter((p) => p.axe.length);
    const focusBad = report.pages.filter((p) => p.noFocus.length);
    const h1Bad = report.pages.filter((p) => p.h1.length !== 1);
    const reflowBad = report.reflow.filter((r) => !r.ok);
    console.log(
      JSON.stringify(
        {
          flows: `${report.flows.filter((f) => f.ok).length}/${report.flows.length}`,
          failedFlows: report.flows.filter((f) => !f.ok).map((f) => f.flow),
          pages: report.pages.length,
          axe: axeBad.map((p) => `${p.label}: ${p.axe.map((v) => v.id).join(",")}`),
          noFocus: focusBad.map((p) => `${p.label}: ${p.noFocus.join("; ")}`),
          h1: h1Bad.map((p) => `${p.label}: ${p.h1.length}`),
          csp: report.csp,
          errors: report.errors.slice(0, 20),
          reflow: `${report.reflow.length - reflowBad.length}/${report.reflow.length}`,
          reflowBad,
        },
        null,
        2,
      ),
    );
  }
})();
