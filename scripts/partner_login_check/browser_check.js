// Real-browser check: the Partner portal's sign-in and sign-up (2026-11-11, the
// owner's reference image). The production build behind scripts/guest_login_check/edge.js
// (Host kept), a real backend on the scratch database scripts/partner_admin_panel_check/prepare.py
// made, headless Chrome. Not part of CI.
//
// NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<seed's>
//   EDGE=127.0.0.1:8088 OUT=<dir> node scripts/partner_login_check/browser_check.js

const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const PASSWORD = process.env.CHECK_PASSWORD;
const EDGE = process.env.EDGE || "127.0.0.1:8088";
const OUT = process.env.OUT || ".";
const AXE = fs.readFileSync(process.env.AXE_PATH, "utf8");
const RESELLER = "owner@pap-reseller.dev";
const HOSTS = ["admin", "partner", "app", "auth", "api"].map((p) => `${p}.workeasy360.com`);
const PARTNER = "http://partner.workeasy360.com";
const ADMIN = "http://admin.workeasy360.com";
const APP = "http://app.workeasy360.com";
const SESSION_COOKIE = "w360_session";

const report = { checks: [], csp: [], consoleErrors: [], apiErrors: [] };
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 500) });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` - ${String(detail).slice(0, 220)}` : ""}`);
}

function watch(page) {
  page.on("console", (msg) => {
    const text = msg.text();
    if (/Content Security Policy|Refused to/i.test(text)) report.csp.push(text);
    // A refused sign-in answers 401: that one is the check's own doing.
    else if (msg.type() === "error" && !/Failed to load resource: the server responded with a status of 401/.test(text))
      report.consoleErrors.push(`${page.url()} ${text}`);
  });
  page.on("response", (res) => {
    if (res.url().includes("/api/") && res.status() >= 500) report.apiErrors.push(`${res.status()} ${res.request().method()} ${res.url()}`);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const settle = (page) => page.waitForNetworkIdle({ idleTime: 500, timeout: 20000 }).catch(() => {});
async function go(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle(page);
}
const text = (page) => page.evaluate(() => document.body.innerText);
const h1 = (page) => page.$$eval("h1", (els) => els.map((e) => e.textContent.trim()).join(" | "));
const noHorizontalScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
async function waitText(page, needle, timeout = 30000) {
  await page.waitForFunction((t) => document.body && document.body.innerText.includes(t), { timeout }, needle);
}
async function axe(page) {
  await page.addScriptTag({ content: AXE });
  return page.evaluate(async () => {
    const result = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return result.violations.map((v) => `${v.id}: ${v.nodes.length}`);
  });
}
async function fieldByLabel(page, label) {
  const id = await page.waitForFunction(
    (l) => {
      const lab = [...document.querySelectorAll("label")].find((x) => x.textContent.trim() === l);
      return lab ? lab.htmlFor : null;
    },
    { timeout: 30000 },
    label,
  );
  return `[id="${await id.jsonValue()}"]`;
}
async function typeInto(page, label, value) {
  const sel = await fieldByLabel(page, label);
  await page.click(sel, { clickCount: 3 });
  await page.type(sel, value);
}
async function clickByText(page, label) {
  const handle = await page.waitForFunction(
    (l) => [...document.querySelectorAll("button, a, label")].find((b) => b.textContent.trim() === l && !b.disabled),
    { timeout: 30000 },
    label,
  );
  await handle.asElement().click();
}
async function sessionCookie(page) {
  return (await page.cookies()).find((c) => c.name === SESSION_COOKIE);
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
  const shot = (page, name, fullPage = true) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage }).catch(() => {});
  try {
    // ======================================================= L: the sign-in page
    const ctx = await browser.createBrowserContext();
    const page = await ctx.newPage();
    watch(page);
    await go(page, `${PARTNER}/login`);
    await waitText(page, "Partner Login");
    check("L1 partner.: the sign-in is the Partner Login page", (await h1(page)) === "Partner Login", await h1(page));
    const body = await text(page);
    const story = [
      "Partner Portal",
      "Grow Your Business",
      "Promote, resell, manage customers and earn recurring income",
      "Promote Products",
      "Resell & Activate",
      "Manage Customers",
      "Earn Commissions",
      "Our Product Ecosystem",
      "OneClickAPI",
      "SailoAI",
      "VyniAI",
      "RCS / SMS",
      "Website",
      "and More",
    ];
    const missing = story.filter((t) => !body.includes(t));
    check("L2 the story: pill, headline, four features, the product family", missing.length === 0, missing.join(", "));
    const card = [
      "Access your partner dashboard to promote, resell, manage customers and track earnings.",
      "For affiliates, resellers and white-label partners",
      "Email Address",
      "Password",
      "Remember me",
      "Forgot Password?",
      "Login to Partner Portal",
      "New to WorkEasy360?",
      "Create Partner Account",
      "Partner Benefits",
      "Earn Commission",
      "Continue as Guest",
    ];
    const missingCard = card.filter((t) => !body.includes(t));
    check("L3 the card: badge, fields, Remember me, Forgot Password, sign-up, benefits", missingCard.length === 0, missingCard.join(", "));
    const storyText = await page.$eval("aside", (a) => a.innerText);
    check("L4 the story carries no figure (the laptop is decoration)", !/\d/.test(storyText.replaceAll("360", "")), storyText.slice(0, 200));
    const hrefs = await page.$$eval("a", (as) => Object.fromEntries(as.map((a) => [a.textContent.trim(), a.getAttribute("href")])));
    check(
      "L5 the links: sign-up, forgot password, guest preview",
      hrefs["Create Partner Account"] === "/partner/apply" &&
        hrefs["Forgot Password?"] === "/forgot-password?mode=partner" &&
        hrefs["Continue as Guest"] === "/partner/preview",
      JSON.stringify(hrefs),
    );
    check("L6 desktop (1440 px): no sideways scroll", await noHorizontalScroll(page));
    check("L7 axe clean on the sign-in", (await axe(page)).length === 0, JSON.stringify(await axe(page)));
    await shot(page, "L-desktop-1440", false);

    await page.setViewport({ width: 1672, height: 941 });
    await sleep(400);
    const fits = await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 2);
    check("L8 the reference image's size (1672 x 941): the whole page in one screen", fits, await page.evaluate(() => document.documentElement.scrollHeight));
    await shot(page, "L-reference-1672", false);

    await page.setViewport({ width: 1366, height: 768 });
    await sleep(400);
    await shot(page, "L-laptop-1366", false);

    for (const [w, h, name] of [
      [768, 1024, "tablet"],
      [390, 844, "phone"],
    ]) {
      await page.setViewport({ width: w, height: h });
      await sleep(400);
      const ok = (await noHorizontalScroll(page)) && (await page.$eval("h1", (e) => e.getBoundingClientRect().width > 0));
      check(`L9 ${name} (${w} px): no sideways scroll, the card shows`, ok);
      await shot(page, `L-${name}`);
    }
    await page.setViewport({ width: 1440, height: 900 });

    // A wrong password: the server's refusal, said plainly.
    await typeInto(page, "Email Address", RESELLER);
    await typeInto(page, "Password", "not the password at all");
    await clickByText(page, "Login to Partner Portal");
    await waitText(page, "That email or password isn't right.");
    check("L10 a wrong password says so, and the page stays", page.url().startsWith(`${PARTNER}/login`));

    // Remember me ticked: a persistent cookie for the remember window.
    await typeInto(page, "Password", PASSWORD);
    await clickByText(page, "Remember me");
    await clickByText(page, "Login to Partner Portal");
    await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
    await settle(page);
    const remembered = await sessionCookie(page);
    const days = remembered && remembered.expires > 0 ? (remembered.expires - Date.now() / 1000) / 86400 : 0;
    check("L11 signs in with Remember me and opens the partner portal", new URL(page.url()).pathname.startsWith("/partner"), page.url());
    check("L12 Remember me ticked: the session cookie lasts about 7 days", days > 6.9 && days < 7.1, `${days.toFixed(2)} days`);
    await ctx.close();

    // Unticked: the cookie ends with the browser.
    const ctx2 = await browser.createBrowserContext();
    const page2 = await ctx2.newPage();
    watch(page2);
    await go(page2, `${PARTNER}/login`);
    await typeInto(page2, "Email Address", RESELLER);
    await typeInto(page2, "Password", PASSWORD);
    await clickByText(page2, "Login to Partner Portal");
    await page2.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 90000 });
    const browserOnly = await sessionCookie(page2);
    check("L13 Remember me left unticked: the session cookie ends with the browser", browserOnly && browserOnly.session === true, JSON.stringify(browserOnly && { expires: browserOnly.expires, session: browserOnly.session }));
    await ctx2.close();

    // ======================================================= S: the sign-up page
    const ctx3 = await browser.createBrowserContext();
    const page3 = await ctx3.newPage();
    watch(page3);
    await go(page3, `${PARTNER}/login`);
    await clickByText(page3, "Create Partner Account");
    await page3.waitForFunction(() => location.pathname === "/partner/apply", { timeout: 30000 });
    await waitText(page3, "How do you want to partner?");
    check("S1 Create Partner Account opens the sign-up in the same frame", (await h1(page3)) === "Create Partner Account" && (await text(page3)).includes("Grow Your Business"), await h1(page3));
    check("S2 axe clean on the sign-up", (await axe(page3)).length === 0, JSON.stringify(await axe(page3)));
    await shot(page3, "S-signup-1440");
    await page3.setViewport({ width: 390, height: 844 });
    await sleep(400);
    check("S3 phone (390 px): no sideways scroll", await noHorizontalScroll(page3));
    await shot(page3, "S-signup-phone");
    await page3.setViewport({ width: 1440, height: 900 });

    await clickByText(page3, "Create Partner Account");
    await waitText(page3, "Enter your business name.");
    check("S4 an empty form is refused in the page", (await text(page3)).includes("Choose how you want to partner."));
    const stamp = Date.now();
    await typeInto(page3, "Business name", "Login Check Partners");
    await typeInto(page3, "First name", "Asha");
    await typeInto(page3, "Last name", "Rao");
    await typeInto(page3, "Email Address", `apply-${stamp}@login-check.dev`);
    await typeInto(page3, "Password", "a long unrelated pass phrase");
    await page3.select(await fieldByLabel(page3, "Country"), "IN");
    await clickByText(page3, "Reseller");
    await clickByText(page3, "Create Partner Account");
    await waitText(page3, "Check your email");
    check("S5 sending it says only to check the email", (await text(page3)).includes(`apply-${stamp}@login-check.dev`));
    await shot(page3, "S-sent");
    await ctx3.close();

    // ======================================================= P: the other pages
    const ctx4 = await browser.createBrowserContext();
    const page4 = await ctx4.newPage();
    watch(page4);
    await go(page4, `${PARTNER}/forgot-password?mode=partner`);
    await waitText(page4, "Reset your password");
    check("P1 the partner's forgotten-password page wears the Partner frame", (await text(page4)).includes("Grow Your Business"));
    check("P2 axe clean on it", (await axe(page4)).length === 0, JSON.stringify(await axe(page4)));
    await shot(page4, "P-forgot", false);
    await go(page4, `${ADMIN}/login`);
    await waitText(page4, "Admin Login");
    check("P3 admin.: still the Admin Login", (await h1(page4)) === "Admin Login" && !(await text(page4)).includes("Partner Benefits"));
    await go(page4, `${APP}/login`);
    await waitText(page4, "Sign in as User");
    check("P4 app.: still the customer sign-in", !(await text(page4)).includes("Partner Login"));
    await ctx4.close();

    check("No CSP violation", report.csp.length === 0, report.csp.join(" | "));
    check("No console error", report.consoleErrors.length === 0, report.consoleErrors.join(" | "));
    check("No API 5xx", report.apiErrors.length === 0, report.apiErrors.join(" | "));
  } catch (err) {
    check("the run itself", false, err && err.stack);
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
    const failed = report.checks.filter((c) => !c.ok);
    console.log(`\n${report.checks.length - failed.length} / ${report.checks.length} passed`);
    process.exitCode = failed.length ? 1 : 0;
  }
})();
