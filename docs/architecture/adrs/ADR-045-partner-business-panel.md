# ADR-045: The partner business panel

Status: accepted, 2026-10-16. Builds on ADR-028 (the partner channel), ADR-041/042 (the Control Center and the setup profile) and ADR-043 (the portal hosts).

## Context

A partner could already sell, earn, brand and sign in (Phases 7, 10, the Control Center). What it lacked was a panel to *run the business*: a dashboard, its own team, sub-partners, its products as it presents them, a balance, an activity trail, a way to change its own business details. The brief also named several things the platform has no model for (a wholesale cost, a customer limit, prepaid credit, payment links, partner SMTP, add-ons, custom CSS).

## Decisions

1. **One `PartnerAccount`, still.** No reseller, white-label, affiliate, agency or partner-user model. The panel's navigation is drawn from the permissions the server resolves (`partners_for_user` returns them); labels stay derived from capabilities.
2. **Roles are presets over capabilities** (`capabilities.ROLE_PRESETS`): owner, admin, sales, finance, support, marketing, viewer. A role can only *narrow* what the partner holds; an unknown stored role fails closed to viewer. Running the account itself (dashboard, profile, team, product list, activity) is a small **baseline** held by every member (view) or administrators (manage), reaching no customer, price or payout.
3. **Sub-partners are a hierarchy link, not a new kind of account.** `PartnerHierarchyLink(parent, child)` between two `PartnerAccount`s. The database refuses self-parenting and a second live parent. Depth is the policy constant `hierarchy.MAX_DEPTH = 1` (a sub-partner cannot have sub-partners); raising it is a policy change, not a migration. A parent creates a child in `draft`, equips it with only what it holds itself and only from `DELEGABLE_CAPABILITIES` (never commission, payouts, custom domains or the hierarchy), invites its administrator, activates, suspends and resumes it. Terminating, and anything money, stay with staff. A parent sees status and counts, never the child's customers, profile, payout contact or secrets; a child sees nothing of its parent.
4. **Presentation is stored; authority is not.** `PartnerProductSetting` (enabled for sale, show on Explore, show on My Products, order) can only narrow a `PartnerProductGrant`. Turning a product off for new sales ends nobody's subscription. White-label customers see the order and visibility; a product a customer holds is never hidden.
5. **The balance is the commission ledger.** No `balance` field exists. The ledger is append-only; the panel reads it by what can happen next (pending, available, in a payout, paid) per currency, never summed across currencies. Money on screen is integer minor units computed by the server; the browser formats and never adds.
6. **Honest gaps, in one registry.** `partner_portal.NOT_IMPLEMENTED` names, with a reason, every thing the brief asked for that the platform cannot honestly do. The screens read it by key and show the reason where the control would be; architecture tests pin that no endpoint exists for them and that every key a screen uses exists.
7. **Self-service for the partner's own account** goes through the *same* commands as staff, split into an authorizing wrapper and a core (`grant_capability`/`_grant_capability`, `invite_member`/`_invite_member`, ...). A partner user acts only on its own partner (anything else is the same 404), team commands are dangerous (second factor), an owner alone changes an owner, nobody changes or removes themselves, and an active partner keeps an administrator. A partner edits `business`, `contacts`, `support` of its profile; payout details and staff notes stay staff's.
8. **A temporary password is not a credential.** `User.password_change_required` (identity.0008) makes `PasswordChangeRequiredMiddleware` refuse every API call but the session, the hand-off, the password change and the partner list until the owner chooses their own password. `create_demo_partner` uses it.
9. **The demo partner exists only in development.** `manage.py create_demo_partner` refuses any environment but development and test (a missing setting fails closed), generates the password from the OS random source, shows it once and only on an interactive terminal (piped output is refused *before* anything is created), stores only its hash, and flags the account.
10. **A customer's owner address stays hidden.** The brief asked for an Owner/Email column; the Phase 10e privacy contract (a partner may know whether its customer's owner has activated, never who they are) is kept, in the list and the export, and pinned. Country and last activity do not exist as data.
11. **The commission rate becomes visible to the partner.** Task 66 of Phase 7 kept the margin arithmetic from partners. The brief asks for gross sales and commission earned side by side, and for the commission rules that name the partner, so a partner can now derive its effective rate. This is a deliberate, owner-requested change; the internal derivation (rule code of the programme default, attribution reference) is still not shown.

## What is deliberately not built

Partner cost, a customer limit, recharge and payment links, add-ons, partner-authored packages and pricing rules, sub-partner pricing and commission, partner SMTP (a partner-chosen host would let a partner make the platform connect anywhere), WhatsApp/SMS/webhook channels, alerts, per-partner templates and delivery logs, partner integrations, shared resources, a second logo and an icon, and custom CSS or script. Each has a reason in the registry. Remove an entry only when the capability is built and tested.

## Consequences

- Two migrations: `partners.0005` (two tables, a profile time zone, role choices) and `identity.0008` (one flag).
- `partners.services` gained cores and wrappers; the staff behaviour is unchanged (one pin, "staff may remove the last administrator", was kept deliberately).
- The panel adds no entitlement writer, price writer or ledger writer (pinned by `tests/partner_panel/test_architecture.py`).
