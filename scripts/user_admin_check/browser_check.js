// Real-browser check: the User Admin Panel of the owner's brief of 11 November 2026
// (login and dashboard after the reference images, the seven menus, the top bar, the
// wallets) and the Super Admin's Login as Guest beside View. The production build
// behind scripts/guest_login_check/edge.js (Host kept), a real backend on the scratch
// database prepare.py made, headless Chrome.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   IDS='<the seed's JSON>' EDGE=127.0.0.1:8088 OUT=<dir> node scripts/user_admin_check/browser_check.js

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
const APP = "http://app.workeasy360.com";
const BASE = `${APP}/o/${IDS.slug}`;
const SEVEN = ["Dashboard", "My Products", "Billing & Subscriptions", "Wallets & Credits", "Organization & Team", "Help", "Settings"];

const report = { checks: [], csp: [], consoleErrors: [], apiErrors: [], pages: [] };
let lastPage = null;
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 240)}` : ""}`);
}

function watch(page, refusalsExpected = false) {
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
    if (!refusalsExpected && res.url().includes("/api/") && status === 403 && !res.url().includes("/auth/"))
      report.apiErrors.push(`403 ${res.request().method()} ${res.url()}`);
  });
}

const text = (page) => page.evaluate(() => document.body.innerText);
const settle = (page) => page.waitForNetworkIdle({ idleTime: 500, timeout: 20000 }).catch(() => {});
async function go(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle(page);
}
async function waitText(page, needle, timeout = 30000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, needle);
}
async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const result = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return result.violations.map((v) => `${v.id}: ${v.nodes.length} (${v.nodes[0]?.target?.join(" ")})`);
  });
}
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
const h1s = (page) => page.$$eval("h1", (els) => els.map((e) => e.textContent.trim()));

