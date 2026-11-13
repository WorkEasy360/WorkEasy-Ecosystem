// Real-browser check: same account or a different one (ADR-066).
//
// 1 ASK ONCE      - signed in for one product, the next product shows "Welcome to
//                   WorkEasy360" naming the account and organization; Continue with
//                   Same Account opens it; opening it (and the first one) again asks nothing.
// 2 DIFFERENT     - Use Different Account signs this account out and the next person in.
// 3 NEW ACCOUNT   - Create New Account signs out and opens the Store's sign-up, then the
//                   product's Store page.
// 4 SWITCH        - User Admin's account menu: Switch Organization / Switch Account;
//                   Switch Account ends the User Admin AND the central sign-in, forgets
//                   the browser state, and the next product asks for a password.
// 5 EMAIL         - Account & Security: change the sign-in email (popup), the emailed
//                   link's page (a click confirms), the new address signs in.
// 6 PREFERENCES   - the default reads "Ask the first time ... (recommended)".
// Every page audited: one h1, axe WCAG 2.1 A/AA, no sideways scroll at 390/320 px;
// no CSP violation, no console error, no API 5xx anywhere.
//
// Product callbacks (https://crm.workeasy360.com/..., https://book.workeasy360.com/...)
// are answered inside Chrome (request interception): nothing reaches the internet.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   IDS='<the ULOGIN json>' EDGE=127.0.0.1:8088 OUT=<dir> node scripts/unified_login_check/browser_check.js

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const IDS = JSON.parse(process.env.IDS);
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const OUT = process.env.OUT || ".";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const HOSTS = ["admin", "partner", "app", "auth", "api", "store"].map((p) => `${p}.workeasy360.com`);
const APP = "http://app.workeasy360.com";
const AUTH = "http://auth.workeasy360.com";
const STORE = "http://store.workeasy360.com";
const CRM = IDS.products.easy_crm;
const BOOK = IDS.products.easybook;

const report = { checks: [], csp: [], consoleErrors: [], apiErrors: [], pages: [] };
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 240)}` : ""}`);
}

const isCallback = (url) => url.startsWith(CRM.redirect_uri) || url.startsWith(BOOK.redirect_uri);

