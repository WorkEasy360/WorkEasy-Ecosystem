# Admin Panel programme: status

Branch `feat/admin-panel`, based on the live release `b2eb178`. Not deployed. Decisions: `docs/architecture/adrs/ADR-060-admin-panel-wallets-kyc-and-module-map.md`. Plan and audit: `00-audit-and-plan.md`. How to build a screen: `ui-guide.md`. Browser check: `scripts/admin_panel_check/README.md`.

## Decisions the owner should know about

The owner decided two things on 2026-11-09: build from the brief, because `08_Credits_Wallets_System.txt` and `07_Product_Web_Mobile_Flow.txt` are not on this machine, and add a public partner application form.

Defaults chosen where the brief was silent. Each is easy to change:

| Default | Why |
|---|---|
| Partners already active stay active, with KYC in draft | Marking them approved would forge a review. The KYC gate applies only to draft to active |
| "Pending Setup" is a separate setup state, not a subscription status | The brief keeps payment, subscription, setup and member access separate |
| The UI says "Admin"; the internal key stays `SUPER_ADMIN` | Renaming the key would touch every grant for no gain |
| Partner commission views say "Earnings" | The brief: commissions are earnings records, not a fourth wallet |
| Old Admin addresses keep working | Nothing bookmarked breaks |
| A plan set to renew by hand and one set not to renew behave alike | Both end with the paid period and can be bought again. The brief names the rules but not a difference |
| On upgrade, security administrators receive the six new permissions; holders of narrower roles do not | Otherwise the live Admin would meet a 403 on the new screens. A Billing or Support Admin who needs wallets, KYC or reports is granted them by an Admin, with a reason |

## Questions for the owner

The two missing documents would probably have answered the first six.

1. Do AI credits expire?
2. Is there a minimum top-up?
3. Should a refund go back to the wallet or to the original payment method? Today a wallet payment refunds to the wallet and a card payment to the card.
4. Should a renewal be paid from the Platform Wallet automatically? Today it is not.
5. Are top-ups and credit packs taxed? Today no tax is charged on them.
6. What low-balance threshold should a new wallet start with? Today it has none, so no alert fires until an Admin sets one.
7. Should a suspended partner keep earning commission on new sales? Today it does (`commissions/accrual.py`).
8. Should a partner's Platform Wallet pay for anything? Today it receives offline top-ups only.
