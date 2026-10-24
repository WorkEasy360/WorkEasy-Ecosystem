# Partner portal final menu: status

Date: 2026-10-16. Decision: [ADR-046](../architecture/adrs/ADR-046-partner-portal-final-menu.md). Browser check: [scripts/partner_panel_browser_check](../../scripts/partner_panel_browser_check/README.md). Not committed, not pushed, not deployed.

## Result

**Does the partner portal now expose only the approved Partner Panel options, with the previous extra navigation consolidated or removed, while preserving shared backend functionality and the Workeasy360 security architecture? YES, with the stated limits below.**

## Final sidebar (24 entries, 8 groups)

```
MENU                    Dashboards
AUDIENCE                Users | Agency | Affiliates
BILLING                 Plans & Add-ons | Currency & Pricing | Payments | Product Billing
PANEL CUSTOMIZATION     Brand Settings | Product Manager | Appearance | Communication | Integrations
NEW PRODUCT SETTINGS    Partner Products | Auth Configuration | Shared Resources
SETTINGS                Account | Channel Manager | API Key Manager | Translation Manager
TEAM                    Role & Permissions | Team Members
HELP                    Support Manager | Notification Center
```

Previous menu: **31** entries. Final: **24**. The list is a maximum: an entry shows only when the server resolved a permission that makes its page useful.

## Where everything went

| Was | Now |
|---|---|
| Customers, Subscriptions | Users (tabs: Customers, Subscriptions) |
| Sub-Partners | Agency (still `PartnerHierarchyLink` over `PartnerAccount`) |
| Affiliates, Referrals | Affiliates (Overview, Conversions, Payouts, Settings) |
| Products | Partner Products (read-only grants); the sale switch is Product Billing, order and visibility is Product Manager |
| Pricing, Currencies | Currency & Pricing (tabs: Currency, Package & Pricing, Pricing Rule) |
| Commissions, Partner Balance, Orders, Payments | Payments (tabs: Orders, Commissions, Balance) |
| Payouts | Affiliates > Payouts |
| Payment Links | removed: it was a statement that none exists; Payments says so |
| Branding, Domains, Set up white-label | Brand Settings (tabs: Branding, URL Manager, White-label setup) |
| Channels, Templates, Alerts, Logs | Communication (tabs) |
| Authentication | Auth Configuration |
| Users & Permissions | Role & Permissions (new, read-only) + Team Members |
| Settings | Account |
| Activity | the trail on Account; newest entries as notifications; Notification Center |

**Removed as top-level sidebar items (19 of the 31):** Customers, Subscriptions, Sub-Partners, Products, Pricing, Currencies, Commissions, Partner Balance, Orders, Payouts, Payment Links, Domains, Activity, Set up white-label, Authentication, Users & Permissions, Settings, and the four Communication entries (Channels, Templates, Alerts, Logs). Each is a tab, a renamed page or merged as shown above. No backend route, model, table or data was removed.

**Redirects:** 22 old addresses (`/customers`, `/subscriptions`, `/sub-partners`, `/referrals`, `/commissions`, `/payouts`, `/balance`, `/orders`, `/payment-links`, `/currencies`, `/pricing`, `/pricing/currencies`, `/branding`, `/domains`, `/white-label`, `/products`, `/authentication`, `/settings`, `/activity`, `/communication`, `/communication/channels`, `/support`) answer 308 to their new page, query kept. Verified in a real browser.

## Item by item

