# Known debt and deliberate omissions

Registered debt lives in
[the debt register](../architecture/debt-register.md); this page adds what is
*not built* and why. Nothing here is hidden behind a screen that pretends
otherwise.

## Registered

| ID | What | Trigger to close |
|---|---|---|
| D-019 | Five control-plane tables are exempt from tenant-scoped modelling | Never "closed"; re-justify at review. A sixth needs an entry and fails an architecture test otherwise |
| D-020 | A partner-provisioned owner (new address) has no way to set a credential - **P1 go-live gate** for the reseller new-customer journey (final audit) | The credential-claim flow in [11-final-opus-audit.md](11-final-opus-audit.md) (the email channel already exists) |
| D-021 | Staff cannot change a customer's bundle | A real support need; call the existing service under a staff context |
| D-022 | Two HTTP surfaces (Phase 2 `/orgs/<slug>/commercial/*` and Phase 7 `/admin/catalog/*`) over one catalog implementation | Retire the Phase 2 routes with their tests when no caller uses them |

## Not built, on purpose

| Omission | Why | Where the seam is |
|---|---|---|
| Real payment / payout execution | Not approved; regulated activity. The brief forbids it | `paid` in `commissions/payouts.py` - a provider event would be a new way to reach it |
| Dual approval of payouts | Manual, low volume; separation of duties + AAL 2 + audit suffice (see ADR-029 for the triggers to adopt it) | `approve` in the same module |
| Recurring-revenue (MRR) on the dashboard | No invoicing data exists; a number would be invented | The dashboard says so explicitly (`mrr.available = false`) |
| Real-time customer summary | The directory reads a projection so a page is one indexed query; it is labelled "as of last reconciliation" | `provisioning.CustomerSummary` |
| Partner-visible invoices | Partners see subscription state and their own price; the only billing action is opening a billing account (currency) for a linked customer. No invoices exist yet | `partner-customer-billing` |
| Partner-defined commission rules | Rules are staff-set; a partner cannot negotiate their own rate | `commissions.rules` |
| Multi-level (sub-partner) commission | One partner per customer at a time | Link model allows extension |
| Refund/chargeback ingestion | No payment provider; staff record reversals by hand with a reason from a closed set | `commissions.ledger.reverse` |
| Automatic payout scheduling | Payouts are prepared by a person after reviewing | - |
| Custom-domain TLS provisioning | Serving a partner domain (routing, certificates) is infrastructure, not this repository; Phase 7 owns ownership proof and brand resolution only | `partners.domains` (state) and `public/branding` |
| Indexed customer search | `icontains` on name and slug is an unindexed scan, bounded by the search-length cap and keyset paging; fine at current scale. Add a `pg_trgm` index past ~50k organizations (measured in [07-performance.md](07-performance.md)) | `organizations_directory_page` |
| Browser-level accessibility checks | Contrast, focus and reflow need a real browser | Manual checklist in [06-frontend.md](06-frontend.md) |
| Legacy `/orgs/<slug>/commercial/*` catalog routes | Phase 2 organization-scoped catalog/pricing routes remain; the control plane is the supported write path. Retiring them changes Phase 2 tests (D-022) | `interfaces/api/v1/commercial_views.py` |
| Orphaned logo uploads are not swept | Small, unreachable once unattached; see [10-retention-and-privacy.md](10-retention-and-privacy.md) | `partners.PartnerLogoUpload` |

## Watch list

- **Capability sprawl** (ADR-028): review the registry whenever a capability is
  proposed.
- **Ledger growth**: `commissions_accrual` grows forever by design. Partition by
  `event_at` before it reaches tens of millions of rows; the keyset indexes
  already lead with the partner and time.
- **Referral table**: unconverted referrals are pruned by
  `commissions-prune-referrals`; converted ones are retained.
- **DNS dependence**: a *failed* lookup (timeout, SERVFAIL) is never counted as a
  miss - an outage of the resolver must not disable every partner's domain -
  while a lookup that answers without the proof (NXDOMAIN, record removed)
  counts, and three consecutive misses disable the domain (corrected by the
  final audit; tested against a real DNS server in
  `tests/phase7/test_dns_resolver_integration.py`).
