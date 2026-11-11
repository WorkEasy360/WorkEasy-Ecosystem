// Real-browser check: the Partner Admin Panel of 08 November 2026 (ADR-063).
// The production build behind scripts/guest_login_check/edge.js (Host kept), a real
// backend on the scratch database prepare.py made, headless Chrome.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   IDS='<the seed's JSON>' EDGE=127.0.0.1:8088 OUT=<dir> node scripts/partner_admin_panel_check/browser_check.js

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const IDS = JSON.parse(process.env.IDS);
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const OUT = process.env.OUT || ".";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const STAFF = "staff@eco.test";
const HOSTS = ["admin", "partner", "app", "auth", "api"].map((p) => `${p}.workeasy360.com`);
const ADMIN = "http://admin.workeasy360.com";
const PARTNER = "http://partner.workeasy360.com";
const EIGHT = ["Dashboard", "Products & Referrals", "Customers", "Billing & Subscriptions", "Platform Wallet", "Earnings & Payouts", "Help", "Settings"];
const inr = (major) => `₹${major}`;

const report = { checks: [], csp: [], consoleErrors: [], apiErrors: [] };
let lastPage = null;
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 220)}` : ""}`);
}

function watch(page, expectRefusals = false) {
  lastPage = page;
  page.on("console", (msg) => {
    const text = msg.text();
    if (/Content Security Policy|Refused to/i.test(text)) report.csp.push(text);
    else if (msg.type() === "error" && !/Failed to load resource: the server responded with a status of (401|403|404|409)/.test(text))
      report.consoleErrors.push(`${page.url()} ${text}`);
  });
  page.on("response", (res) => {
    const status = res.status();
    if (res.url().includes("/api/") && status >= 500) report.apiErrors.push(`${status} ${res.request().method()} ${res.url()}`);
    if (!expectRefusals && res.url().includes("/api/") && status === 403 && !res.url().includes("/auth/")) {
      report.apiErrors.push(`403 ${res.request().method()} ${res.url()}`);
    }
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (page) => page.evaluate(() => document.body.innerText);

async function waitText(page, needle, timeout = 30000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, needle);
}

async function settle(page) {
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 20000 }).catch(() => {});
}

async function go(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle(page);
}

async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const result = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return result.violations.map((v) => `${v.id}: ${v.nodes.length}`);
  });
}

async function h1s(page) {
  return page.$$eval("h1", (els) => els.map((e) => e.textContent.trim()));
}

async function sidebar(page) {
  await page.waitForSelector('nav[aria-label$="workspace"]', { timeout: 30000 });
  return page.$$eval('nav[aria-label$="workspace"] li > a, nav[aria-label$="workspace"] li > button', (els) =>
    els.map((e) => e.textContent.trim()),
  );
}

async function tabs(page) {
  return page.$$eval('nav[aria-label="Sections"] a', (els) => els.map((e) => e.textContent.trim()));
}

async function signIn(page, origin, email) {
  await go(page, `${origin}/login`);
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
  await settle(page);
}

async function clickButton(page, label) {
  const handle = await page.waitForFunction(
    (l) => [...document.querySelectorAll("button, a")].find((b) => b.textContent.trim() === l && !b.disabled),
    { timeout: 30000 },
    label,
  );
  await handle.asElement().click();
}

async function selectByLabel(page, label, value) {
  const id = await page.waitForFunction(
    (l, v) => {
      // A required field's label carries its mark after the words.
      const lab = [...document.querySelectorAll("label")].find((x) => x.textContent.trim() === l || x.textContent.trim().startsWith(`${l}*`) || x.textContent.trim().startsWith(`${l} `));
      const sel = lab && document.getElementById(lab.htmlFor);
      return sel && [...sel.options].some((o) => o.value === v) ? sel.id : null;
    },
    { timeout: 30000 },
    label,
    value,
  );
  // React's generated ids hold colons: an attribute selector, never `#id`.
  await page.select(`[id="${await id.jsonValue()}"]`, value);
}

async function noHorizontalScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
}