async function prepare(page) {
  page.on("console", (msg) => {
    const t = msg.text();
    if (/Content Security Policy|Refused to/i.test(t)) report.csp.push(`${page.url()} ${t}`);
    else if (msg.type() === "error" && !/Failed to load resource: the server responded with a status of (401|403|404|409)/.test(t))
      report.consoleErrors.push(`${page.url()} ${t}`);
  });
  page.on("response", (res) => {
    if (res.url().includes("/api/") && res.status() >= 500) report.apiErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
  });
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    const url = req.url();
    if (isCallback(url)) {
      // The product's callback: what it received, nothing more.
      const q = new URL(url).searchParams;
      req.respond({
        status: 200,
        contentType: "text/html",
        body: `<!doctype html><html lang="en"><title>product</title><link rel="icon" href="data:,"><h1>Product callback</h1><p id="code">${q.has("code") ? "code" : "no-code"}</p><p id="error">${q.get("error") ?? ""}</p></html>`,
      });
      return;
    }
    const host = new URL(url).hostname;
    if (host === "crm.workeasy360.com" || host === "book.workeasy360.com") {
      req.respond({ status: 204, body: "" }); // anything else a product page asks its own host for
      return;
    }
    if (url.startsWith("http") && !HOSTS.includes(host) && host !== "127.0.0.1" && host !== "localhost") {
      req.abort(); // nothing else leaves the machine
      return;
    }
    req.continue();
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
/** Poll the page's address from Node (no code is evaluated in the page, so the CSP never sees it). */
async function waitUrl(page, test, timeout = 30000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (test(page.url())) {
      await settle(page);
      return;
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`timed out waiting for the address; at ${page.url()}`);
}
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

async function clickText(page, label, selector = "button, a, [role=menuitem]") {
  const handle = await page.waitForFunction(
    (l, s) =>
      [...document.querySelectorAll(s)].find((b) => {
        const text = b.textContent.trim().replace(/\s+/g, " ");
        // A choice button's label runs straight into its hint ("...AccountContinue using..."), so a prefix match.
        return (text.startsWith(l) || (b.getAttribute("aria-label") ?? "").startsWith(l)) && !b.disabled;
      }),
    { timeout: 30000 },
    label,
    selector,
  );
  await handle.asElement().click();
}

async function audit(page, name) {
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

function authorizeUrl(product) {
  const verifier = crypto.randomBytes(48).toString("base64url");
  const challenge = crypto.createHash("sha256").update(verifier).digest("base64url");
  const q = new URLSearchParams({
    response_type: "code",
    client_id: product.client_id,
    redirect_uri: product.redirect_uri,
    scope: "openid",
    state: crypto.randomBytes(16).toString("hex"),
    nonce: crypto.randomBytes(16).toString("hex"),
    code_challenge: challenge,
    code_challenge_method: "S256",
    organization: IDS.beta_id,
  });
  return `${AUTH}/api/v1/integrations/oauth/authorize?${q}`;
}

/** Open a product; return where the browser ended: "code", "chooser", "login" or the URL. */
async function openProduct(page, product) {
  await page.goto(authorizeUrl(product), { waitUntil: "domcontentloaded" });
  await settle(page);
  const url = page.url();
  if (isCallback(url)) return (await page.$eval("#code", (e) => e.textContent)) === "code" ? "code" : `error:${await page.$eval("#error", (e) => e.textContent)}`;
  if (url.startsWith(`${AUTH}/product-access`)) {
    await waitText(page, "Welcome to WorkEasy360");
    return "chooser";
  }
  if (url.startsWith(`${AUTH}/login`)) return "login";
  return url;
}

async function signInHere(page, email) {
  await page.waitForSelector('input[type="email"]', { timeout: 30000 });
  await page.$eval('input[type="email"]', (e) => (e.value = ""));
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {}), page.click('button[type="submit"]')]);
  await settle(page);
}

