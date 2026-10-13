# One Workeasy360 login for every product: what this round changes

**Written for: engineers of Workeasy360 and of each product.**

Brief: "WORKEASY360 - ONE USER LOGIN FOR ALL PRODUCTS / UNIVERSAL PRODUCT SSO" (2026-10-12). It builds on everything in `design.md`, `completion-design.md` and ADR-044 (which already give each of the four products an OIDC client, per-person entitlement re-checks, trials, logout and the preference model). This round closes the two places where the experience still differed from the brief. Record: `status.md`.

## 1. The default preference is Automatic

ADR-044 made "Ask me each time" the default (that brief's recommendation). This brief recommends "Automatically use Workeasy360" for products that support SSO: click Open, the product opens. So:

- `contracts.values.sign_in.DEFAULT_PREFERENCE` is `auto_sso`. A person who never chose is signed in silently; "Reset all to default" means Automatic.
- All three choices stay: Automatically use Workeasy360, Ask me each time, Prefer the product's own sign-in (where the product has one). Nothing about the preference model, the chooser or its ordering changed (ADR-044 §4: applied after every access check, never grants access).
- People who stored a choice keep it.

This is one constant. Reverting it is a one-line change plus its tests.

## 2. Opening a product directly

Before: a person signed in to Workeasy360 who typed `https://book.workeasy360.com/` saw EasyBook's sign-in page and had to click "Continue with Workeasy360" (no password, but a step). The brief: no local session -> OIDC redirect -> the Workeasy360 session is reused -> the product opens signed in.

The rule every SSO-only product implements is the integration guide §6b (`docs/phase-3/09-product-integration-guide.md`):

1. A page navigation that needs a signed-in person, with no product session, goes straight to the product's own `/api/auth/workeasy360/start?next=<page>`.
2. Never automatically: on the product's sign-in page (including `?sso_error=`), after a sign-out of the product in this browser (a marker that holds until the next sign-in), or within 60 s of a previous automatic start that left no session (loop guard).
3. A session whose access ended keeps its explanation.
4. API calls are never redirected.
5. `next` is a validated same-site path.
6. Products whose own password sign-in is on are unchanged: their sign-in page offers both ways in.

On the Workeasy360 side, the authorization endpoint already reuses its session, applies the preference after every access check, and shows the central sign-in when there is no session. One change was needed, which the first browser run found: a product opened by typing its address names no organization. Workeasy360 used to send every such request to its organization chooser, even for a person with only one organization. Now a request that names no organization, from a person with exactly one, is for that organization, checked exactly like a named one. Someone in several organizations is still asked. See ADR-044 item 16 and `test_one_login_guards.py`.

Easy Project's registered post-logout address is its root (`https://ops.workeasy360.com/`); its sign-out marker is what keeps "Sign out of Easy Project and Workeasy360" from bouncing straight back to Workeasy360's sign-in page.

## 3. What is proven, and how

The browser check (`scripts/universal_sso_browser_check/`) adds:

- **14 DIRECT** (the brief's journey B): signed in to Workeasy360 only, the person types each product's address. Each product starts the flow itself, Workeasy360 reuses its session, and the product opens signed in with no password and no sign-in page. The negative cases are checked too: after a product sign-out, the product shows its sign-in page and does not sign the person back in; with no Workeasy360 session, the central sign-in appears and the product opens after it. Easy CRM is checked in its production mode (SSO-only) in a second pass. The first pass keeps it `sso_optional` for the journeys about its own sign-in, and there its direct visit shows both ways in.
- **15 CROSS-ORG**: a person who opens a product for another organization's id is refused. Two organizations' product sessions are different product organizations.
- Journeys 7 (account switch) and 8 (logout) now cover all four products, EasyBook included.
- Journey 2 starts from an explicit "Ask me each time", because Automatic is now the default.

Architecture guards (`backend/tests/universal_sso/test_one_login_guards.py`) pin the brief's list in one place.

## 4. Limits, stated plainly

These come from the independent audits of the four products. None of them grants access to anyone, but each can surprise a person.

- **"Sign out of <product>" lasts one browser session.** The sign-out marker is a browser-session cookie, but the Workeasy360 session lasts up to 8 hours. Signing out of a product only and then restarting the browser means the next visit to that product signs the person in again silently. To end both, use "Sign out of <product> and Workeasy360".
- **A product's own "sign out every device" does not lock anyone out.** It ends the product's sessions. A device still signed in to Workeasy360 signs straight back in on its next visit, which is what single sign-on means. To stop a person, suspend or remove them, or unassign their seat, in Workeasy360. Every product then refuses them within the re-check window (journey 12).
- **The marker is a convenience, not a control.** A link from another site to a product's start endpoint can start a sign-in. That sign-in is always for the person's own account and only completes with their live Workeasy360 session, after every access check.
- **The launcher always starts a sign-in.** `?organization=<uuid>` starts one even on a product's sign-in page and even after a sign-out (contract §6a): the person just picked that organization in Workeasy360.
- **Several tabs opened at once with no session:** one sign-in completes, and the others may show "the sign-in expired". Reloading those tabs opens them. The loop guard is per tab in Easy CRM and Easy Project (`sessionStorage`) and per browser in EasyBook and Easy Help Desk (a 60-second cookie). Either way it stops a loop, which can only happen inside one tab.
- **Signing out of Workeasy360** (other than through a product's "…and Workeasy360") does not end product sessions. There is no front- or back-channel logout (ADR-044). Removal, suspension, a disabled account or an unassigned seat ends them within the re-check window.