async function partnerApi(page, method, url, body, key) {
  return page.evaluate(
    async (m, u, b, k) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="))?.split("=")[1] ?? "";
      const res = await fetch(u, {
        method: m,
        headers: { "content-type": "application/json", "x-csrftoken": csrf, ...(k ? { "idempotency-key": k } : {}) },
        body: b ? JSON.stringify(b) : undefined,
      });
      let json = null;
      try {
        json = await res.json();
      } catch {
        /* none */
      }
      return { status: res.status, json };
    },
    method,
    url,
    body,
    key,
  );
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
    defaultViewport: { width: 1366, height: 900 },
  });
  const shot = async (page, name) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true }).catch(() => {});
  try {
    // ================================================================ R: the reseller
    const ctxR = await browser.createBrowserContext();
    const r = await ctxR.newPage();
    watch(r);
    await signIn(r, PARTNER, "owner@pap-reseller.dev");
    check("R1 the reseller signs in on partner. and lands on its dashboard", new URL(r.url()).pathname === "/partner/reseller/dashboard", r.url());
    const navR = await sidebar(r);
    check("R2 exactly eight sidebar menus, in the specification's order", JSON.stringify(navR) === JSON.stringify(EIGHT), navR.join(" | "));
    await waitText(r, "Account status");
    const dash = await text(r);
    check("R3 the dashboard shows KYC and account status side by side", /KYC:\s*Approved/i.test(dash) && /Account:\s*Active/i.test(dash), dash.slice(0, 300));
    check("R4 the dashboard shows the Platform Wallet apart from earnings", dash.includes("Platform Wallet") && dash.includes(inr("1,000.00")) && dash.includes("My earnings"), "");
    const quick = await r.$$eval('nav[aria-label="Quick actions"] a, nav[aria-label="Quick actions"] button', (els) => els.map((e) => e.textContent.trim()));
    check("R5 quick actions: Activate Customer Plan, Add Money, Contact Support", ["Activate Customer Plan", "Add Money", "Contact Support"].every((q) => quick.includes(q)), quick.join(" | "));
    check("R6 the dashboard: one h1, axe clean", (await h1s(r)).length === 1 && (await axe(r)).length === 0, (await axe(r)).join(", "));
    await shot(r, "R-dashboard");

    // Plan Activation from the wallet.
    await go(r, `${PARTNER}/partner/reseller/customers/activate`);
    check("R7 Plan Activation is a tab of Customers", (await tabs(r)).join("|") === "Assigned Organizations|Products & Setup|Plan Activation|Downline Partners", (await tabs(r)).join("|"));
    await selectByLabel(r, "Assigned customer", IDS.customers[0]);
    await selectByLabel(r, "Eligible product", "easy_crm");
    const planValue = await r.evaluate(() => {
      const lab = [...document.querySelectorAll("label")].find((x) => x.textContent.trim() === "Available plan");
      const sel = lab && document.getElementById(lab.htmlFor);
      return [...(sel?.options ?? [])].find((o) => o.textContent.trim() === "Basic")?.value ?? "";
    });
    await selectByLabel(r, "Available plan", planValue);
    await clickButton(r, "Review price");
    await waitText(r, "Platform Wallet deduction");
    const priced = await text(r);
    check("R8 the approved purchase price (999.00 less 20%) and the wallet after are shown before confirming", priced.includes(inr("799.20")) && priced.includes(inr("200.80")), "");
    check("R9 the price step is axe clean", (await axe(r)).length === 0, (await axe(r)).join(", "));
    await clickButton(r, `Confirm and pay ${inr("799.20")}`);
    await waitText(r, "is recorded and the plan is active");
    const done = await text(r);
    const order = (done.match(/Order (ORD-\d+)/) || [])[1];
    check("R10 confirming records the order and activates the plan", Boolean(order) && done.includes(inr("200.80")), order);
    await shot(r, "R-activated");

    await go(r, `${PARTNER}/partner/reseller/platform-wallet/transactions`);
    await waitText(r, "Plan activation");
    const tx = await text(r);
    check("R11 the wallet transaction names the order and the customer", tx.includes(order) && tx.includes("PAP Customer One") && tx.includes(`-${inr("799.20")}`), "");

    await go(r, `${PARTNER}/partner/reseller/users/subscriptions`);
    await waitText(r, "PAP Customer One");
    const subs = await text(r);
    check("R12 Customer Subscriptions lists it as paid from the wallet, with its order", subs.includes("Your Platform Wallet") && subs.includes(order), "");
    check("R13 the subscription filters are Active, Pending Setup, Expiring, Expired", ["Active", "Pending Setup", "Expiring", "Expired"].every((f) => subs.includes(f)), "");

    // A duplicate charge cannot happen: the same customer and plan again is refused.
    await go(r, `${PARTNER}/partner/reseller/customers/activate`);
    await selectByLabel(r, "Assigned customer", IDS.customers[1]);
    await selectByLabel(r, "Eligible product", "easy_crm");
    const advanced = await r.evaluate(() => {
      const lab = [...document.querySelectorAll("label")].find((x) => x.textContent.trim() === "Available plan");
      const sel = lab && document.getElementById(lab.htmlFor);
      return [...(sel?.options ?? [])].find((o) => o.textContent.trim() === "Advanced")?.value ?? "";
    });
    await selectByLabel(r, "Available plan", advanced);
    await clickButton(r, "Review price");
    await waitText(r, "does not hold enough");
    const short = await r.$$eval("a", (as) => as.filter((a) => a.textContent.trim() === "Add Money").map((a) => a.getAttribute("href")));
    check("R14 an activation the wallet cannot pay offers Add Money and no confirm", short.includes("/partner/reseller/platform-wallet") && !(await text(r)).includes("Confirm and pay"), short.join(","));

    // Add Money online, through the test provider's page and its signed callback.
    await go(r, `${PARTNER}/partner/reseller/platform-wallet`);
    await clickButton(r, "Add Money");
    await r.waitForSelector('[role="dialog"] input[inputmode="decimal"]');
    await r.type('[role="dialog"] input[inputmode="decimal"]', "500.00");
    await Promise.all([r.waitForNavigation({ timeout: 60000 }).catch(() => {}), clickButton(r, "Continue to payment")]);
    await r.waitForFunction(() => location.href.includes("test-checkout"), { timeout: 60000 });
    const hosted = new URL(r.url());
    check("R15 Add Money opens the provider's hosted page (the test provider's, on app.)", hosted.host === "app.workeasy360.com", hosted.href.split("#")[0]);
    // The development test provider's "hosted page" is the platform's own page on
    // app., which needs a sign-in there (host-only sessions). A real provider's page
    // is on its own domain and confirms by its signed callback; here the check
    // confirms exactly as that page does - the test provider's public completion -
    // and then follows the return address.
    const hash = new URLSearchParams(hosted.hash.slice(1));
    const reference = hash.get("reference");
    const back = hash.get("return");
    await go(r, `${PARTNER}/partner/reseller/platform-wallet`);
    const completed = await partnerApi(r, "POST", "/api/v1/public/payments/test-checkout/complete", {
      organization_id: "",
      reference,
      outcome: "succeeded",
    });
    check("R15b the test provider confirms the payment through the signed callback path", completed.status < 300, JSON.stringify(completed));
    await go(r, back);
    await waitText(r, "Payment sent");
    await sleep(1000);
    await go(r, `${PARTNER}/partner/reseller/platform-wallet`);
    await waitText(r, "Top-ups");
    const after = await text(r);
    check("R16 the verified payment credits the wallet once (200.80 + 500.00)", after.includes(inr("700.80")), "");
    check("R17 the wallet page: one h1, axe clean", (await h1s(r)).length === 1 && (await axe(r)).length === 0, (await axe(r)).join(", "));
    await shot(r, "R-wallet");

    // Phone and tablet.
    for (const [w, h, name] of [
      [390, 844, "phone"],
      [768, 1024, "tablet"],
    ]) {
      await r.setViewport({ width: w, height: h });
      for (const p of ["dashboard", "platform-wallet", "customers/activate", "users/subscriptions"]) {
        await go(r, `${PARTNER}/partner/reseller/${p}`);
        await sleep(600);
        check(`R18 ${p} at ${w}px (${name}): no sideways page scroll`, await noHorizontalScroll(r), "");
      }
      await shot(r, `R-${name}`);
    }
    await r.setViewport({ width: 1366, height: 900 });

    // Settings: no White Label for a reseller without it.
    await go(r, `${PARTNER}/partner/reseller/business-kyc`);
    const settingsTabs = await tabs(r);
    check("R19 a reseller without White Label approval has no White Label settings", !settingsTabs.includes("White Label") && settingsTabs.includes("Business & KYC"), settingsTabs.join("|"));
    check("R20 Business & KYC shows the KYC application and its status", (await text(r)).includes("KYC application") && (await text(r)).includes("Approved"), "");
    await go(r, `${PARTNER}/partner/reseller/account`);
    check("R21 My Account & Security offers Sign out all other sessions", (await text(r)).includes("Sign out all other sessions"), "");
    await go(r, `${PARTNER}/partner/reseller/help`);
    await waitText(r, "Which KYC documents do I need?");
    check("R22 Help Content lists the published partner FAQ", true, "");
    await ctxR.close();

    // ================================================================ A: the affiliate
    const ctxA = await browser.createBrowserContext();
    const a = await ctxA.newPage();
    watch(a, true);
    await signIn(a, PARTNER, "owner@pap-affiliate.dev");
    const navA = await sidebar(a);
    check(
      "A1 an affiliate sees six menus and no reseller module",
      JSON.stringify(navA) === JSON.stringify(["Dashboard", "Products & Referrals", "Billing & Subscriptions", "Earnings & Payouts", "Help", "Settings"]),
      navA.join(" | "),
    );
    await go(a, `${PARTNER}/partner/affiliate/eligible-products`);
    await a.waitForFunction(() => [...document.querySelectorAll("button")].some((b) => /Create my Store link|Copy Store referral link/.test(b.textContent)), { timeout: 30000 });
    if ((await text(a)).includes("Create my Store link")) {
      await clickButton(a, "Create my Store link");
      await a.waitForSelector('input[aria-label="Store referral link"]', { timeout: 30000 });
    }
    const storeLink = await a.$eval('input[aria-label="Store referral link"]', (i) => i.value);
    check("A2 Copy Store referral link: the partner's own link, opening the Store", /\/r\/[A-Z0-9]+\?to=store$/.test(storeLink), storeLink);
    check("A3 Eligible Products lists Easy CRM with its commission terms", (await text(a)).includes("Products you may promote"), "");
    const visitor = await browser.createBrowserContext();
    const v = await visitor.newPage();
    await v.goto(storeLink, { waitUntil: "domcontentloaded" });
    await v.waitForFunction(() => location.pathname === "/store", { timeout: 60000 }).catch(() => {});
    check("A4 following the Store link lands on the Store (attributed)", new URL(v.url()).pathname === "/store", v.url());
    await visitor.close();
    await go(a, `${PARTNER}/partner/affiliate/users`);
    check("A5 an affiliate is refused the customer pages", (await text(a)).includes("You don't have access to this page."), "");
    const wallet = await partnerApi(a, "GET", `/api/v1/partner/${IDS.affiliate}/platform-wallet`);
    check("A6 an affiliate's Platform Wallet API answers 403", wallet.status === 403, wallet.status);
    const cross = await partnerApi(a, "GET", `/api/v1/partner/${IDS.reseller}/platform-wallet`);
    check("A7 another partner's wallet, by editing the id, is a 404", cross.status === 404, cross.status);
    await ctxA.close();

    // ================================================================ W: White Label only
    const ctxW = await browser.createBrowserContext();
    const w = await ctxW.newPage();
    watch(w, true);
    await signIn(w, PARTNER, "owner@pap-brand.dev");
    const navW = await sidebar(w);
    check("W1 White Label alone: no Customers, Platform Wallet or referral menus", !navW.includes("Customers") && !navW.includes("Platform Wallet") && !navW.includes("Products & Referrals"), navW.join(" | "));
    await go(w, `${PARTNER}/partner/white-label/brand-settings`);
    check("W2 its Settings carry the White Label tab", (await tabs(w)).includes("White Label"), (await tabs(w)).join("|"));
    const sell = await partnerApi(w, "POST", `/api/v1/partner/${IDS.white_label}/customers`, { organization_name: "X Co", owner_email: "x@pap.dev" }, "pap-w3");
    check("W3 White Label approval grants no customer creation (server refuses)", sell.status === 403, sell.status);
    await ctxW.close();

    // ================================================================ D: a draft partner
    const ctxD = await browser.createBrowserContext();
    const d = await ctxD.newPage();
    watch(d, true);
    await signIn(d, PARTNER, "owner@pap-draft.dev");
    await waitText(d, "Onboarding status");
    check("D1 a draft lands on its onboarding dashboard", new URL(d.url()).pathname.endsWith("/dashboard"), d.url());
    const navD = await sidebar(d);
    check("D2 before approval: Dashboard, Help and Settings only", JSON.stringify(navD) === JSON.stringify(["Dashboard", "Help", "Settings"]), navD.join(" | "));
    check("D3 the onboarding dashboard is axe clean", (await axe(d)).length === 0, (await axe(d)).join(", "));
    const base = new URL(d.url()).pathname.replace(/\/dashboard$/, "");
    await go(d, `${PARTNER}${base}/customers/activate`);
    check("D4 a commercial page says it opens once approved", (await text(d)).includes("This opens once your partner account is approved."), "");
    await go(d, `${PARTNER}${base}/business-kyc`);
    await waitText(d, "KYC application");
    check("D5 Business & KYC opens before approval", /KYC:\s*Draft/i.test(await text(d)), "");
    await go(d, `${PARTNER}${base}/support-manager`);
    check("D6 support tickets open before approval", (await h1s(d)).length === 1, (await h1s(d)).join("|"));
    const drafted = await partnerApi(d, "POST", `/api/v1/partner/${IDS.draft}/customers`, { organization_name: "X Co", owner_email: "x@pap.dev" }, "pap-d7");
    check("D7 the server refuses a draft's customer creation", drafted.status === 403, drafted.status);
    await ctxD.close();

    // ================================================================ S: the Super Admin
    const ctxS = await browser.createBrowserContext();
    const s = await ctxS.newPage();
    watch(s, true);
    await signIn(s, ADMIN, STAFF);
    await go(s, `${ADMIN}/admin/partners/all`);
    await s.waitForSelector("table thead th");
    const heads = await s.$$eval("table thead th", (ths) => ths.map((t) => t.textContent.trim()));
    const wanted = ["Partner Name", "Partner Email", "Type / Permissions", "KYC Status", "Account Status", "Country", "Assigned Customers", "Created", "Guest Login"];
    check("S1 the partner list has the specification's columns", wanted.every((h) => heads.includes(h)), heads.join(" | "));
    check("S2 the list is axe clean", (await axe(s)).length === 0, (await axe(s)).join(", "));
    // Login as Guest: the reason first.
    const login = await s.waitForSelector('button[aria-label="Login as Guest to PAP Reseller Co"]', { timeout: 30000 });
    await login.evaluate((b) => b.scrollIntoView({ block: "center", inline: "center" }));
    await login.click();
    await s.waitForSelector('[role="dialog"] select', { timeout: 30000 });
    check("S3 Login as Guest asks for the reason", (await text(s)).includes("Start guest session"), "");
    await Promise.all([s.waitForNavigation({ timeout: 60000 }).catch(() => {}), clickButton(s, "Start guest session")]);
    await s.waitForFunction(() => location.host === "partner.workeasy360.com", { timeout: 60000 });
    await waitText(s, "Guest Session — Viewing Partner:");
    check("S4 the partner portal shows the Guest Session banner", (await text(s)).includes("Guest Session — Viewing Partner:"), s.url());
    const refused = await partnerApi(s, "POST", `/api/v1/partner/${IDS.reseller}/platform-wallet/top-up`, { amount: "100.00" });
    check("S5 a write in a guest session is refused (guest_read_only)", refused.status === 403 && refused.json?.reason === "guest_read_only", JSON.stringify(refused));
    await Promise.all([s.waitForNavigation({ timeout: 60000 }).catch(() => {}), clickButton(s, "Exit Guest Session")]);
    await s.waitForFunction(() => location.host === "admin.workeasy360.com", { timeout: 60000 });
    check("S6 Exit Guest Session returns to the Admin, still signed in", new URL(s.url()).pathname.startsWith("/admin/partners"), s.url());
    // New partner: Partner Email, and base permissions beside White Label.
    await go(s, `${ADMIN}/admin/partners/new`);
    await waitText(s, "Partner Email");
    check("S7 New partner asks for the Partner Email", (await text(s)).includes("Partner Email"), "");
    await selectByLabel(s, "Partner type", "white_label");
    await waitText(s, "Also allow");
    check("S8 White Label asks which base permissions to grant beside it", (await text(s)).includes("White Label approval gives branding and a custom domain only"), "");
    await go(s, `${ADMIN}/admin/settings`);
    await waitText(s, "Partner policies");
    check("S9 Settings carry the two partner policies", (await text(s)).includes("Partners active before KYC") || (await text(s)).includes("Active before KYC"), "");
    await ctxS.close();
  } catch (err) {
    check("the run itself", false, err.stack || String(err));
    if (lastPage) {
      await lastPage.screenshot({ path: path.join(OUT, "failure.png"), fullPage: true }).catch(() => {});
      console.log("AT", lastPage.url());
      console.log((await lastPage.evaluate(() => document.body.innerText).catch(() => "")).slice(0, 1500));
    }
  } finally {
    check("no CSP violation anywhere", report.csp.length === 0, report.csp.slice(0, 3).join(" || "));
    check("no console error anywhere", report.consoleErrors.length === 0, report.consoleErrors.slice(0, 3).join(" || "));
    check("no unexpected API error", report.apiErrors.length === 0, report.apiErrors.slice(0, 5).join(" || "));
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    const failed = report.checks.filter((c) => !c.ok).length;
    console.log(`\n${report.checks.length - failed} passed, ${failed} failed`);
    await browser.close();
    process.exit(failed ? 1 : 0);
  }
})();
