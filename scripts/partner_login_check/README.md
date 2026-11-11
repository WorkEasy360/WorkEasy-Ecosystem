# Real-browser check: the Partner sign-in and sign-up (2026-11-11)

What a real browser on `partner.workeasy360.com` shows of the owner's reference image for the Partner Login: the story panel (Partner Portal pill, "Grow Your Business with WorkEasy360", the four features, the laptop, Our Product Ecosystem), the card (Partner Portal badge, Email Address, Password with show / hide, Remember me, Forgot Password?, Login to Partner Portal, New to WorkEasy360?, Create Partner Account, Partner Benefits), the sign-up page it opens, and the partner's forgotten-password page. The **production build**, a **real backend** on the scratch database of [partner_admin_panel_check](../partner_admin_panel_check/README.md) (`w360_pap_fresh`), headless Chrome with `puppeteer-core` from a scratch directory. Not part of CI.

Checks (26):

- **L** (sign-in): every part of the reference image; no figure anywhere on the story panel (the laptop is an abstract drawing); the links; 1440, 1672 x 941 (the image's size: one screen), 1366, 768 and 390 px with no sideways scroll; a wrong password refused in words; Remember me ticked gives a 7-day session cookie, unticked a browser-session cookie; axe clean.
- **S** (sign-up): Create Partner Account opens `/partner/apply` in the same frame; the page refuses an empty form; a real application is accepted and the page says only to check the email; phone layout; axe clean.
- **P**: the partner's forgotten-password page wears the frame; `admin.` and `app.` keep their own sign-in pages.
- No CSP violation, no console error, no API 5xx.

```
# Windows, Git Bash, repository root. Ports 8011, 3999 and 8088 free.
# Prepare the database and start the backend, the production frontend and the edge
# exactly as scripts/partner_admin_panel_check/README.md says, then:
NODE_PATH=<scratch>/pptr/node_modules AXE_PATH=<axe.min.js> CHECK_PASSWORD=<same> \
  EDGE=127.0.0.1:8088 OUT=<dir> node scripts/partner_login_check/browser_check.js
python scripts/partner_admin_panel_check/prepare.py --drop
```

It signs in as the seed's reseller (`owner@pap-reseller.dev`) and sends one partner application with a fresh address each run, so it can run twice on one database.