async function signIn(page, origin, email) {
  await go(page, `${origin}/login`);
  await page.waitForSelector('input[type="email"]', { visible: true });
  await page.type('input[type="email"]', email);
  await page.type('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
  await settle(page);
}

async function clickText(page, label, selector = "button, a") {
  const handle = await page.waitForFunction(
    (l, s) => [...document.querySelectorAll(s)].find((b) => b.textContent.trim() === l && !b.disabled),
    { timeout: 30000 },
    label,
    selector,
  );
  await handle.asElement().click();
}

/** The sidebar's seven menus: the top-level entries of the User Admin navigation. */
const menus = (page) =>
  page.$$eval('nav[aria-label="User Admin"] > div > ul > li > a, nav[aria-label="User Admin"] > div > ul > li > button', (els) =>
    els.map((e) => e.textContent.trim()),
  );

async function api(page, method, url, body) {
  return page.evaluate(
    async (m, u, b) => {
      const csrf = document.cookie.split("; ").find((c) => c.startsWith("w360_csrf="))?.split("=")[1] ?? "";
      const res = await fetch(u, {
        method: m,
        headers: { "content-type": "application/json", "X-CSRFToken": csrf, "Idempotency-Key": crypto.randomUUID() },
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

const PAGES = [
  "apps",
  "store",
  "explore",
  "settings/billing",
  "orders",
  "settings/billing/profile",
  "wallets",
  "wallets/platform",
  "wallets/ai-credits",
  "wallets/consumption",
  "wallets/usage",
  "settings/organization",
  "settings/members",
  "settings/invitations",
  "settings/products",
  "help",
  "settings/profile",
  "settings/security",
  "settings/sign-in-preferences",
  "settings/notifications",
  "settings/audit",
  "settings/sign-ins",
  "settings/security/authentication",
  "settings/security/network",
];

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
  try {
    // ============================================================ L: the login (image 1)
    const ctxL = await browser.createBrowserContext();
    const l = await ctxL.newPage();
    watch(l);
    await go(l, `${APP}/login`);
    await waitText(l, "User Admin Login");
    const login = await text(l);
    check(
      "L1 the customer portal's sign-in is the User Admin Login of the reference image",
      ["Manage Your Business", "User Admin Panel", "Business Overview", "Products & Subscriptions", "Wallets & Credits", "Organization & Team", "Our Product Ecosystem", "User Admin Login", "For customers and authorized team members", "Remember me", "Forgot Password?", "Login to User Admin Panel", "New to WorkEasy360?", "Create Organization Account", "WorkEasy360 Platform", "All Products", "Reports"].every((t) => login.includes(t)),
      "",
    );
    check("L2 one h1 on the sign-in", (await h1s(l)).length === 1, (await h1s(l)).join(" | "));
    check("L3 the sign-in is axe clean", (await axe(l)).length === 0, (await axe(l)).join(", "));
    await shot(l, "login-1440", false);
    await l.setViewport({ width: 390, height: 844 });
    await settle(l);
    check("L4 the sign-in fits a phone (no sideways scroll at 390 px)", (await overflow(l)) <= 0, await overflow(l));
    await shot(l, "login-390");
    await l.setViewport({ width: 1440, height: 900 });
    // A wrong password reads like every sign-in's.
    await l.type('input[type="email"]', "manoj@uap-acme.dev");
    await l.type('input[type="password"]', "not-the-password");
    await l.click('button[type="submit"]');
    await l.waitForSelector('[role="alert"]', { timeout: 30000 });
    check("L5 a wrong password is refused on the page", (await text(l)).includes("That email or password isn't right."), "");
    await ctxL.close();

    // ============================================================ O: the owner
    const ctxO = await browser.createBrowserContext();
    const o = await ctxO.newPage();
    watch(o);
    await signIn(o, APP, "manoj@uap-acme.dev");
    check("O1 the owner signs in on app. and is taken on", !new URL(o.url()).pathname.startsWith("/login"), o.url());
    await go(o, `${BASE}/dashboard`);
    await waitText(o, "Welcome, Manoj");
    await o.waitForFunction(() => document.body.innerText.includes("Credits") && document.body.innerText.includes("₹10,000.00"), { timeout: 30000 });
    await settle(o);
    check("O2 exactly the seven approved menus, in order", JSON.stringify(await menus(o)) === JSON.stringify(SEVEN), (await menus(o)).join(" | "));
    const dash = await text(o);
    check("O3 the dashboard greets the owner and names the organization", dash.includes("Welcome, Manoj") && dash.includes("UAP Acme Enterprises"), "");
    check(
      "O4 the summary cards of the reference image, from real data",
      ["Active Products", "Team Members", "Total Subscriptions", "Total Spend", "Renewals Due"].every((t) => dash.includes(t)),
      "",
    );
    const cards = await o.$$eval("a[href]", (as) => as.map((a) => a.innerText.replace(/\s+/g, " ").trim()));
    const card = (label) => cards.find((c) => c.startsWith(label)) ?? "";
    check("O5 Team Members counts the four people", /Team Members 4\b/.test(card("Team Members")), card("Team Members"));
    // Whole rupees on the card ("₹10,000"), as in the reference image.
    check("O6 Total Spend is this month's paid top-up order", /Total Spend ₹10,000 This Month/.test(card("Total Spend")), card("Total Spend"));
    check("O7 Total Subscriptions counts Easy CRM", /Total Subscriptions 1\b/.test(card("Total Subscriptions")), card("Total Subscriptions"));
    check(
      "O8 the three wallets apart: Platform 10,000, AI 4,82,000 credits, Consumption 380 and low",
      dash.includes("Platform Wallet") && dash.includes("₹10,000.00") && dash.includes("4,82,000 Credits") && dash.includes("₹380.00") && dash.includes("Low balance"),
      "",
    );
    check("O9 never a combined balance (10,000 + 380)", !dash.includes("10,380"), "");
    check("O10 pending actions from the server (low balance)", /Consumption Wallet/i.test(dash) && dash.includes("Review"), "");
    check("O11 Quick Actions as in the image", ["Buy / Upgrade Product", "Add Money to Wallet", "Invite Team Member", "Raise Support Ticket"].every((t) => dash.includes(t)), "");
    check("O12 the subscription table names the product and plan", dash.includes("Easy CRM") && dash.includes("Advanced"), "");
    check("O13 Store banner and sidebar card", dash.includes("Explore More Products on") && dash.includes("Visit WorkEasy360 Store"), "");
    check("O14 the dashboard is axe clean", (await axe(o)).length === 0, (await axe(o)).join(", "));
    check("O15 one h1 on the dashboard", (await h1s(o)).length === 1, (await h1s(o)).join(" | "));
    await shot(o, "dashboard-1440", false);
    await shot(o, "dashboard-1440-full");
    for (const width of [1920, 1366, 1024, 768, 390, 360]) {
      await o.setViewport({ width, height: 900 });
      await settle(o);
      const over = await overflow(o);
      check(`O16 dashboard at ${width} px: no sideways scroll`, over <= 0, over);
      if (width === 1024 || width === 390) await shot(o, `dashboard-${width}`);
    }
    // The drawer on a phone.
    await o.setViewport({ width: 390, height: 844 });
    await o.click('button[aria-label="Menu"]');
    await o.waitForSelector('nav[aria-label="User Admin"][data-open]', { timeout: 10000 });
    check("O17 the phone menu is a drawer that opens", true, "");
    await shot(o, "drawer-390", false);
    await o.keyboard.press("Escape");
    await o.setViewport({ width: 1440, height: 900 });
    await settle(o);
    // The icon rail.
    await clickText(o, "Collapse menu");
    await o.waitForSelector('[data-rail]', { timeout: 10000 });
    const rail = await o.$eval('nav[aria-label="User Admin"]', (n) => n.getBoundingClientRect().width);
    check("O18 the menu folds to an icon rail on desktop", rail <= 80, rail);
    await shot(o, "rail-1440", false);
    await clickText(o, "Expand menu");
    // Sub-menus open with their menu.
    await o.click('nav[aria-label="User Admin"] button[aria-expanded="false"]');
    const opened = await o.$$eval('nav[aria-label="User Admin"] ul ul a', (as) => as.map((a) => a.textContent.trim()));
    check("O19 a menu's sub-menu shows when it is opened", opened.length > 0, opened.join(", "));

    // Every page: loads, one h1, no sideways scroll, axe clean.
    for (const p of PAGES) {
      await go(o, `${BASE}/${p}`);
      await o.waitForSelector("main h1", { timeout: 30000 }).catch(() => {});
      await settle(o);
      const titles = await h1s(o);
      const over = await overflow(o);
      const violations = await axe(o);
      report.pages.push({ page: p, h1: titles, overflow: over, axe: violations });
      check(`P ${p}: one h1, no sideways scroll, axe clean`, titles.length === 1 && over <= 0 && violations.length === 0, `${titles.join(" | ")} ${over} ${violations.join(", ")}`);
      if (["apps", "settings/billing", "orders", "wallets", "wallets/platform", "settings/members", "help", "settings/security"].includes(p))
        await shot(o, `page-${p.replace(/\//g, "-")}`);
    }
    await go(o, `${BASE}/wallets/platform?action=top-up`);
    await o.waitForSelector('[role="dialog"]', { timeout: 30000 }).catch(() => {});
    check("O20 Add Money opens from the dashboard's link", Boolean(await o.$('[role="dialog"]')), "");
    await go(o, `${BASE}/apps?filter=setup`);
    await waitText(o, "Easy CRM");
    const apps = await text(o);
    check("O21 My Products filters and pending setup inside My Products", ["All", "Active", "Free", "Trial", "Pending Setup", "Expired"].every((t) => apps.includes(t)) && apps.includes("Easy CRM"), "");
    // The bell.
    await go(o, `${BASE}/dashboard`);
    const bell = await o.waitForSelector('header button[aria-label^="Notifications"]', { timeout: 30000 });
    const bellName = await bell.evaluate((b) => b.getAttribute("aria-label"));
    await bell.click();
    await waitText(o, "Notification preferences");
    const panel = await text(o);
    check("O22 notifications: unread items from real state (low balance, payment, setup)", /unread/.test(bellName) && /Consumption/i.test(panel), bellName);
    await o.keyboard.press("Escape");
    // The organization switcher.
    const switcher = await o.waitForSelector('button[aria-label="Current organization: UAP Acme Enterprises. Switch organization"]', { timeout: 30000 });
    await switcher.click();
    await clickText(o, "UAP Beta Labs", '[role="menuitem"]');
    await o.waitForFunction((slug) => location.pathname.startsWith(`/o/${slug}/`), { timeout: 30000 }, IDS.beta);
    await waitText(o, "UAP Beta Labs");
    check("O23 the switcher opens the other organization, its own data", (await text(o)).includes("UAP Beta Labs"), o.url());
    // Account menu.
    await o.click('header button[aria-label^="Account menu for Manoj Kumar"]');
    const account = await o.$$eval('[role="menuitem"]', (els) => els.map((e) => e.textContent.trim()));
    check("O24 the account menu: My Profile, Account & Security, Logout", ["My Profile", "Account & Security", "Logout"].every((t) => account.includes(t)), account.join(", "));
    await o.keyboard.press("Escape");
    await ctxO.close();

    // ============================================================ M: a plain member
    const ctxM = await browser.createBrowserContext();
    const m = await ctxM.newPage();
    watch(m, true);
    await signIn(m, APP, "member@uap-acme.dev");
    await go(m, `${BASE}/dashboard`);
    await waitText(m, "Welcome, Neha");
    check("M1 a member sees the same seven menus", JSON.stringify(await menus(m)) === JSON.stringify(SEVEN), (await menus(m)).join(" | "));
    const mdash = await text(m);
    check("M2 no balances and no spend for a member; Contact the organization admin", !mdash.includes("₹10,000.00") && !mdash.includes("Total Spend") && /Contact your\s+organization admin/i.test(mdash), "");
    await go(m, `${BASE}/wallets`);
    await settle(m);
    check("M3 the Wallets page says who can help", /Contact Organization Admin|organization admin/i.test(await text(m)) && !(await text(m)).includes("₹10,000.00"), "");
    const refusedRead = await api(m, "GET", `/api/v1/orgs/${IDS.slug}/wallets`);
    const refusedSpend = await api(m, "POST", `/api/v1/orgs/${IDS.slug}/wallets/top-up`, { wallet_kind: "platform", amount_minor: 100, currency: "INR" });
    check("M4 the server refuses a member's wallet read and top-up", refusedRead.status === 403 && refusedSpend.status === 403, `${refusedRead.status} ${refusedSpend.status}`);
    await shot(m, "member-wallets");
    await ctxM.close();

    // ============================================================ B: the Billing Manager
    const ctxB = await browser.createBrowserContext();
    const b = await ctxB.newPage();
    watch(b);
    await signIn(b, APP, "billing@uap-acme.dev");
    await go(b, `${BASE}/wallets`);
    await waitText(b, "₹10,000.00");
    const bw = await text(b);
    check("B1 a Billing Manager sees the three balances and may add money", bw.includes("4,82,000") && /Add Money/.test(bw), "");
    await ctxB.close();

    // ============================================================ S: the Super Admin
    const ctxS = await browser.createBrowserContext();
    const s = await ctxS.newPage();
    watch(s, true);
    await signIn(s, ADMIN, STAFF);
    const lists = [
      ["affiliate", "UAP Affiliate Co", "Affiliate"],
      ["reseller", "UAP Reseller Co", "Reseller"],
      ["white-label", "UAP Brand Co", "White Label"],
    ];
    for (const [list, name] of lists) {
      for (const width of [1366, 1280]) {
        await s.setViewport({ width, height: 900 });
        await go(s, `${ADMIN}/admin/partners/${list}`);
        await s.waitForSelector(`a[aria-label="View ${name}"]`, { timeout: 30000 });
        const heads = await s.$$eval("table thead th", (ths) => ths.map((t) => t.textContent.trim()));
        const geo = await s.evaluate((n) => {
          const view = document.querySelector(`a[aria-label="View ${n}"]`).getBoundingClientRect();
          const guest = document.querySelector(`button[aria-label="Login as Guest to ${n}"]`).getBoundingClientRect();
          return { view: [view.left, view.right, view.top], guest: [guest.left, guest.right, guest.top], width: window.innerWidth };
        }, name);
        const beside = geo.guest[0] >= geo.view[1] && geo.guest[0] - geo.view[1] < 24 && Math.abs(geo.guest[2] - geo.view[2]) < 6;
        const inView = geo.guest[1] <= geo.width && geo.view[0] >= 0;
        check(
          `S1 ${list} at ${width} px: View / Actions fourth, Login as Guest right beside View, both in view`,
          heads[3] === "View / Actions" && !heads.includes("Guest Login") && beside && inView,
          `${heads.slice(0, 4).join(" | ")} ${JSON.stringify(geo)}`,
        );
      }
      await shot(s, `admin-${list}-1280`, false);
    }
    await s.setViewport({ width: 390, height: 844 });
    await go(s, `${ADMIN}/admin/partners/reseller`);
    await s.waitForSelector('button[aria-label="Login as Guest to UAP Reseller Co"]', { timeout: 30000 });
    check("S2 on a phone both actions stay in the row", Boolean(await s.$('a[aria-label="View UAP Reseller Co"]')), "");
    await shot(s, "admin-reseller-390");
    await s.setViewport({ width: 1366, height: 900 });

    // The partner's own page: the actions at the top.
    await go(s, `${ADMIN}/admin/partners/${IDS.affiliate}`);
    await waitText(s, "Back to Partners");
    const top = await s.evaluate(() =>
      ["Back to Partners", "Edit Partner", "Login as Guest"].map((t) => {
        const el = [...document.querySelectorAll("a, button")].find((e) => e.textContent.trim() === t);
        return el ? Math.round(el.getBoundingClientRect().top) : null;
      }),
    );
    check("S3 the partner page has Back to Partners, Edit Partner and Login as Guest at the top", top.every((y) => y !== null && y < 320), JSON.stringify(top));
    await shot(s, "admin-partner-detail", false);

    // Login as Guest, for each type of partner, from a filtered list.
    for (const [list, name, type] of lists) {
      await go(s, `${ADMIN}/admin/partners/${list}?q=UAP`);
      const button = await s.waitForSelector(`button[aria-label="Login as Guest to ${name}"]`, { timeout: 30000 });
      await button.click();
      await s.waitForSelector('[role="dialog"] select', { timeout: 30000 });
      await Promise.all([s.waitForNavigation({ timeout: 60000 }).catch(() => {}), clickText(s, "Start guest session")]);
      await s.waitForFunction(() => location.host === "partner.workeasy360.com", { timeout: 60000 });
      await waitText(s, `Guest Session – Viewing Partner: ${name}`);
      const banner = await text(s);
      check(
        `S4 ${type}: the partner's own dashboard under the Guest Session banner, with its type and both exits`,
        banner.includes(`Partner type: ${type}`) && banner.includes("Return to Super Admin") && banner.includes("End Guest Session"),
        s.url(),
      );
      if (list === "reseller") await shot(s, "guest-reseller", false);
      const write = await api(s, "POST", `/api/v1/partner/${IDS[list === "white-label" ? "white_label" : list]}/platform-wallet/top-up`, { amount: "100.00" });
      check(`S5 ${type}: a write in the guest session is refused`, write.status === 403, `${write.status} ${write.json?.reason ?? ""}`);
      const exit = list === "affiliate" ? "End Guest Session" : "Return to Super Admin";
      await Promise.all([s.waitForNavigation({ timeout: 60000 }).catch(() => {}), clickText(s, exit)]);
      await s.waitForFunction(() => location.host === "admin.workeasy360.com", { timeout: 60000 });
      await settle(s);
      const back = new URL(s.url());
      if (exit === "End Guest Session") {
        check(`S6 ${type}: End Guest Session opens the partner's page in the Admin, still signed in`, back.pathname === `/admin/partners/${IDS.affiliate}`, s.url());
      } else {
        await s.waitForSelector('input[type="search"]', { timeout: 30000 });
        const q = await s.$eval('input[type="search"]', (i) => i.value);
        check(`S6 ${type}: Return to Super Admin goes back to the list, its search kept, still signed in`, back.pathname === `/admin/partners/${list}` && q === "UAP", `${s.url()} q=${q}`);
      }
    }
    // The partner host no longer opens the partner's data.
    const p2 = await s.browserContext().newPage();
    watch(p2, true);
    await go(p2, `${PARTNER}/partner`);
    await settle(p2);
    check("S7 after the guest session the partner portal asks for a sign-in", new URL(p2.url()).pathname.startsWith("/login") || (await text(p2)).includes("Partner Login"), p2.url());
    await p2.close();
    await ctxS.close();

    // ============================================================ X: impersonation refused
    const ctxX = await browser.createBrowserContext();
    const x = await ctxX.newPage();
    watch(x, true);
    await signIn(x, PARTNER, "owner@uap-reseller.dev");
    const fromPartner = await api(x, "POST", `/api/v1/admin/partners/${IDS.affiliate}/guest-view`, { reason: "Trying" });
    check("X1 a partner user cannot start a guest session (refused)", [401, 403, 404].includes(fromPartner.status), fromPartner.status);
    await ctxX.close();
    const ctxY = await browser.createBrowserContext();
    const y = await ctxY.newPage();
    watch(y, true);
    await signIn(y, APP, "manoj@uap-acme.dev");
    const fromCustomer = await api(y, "POST", `/api/v1/admin/partners/${IDS.reseller}/guest-view`, { reason: "Trying" });
    check("X2 a customer cannot start a guest session (refused)", [401, 403, 404].includes(fromCustomer.status), fromCustomer.status);
    await ctxY.close();
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