1. **Dashboards.** Existing dashboard kept; added product activity and unread notifications. No invented metric.
2. **Users.** Same provisioning and relationship services; server-side search over live relationships (an ended relationship's name cannot be found); the owner's address is still never shown.
3. **Agency.** Same hierarchy: list, add, activate, suspend, resume, products and capability subset; a parent can only hand on what it holds.
4. **Affiliates.** Overview, Conversions, Payouts, Settings over the existing referral, commission and payout code. Settings shows window, eligible products, rules naming the partner, payout currency; the rates and window are Workeasy360's. No visitor PII.
5. **Plans & Add-ons.** Plans read; Add-ons is an honest statement (no purchasable add-on exists).
6. **Currency & Pricing.** Currency registry (closed enum, integer minor units); packages and fixed prices read-only. Partners cannot change prices; all arithmetic is server-side.
7. **Payments.** Customer orders, commission, balance. States what is not offered: no recharge, no payment links, no card or bank data.
8. **Product Billing.** Granted products, status, partner and list price, trial, subscriptions, the sale switch; a future product appears automatically.
9. **Brand Settings.** Branding and URL Manager reuse `PartnerBrand` and `PartnerDomain`; DNS proof untouched (D-035 not weakened).
10. **Product Manager.** Order and visibility, plus a customer-catalog preview. Never touches entitlement.
11. **Appearance.** Six colour schemes plus "Advanced theme tokens" (six validated hex colours with a contrast gate) in place of custom CSS. No custom CSS or script.
12. **Communication.** Channel, Templates, Alerts, Logs as tabs; only email exists; the rest are explained, not faked.
13. **Integrations.** Statement: connectors belong to customer organizations; nothing executable, no outbound calls.
14. **Partner Products.** Granted vs available, sell access, pricing, trial. Read-only; a partner cannot grant itself a product.
15. **Auth Configuration.** A statement of what is Workeasy360's and what is the partner's (its brand on the sign-in page). No partner-settable policy: a default destination, a stricter MFA rule, session length and SSO mode would each change security for people who belong to their own organization, so none is offered.
16. **Shared Resources.** Honest "not configured": the files platform is organization-scoped.
17. **Account.** Existing lean profile, plus the activity trail.
18. **Channel Manager.** States: email connected / needs setup; webhook, WhatsApp, SMS not available. No secrets.
19. **API Key Manager.** New. Scoped (`account.read`, `customers.read`, `commissions.read`), expiring (30-365 days), hash-only, secret shown once and never logged or audited. Create, rotate, revoke need AAL 2 (a dangerous permission) and are rate-limited and audited. A key can carry only what its creator's role already holds, is re-checked on every use, and dies with its expiry, its partner's ability to transact or its creator's membership. The read-only `/partner-api/` accepts GET only and ignores the session.
20. **Translation Manager.** New. Closed key and locale lists, one line of plain text (no markup, braces or code), the fall-back partner -> Workeasy360 -> English. The customer portal applies the welcome message and the footer text; the other stored strings (navigation labels, support text, email copy) are stored and validated but not yet applied by any customer screen or email.
21. **Role & Permissions.** New, read-only: the seven presets as intersected with the partner's capabilities. No custom roles.
22. **Team Members.** Invite, change role, resend, suspend and resume (new), remove. Removal and suspension end partner access on the next request and never touch the Workeasy360 identity.
23. **Support Manager.** Contact address (`W360_PARTNER_SUPPORT_EMAIL`), Easy Help Desk (`W360_PARTNER_HELP_DESK_PRODUCT`), and a form that prepares an email in the person's own mail app. No ticket list: the platform has no ticket model. Route is `/support-manager` (a frontend folder named after a product code is refused by an ADR-017 architecture test); `/support` redirects.
24. **Notification Center.** Unread, All, Important; mark read, mark all read. It is the audit log, worded and filtered to what the person's permissions allow; only read-state is stored. A customer's trial ending, a subscription starting and card payment results are not recorded against the partner, so they are listed as not recorded.

## Evidence

| Gate | Result |
|---|---|
| New backend tests (`backend/tests/partner_menu/`) | 119 (API keys 36, translations 35, notifications 19, team/roles/help 21, architecture 8) |
| Full backend suite, bare `pytest`, final tree | **4,516 passed, 0 failed, 0 skipped** (1 h 24 min). An earlier full run on a slightly earlier tree had 3 failures (a menu-architecture regex, a hard-coded product code in the Support Manager, a pinned brand payload); they were fixed and the whole suite re-run |
| ruff, ruff format | clean |
| mypy, lint-imports, bandit (medium+), pip-audit, `manage.py check`, `makemigrations --check` | clean / 18 kept / 0 / none / no issues / no changes |
| Frontend (clean `npm ci` in a scratch copy): typecheck, eslint, vitest, `next build`, dynamic-rendering assertion | 0 / 0 / 836 tests pass / clean / pass |
| npm audit | runtime 0; dev-only 5 high in the accepted `braces` chain (unchanged) |
| Real browser (production build, real backend) | **21/21 journeys**; 41 pages: axe 0, CSP 0, console errors 0; reflow 185/185 at 1440, 1024, 768, 390, 320 px |
| Manual screen reader | **NOT EXECUTED** |

## Security tests run

Customer to partner portal (404); Partner A to B (every read and write 404, keys, translations, notifications, members); sub-partner to parent (404, from the earlier panel suite, still green); API-key escalation (a scope the creator lacks is refused; rotation re-checks; demotion narrows a live key); role escalation (nobody changes their own role; only an owner touches an owner); product grant bypass (Partner Products has no write control; grant stays staff-only); pricing manipulation (prices read-only, no amount accepted from the browser); commission leakage (finance cannot read customers; a key without the scope gets 403); domain takeover (the URL Manager is the existing DNS-proof flow, unchanged); notification leakage (only permitted kinds; another partner's events never appear; ids the person cannot see are ignored); translation XSS (markup, braces, template syntax, control characters all 400; hostile stored values render as text); brand XSS (existing tests, theme tokens accept only six-digit hex); CSRF (key creation without a token is 403; the key API is exempt only because it ignores cookies and is enumerated for the architecture test); IDOR (key, member, notification and customer ids of another partner).

## Open items

- **P2 (yours): migrate the dev database.** `partners.0006` is not applied to `workeasy360`; until it is, the new screens fail on your dev backend (8010). Needs your approval and a backup, as before.
- **P2:** the customer portal applies only the welcome message and the footer from the Translation Manager; applying navigation labels, support text and email copy is a follow-up.
- **P2:** the 20 per-partner NOT-IMPLEMENTED items inherited from ADR-045 remain (add-ons, recharge, payment links, partner SMTP, WhatsApp/SMS/webhook, alerts and logs, per-partner templates, partner integrations, shared resources, custom roles, a ticket list, partner-settable authentication policy).
- **P3:** manual screen-reader pass; the `braces` dev-only advisory.
- Go-live of the portal hosts is unchanged (`docs/portals/status.md`).