async function sessionOn(page, origin) {
  const probe = await page.browserContext().newPage();
  await probe.setRequestInterception(false);
  await probe.goto(`${origin}/api/v1/auth/session`, { waitUntil: "domcontentloaded" });
  const body = JSON.parse(await probe.evaluate(() => document.body.innerText));
  await probe.close();
  return body.authenticated ? body.email : null;
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
  const fresh = async () => {
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    await prepare(page);
    return page;
  };
  /** Signed in at the central sign-in by opening Easy CRM: no question, the password was the answer. */
  const signedInThroughCrm = async (page, email) => {
    const first = await openProduct(page, CRM);
    check(`[${email}] no session: a product opens the central sign-in`, first === "login", first);
    await signInHere(page, email);
    await waitUrl(page, (u) => u.startsWith("https://crm.workeasy360.com/"));
    check(`[${email}] signing in for the request is the answer: Easy CRM opens with no question`, (await page.$eval("#code", (e) => e.textContent)) === "code");
  };

  let page = null;
  try {
    // --- 1 ASK ONCE ---------------------------------------------------------------
    page = await fresh();
    await signedInThroughCrm(page, IDS.owner);
    check("1 ASK ONCE: EasyBook's first entry shows the chooser", (await openProduct(page, BOOK)) === "chooser");
    const text = await bodyText(page);
    for (const needle of ["Opening EasyBook", "Welcome to WorkEasy360", "How would you like to continue?", "Currently signed in as", "Manoj Kumar", IDS.owner, "UAP Beta Labs", "Continue with Same Account", "Use Different Account", "Create New Account"]) {
      check(`1 ASK ONCE: the chooser says "${needle}"`, text.includes(needle));
    }
    await audit(page, "1 chooser");
    await shot(page, "chooser-1440");
    await page.setViewport({ width: 390, height: 844 });
    await shot(page, "chooser-390");
    await page.setViewport({ width: 1440, height: 900 });
    await clickText(page, "Continue with Same Account");
    await waitUrl(page, (u) => u.startsWith("https://book.workeasy360.com/"));
    check("1 ASK ONCE: Continue with Same Account opens EasyBook signed in", (await page.$eval("#code", (e) => e.textContent)) === "code");
    check("1 ASK ONCE: EasyBook again in the same sign-in - no question", (await openProduct(page, BOOK)) === "code");
    check("1 ASK ONCE: Easy CRM again in the same sign-in - no question", (await openProduct(page, CRM)) === "code");
    await page.browserContext().close();

    // --- 2 DIFFERENT ACCOUNT --------------------------------------------------------
    page = await fresh();
    await signedInThroughCrm(page, IDS.owner);
    check("2 DIFFERENT: EasyBook asks", (await openProduct(page, BOOK)) === "chooser");
    await clickText(page, "Use Different Account");
    await waitUrl(page, (u) => u.includes("/login?resume="));
    check("2 DIFFERENT: the central sign-in opens for the same request", page.url().startsWith(`${AUTH}/login?resume=`), page.url());
    check("2 DIFFERENT: Manoj is signed out of the central sign-in", (await sessionOn(page, AUTH)) === null);
    await signInHere(page, IDS.other);
    await waitUrl(page, (u) => u.startsWith("https://book.workeasy360.com/"));
    check("2 DIFFERENT: EasyBook opens as the other person", (await page.$eval("#code", (e) => e.textContent)) === "code");
    check("2 DIFFERENT: the central sign-in is now Priya's", (await sessionOn(page, AUTH)) === IDS.other);
    await page.browserContext().close();

    // --- 3 CREATE NEW ACCOUNT -------------------------------------------------------
    page = await fresh();
    await signedInThroughCrm(page, IDS.owner);
    check("3 NEW ACCOUNT: EasyBook asks", (await openProduct(page, BOOK)) === "chooser");
    await clickText(page, "Create New Account");
    await waitUrl(page, (u) => u.includes("/signup"));
    check("3 NEW ACCOUNT: the Store's sign-up opens, returning to EasyBook's Store page", page.url() === `${STORE}/signup?next=%2Fstore%2Fproduct%2Feasybook`, page.url());
    await waitText(page, "Create");
    check("3 NEW ACCOUNT: Manoj is signed out of the central sign-in", (await sessionOn(page, AUTH)) === null);
    await audit(page, "3 store sign-up");
    await page.browserContext().close();

    // --- 4 SWITCH ACCOUNT from User Admin -------------------------------------------
    page = await fresh();
    await signedInThroughCrm(page, IDS.owner);
    await go(page, `${APP}/login`);
    await signInHere(page, IDS.owner);
    await go(page, `${APP}/o/${IDS.beta_slug}/dashboard`);
    await page.evaluate(() => {
      localStorage.setItem("w360.admin.recent", "[\"someone's record\"]");
      localStorage.setItem("w360.columns.members", "[\"name\"]");
      sessionStorage.setItem("w360.store-org", "uap-beta");
    });
    await clickText(page, "Account menu for", "button");
    await page.waitForSelector("[role=menuitem]");
    const items = await page.$$eval("[role=menuitem]", (els) => els.map((e) => e.textContent.trim()));
    check("4 SWITCH: the account menu", JSON.stringify(items) === JSON.stringify(["My Profile", "Switch Organization", "Switch Account", "Account & Security", "Logout"]), JSON.stringify(items));
    await shot(page, "account-menu-1440", false);
    await clickText(page, "Switch Account");
    await waitUrl(page, (u) => u.startsWith("http://app.workeasy360.com/login?switched=1"));
    await waitText(page, "You're signed out. Sign in with the account you want to use.");
    check("4 SWITCH: back at User Admin's sign-in, saying why", true);
    check("4 SWITCH: User Admin signed out", (await sessionOn(page, APP)) === null);
    check("4 SWITCH: the central sign-in signed out too", (await sessionOn(page, AUTH)) === null);
    const kept = await page.evaluate(() => [localStorage.getItem("w360.admin.recent"), localStorage.getItem("w360.columns.members"), sessionStorage.getItem("w360.store-org")]);
    check("4 SWITCH: the previous person's browser state is forgotten, layout kept", kept[0] === null && kept[1] === "[\"name\"]" && kept[2] === null, JSON.stringify(kept));
    await audit(page, "4 sign-in after switch");
    await shot(page, "switched-login-1440");
    check("4 SWITCH: the next product asks for a password - nobody is signed in silently", (await openProduct(page, CRM)) === "login");
    await go(page, `${AUTH}/switch-account#handle=forged`);
    await waitUrl(page, (u) => u.startsWith("http://app.workeasy360.com/login?switched=1"));
    check("4 SWITCH: a forged hand-over just opens the sign-in page", true);
    await page.browserContext().close();

    // --- 5 EMAIL --------------------------------------------------------------------
    page = await fresh();
    await go(page, `${APP}/login`);
    await signInHere(page, IDS.owner);
    await go(page, `${APP}/o/${IDS.beta_slug}/settings/security`);
    await waitText(page, "Sign-in email");
    const security = await bodyText(page);
    check("5 EMAIL: Account & Security names the sign-in email", security.includes(`You sign in with ${IDS.owner}`));
    check("5 EMAIL: Account & Security lists the sign-in methods", security.includes("Sign-in methods") && security.includes("WorkEasy360 password"));
    await audit(page, "5 account & security");
    await clickText(page, "Change sign-in email");
    await page.waitForSelector("[role=dialog]");
    const dialogViolations = await axe(page);
    check("5 EMAIL: the popup is axe clean", dialogViolations.length === 0, dialogViolations.join("; "));
    await shot(page, "change-email-popup", false);
    await page.type('[role=dialog] input[type="email"]', "manoj.new@uap-acme.dev");
    await page.type('[role=dialog] input[type="password"]', PASSWORD);
    await clickText(page, "Send confirmation link");
    await waitText(page, "We sent a confirmation link to manoj.new@uap-acme.dev");
    check("5 EMAIL: the request says where the link went and that nothing changed yet", true);
    check("5 EMAIL: still signed in with the old address", (await sessionOn(page, APP)) === IDS.owner);
    await page.browserContext().close();

    page = await fresh();
    await go(page, `${APP}/confirm-email#token=${IDS.email_token}`);
    await waitText(page, "Confirm your new sign-in email");
    check("5 EMAIL: the link leaves the address bar at once", !page.url().includes("token="), page.url());
    await audit(page, "5 confirm email");
    await shot(page, "confirm-email-1440");
    await clickText(page, "Confirm new email");
    await waitText(page, "Sign-in email changed");
    check("5 EMAIL: confirmed", true);
    await go(page, `${APP}/login`);
    await signInHere(page, "neha.new@uap-acme.dev");
    check("5 EMAIL: the new address signs in", (await sessionOn(page, APP)) === "neha.new@uap-acme.dev");
    await page.browserContext().close();

    // --- 6 PREFERENCES --------------------------------------------------------------
    page = await fresh();
    await go(page, `${APP}/login`);
    await signInHere(page, IDS.owner);
    await go(page, `${APP}/o/${IDS.beta_slug}/settings/sign-in-preferences`);
    await waitText(page, "Ask the first time I open each product after signing in");
    check("6 PREFERENCES: the default reads Ask once", true);
    await audit(page, "6 sign-in preferences");
    await page.browserContext().close();
    page = null;
  } catch (err) {
    check("the run completed", false, err && err.stack ? err.stack : String(err));
    if (page) await shot(page, "failure");
  } finally {
    check("no CSP violation anywhere", report.csp.length === 0, report.csp.join(" | "));
    check("no console error anywhere", report.consoleErrors.length === 0, report.consoleErrors.join(" | "));
    check("no API 5xx anywhere", report.apiErrors.length === 0, report.apiErrors.join(" | "));
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    const failed = report.checks.filter((c) => !c.ok);
    console.log(`${report.checks.length - failed.length}/${report.checks.length} passed`);
    await browser.close();
    process.exitCode = failed.length ? 1 : 0;
  }
})();
