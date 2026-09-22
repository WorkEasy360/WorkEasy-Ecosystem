# Phase 2 — Pricing

**Date:** 2026-09-21

---

## 1. The invariant

> **A historical price never changes.**

If today's CRM Starter price moves from 1499 to 1699, then tomorrow:

- the existing subscription item still says 1499
- `resolve_price(at=yesterday)` still returns 1499
- the 1499 row is still there, merely closed

All three are asserted by
`test_a_price_rise_does_not_rewrite_history`.

## 2. Model

```python
PriceBook(code, name, channel, region, currency, priority,
          valid_from, valid_to, is_active)                    # control plane
PlanPrice(price_book, plan_version | bundle_version, interval, model,
          amount_minor, min_quantity, max_quantity, tax_behaviour,
          valid_from, valid_to, published_at)                 # control plane
PriceTier(plan_price, up_to, unit_amount_minor, flat_amount_minor)
ContractPrice(organization, plan_version | bundle_version, interval, model,
              amount_minor, currency, tax_behaviour, valid_from, valid_to,
              contract_ref, approved_by)                      # data plane, RLS
```

### Segmentation is rows, not columns

A price book is a named set of prices for one segment, along
`channel × region × currency × time`. Adding a market, a channel or a partner
tier is **new rows** — never a new column and never a code change. That is
the mechanism that makes "future pricing strategies" cheap.

The four use cases named in the brief are all price book codes:

| Use case | How |
|---|---|
| `DIRECT_INDIA` | `channel=direct`, `region=IN`, `currency=INR` |
| `DIRECT_US` | `channel=direct`, `region=US`, `currency=USD` |
| `RESELLER_GOLD` | `channel=partner`, a code — **not selectable yet**, see §5 |
| `ENTERPRISE_CUSTOM` | a `ContractPrice` for that organization |

### `PlanPrice` has no currency column

The price book has exactly one currency. A currency on the price would be a
second copy that can disagree with it, and a price whose currency disagrees
with its book is a billing incident waiting for a customer to notice. It is
read through `price_book.currency`, which is the single source.

This departs from the Phase 0 sketch, which lists `currency` on `PlanPrice`.
Removing a field that can only ever hold a duplicate of another field is a
strengthening. `test_a_price_has_no_currency_of_its_own` pins it.

`ContractPrice` does carry a currency, because it has no book.

### Extensions to the Phase 0 model, and why

| Added | Reason |
|---|---|
| `PlanPrice.valid_from` / `valid_to` | Task 5 requires valid-from/valid-to and Task 6 requires versioned pricing. The Phase 0 sketch puts the window on the book only, which would make a single price change require a whole new price book |
| `PlanPrice.published_at` | The immutability trigger needs a publication marker, exactly as `PlanVersion` does |

Both are additive within the approved model. Neither changes a documented
behaviour.

## 3. Resolution

```python
resolve_price(*, plan_version_id | bundle_version_id, organization_id,
              currency, interval, quantity, region, at) -> PriceQuote
```

One function. No other code in the system computes a price.

Order, first match wins:

1. `ContractPrice` for this organization, valid at `at`
2. **Partner price book — not implemented.** See §5
3. Regional price book, `channel=direct`, matching region + currency
4. Default price book, `channel=direct` + currency
5. **Raise `PriceNotAvailable`**

Step 5 is the one that matters. Never a fallback currency, never a fallback
region, never zero. A silent fallback bills someone incorrectly and nobody
finds out until a customer complains; a raised error is a catalog defect that
surfaces at checkout, where it can still be fixed.
`test_a_missing_price_raises_rather_than_falling_back`.

### The quote records why it was chosen

`PriceQuote` carries `price_id`, `price_source` and `reason` in words, and
all three are pinned onto the subscription item. "Why is this customer paying
this?" is answerable in production from one row, rather than by re-deriving
resolution against a catalog that has since moved on.

## 4. Price models

| Model | Computation |
|---|---|
| `flat` | the amount |
| `per_seat` | amount × quantity |
| `tiered` | **graduated** — each band at its own rate |
| `volume` | **all** units at the rate of the band reached |
| `usage` | refused at publish time, see below |

`tiered` and `volume` are routinely confused and are genuinely different;
conflating them produces invoices customers dispute. Both have a test with
the same inputs and different expected totals.

Tiers with a gap are refused rather than silently undercharging for the
units nobody priced: `test_tiers_with_a_gap_are_refused`.

**`usage` cannot be published.** Charging for usage requires a metering
pipeline that does not exist. A usage price would resolve to an amount nobody
can compute, and returning zero would be an invoice that undercharges. The
publish guard raises; the enum value remains so the model is declared rather
than invented later.

## 5. Partner price books are declared but not selectable

`channel=partner` exists as a segmentation axis and a price book with
`code=reseller_gold` can be created. **Resolution never selects one.**

A partner book must only apply to a customer with an active partner
relationship, and `commercial/partners` is a later phase. Selecting one by
region alone would hand reseller pricing to direct customers — a revenue leak
rather than a missing feature.

The slot is documented in the resolution order rather than stubbed, and
`test_partner_books_are_not_selectable_without_a_relationship` creates a
cheaper partner price and asserts a direct customer still pays list.

## 6. How a price changes

Two writes, not one:

```python
publish_price(..., amount_minor=1699)   # closes 1499 with valid_to, opens 1699
```

The old row is **closed**, never edited. Both facts survive, and
`resolve_price(at=...)` returns whichever was in force.

### Backdating is refused

A supersession with `valid_from` in the past would retroactively change what
a past date resolves to, which is the one thing versioned pricing exists to
prevent: an invoice raised last week would no longer be reproducible.

Establishing a *first* price with a historical start is permitted, because
there is no prior fact to contradict.
`test_backdating_a_price_change_is_refused`.

### Published prices are immutable at the database

The same trigger mechanism as plan versions, over
`price_book_id, plan_version_id, bundle_version_id, interval, model,
amount_minor, min_quantity, max_quantity, tax_behaviour, valid_from`.
`valid_to` is excluded because closing is how supersession is recorded.
`PriceTier` rows are frozen once their parent price is published.

`test_a_published_price_cannot_be_edited_at_the_database` writes with
`QuerySet.update()` and asserts the amount is unchanged.

## 7. Currency safety

| Control | Where |
|---|---|
| No float or decimal columns anywhere in pricing | `test_no_float_columns_exist_on_any_pricing_model` |
| `amount_minor` must be `int`, and `bool` is excluded | service guard + test |
| `Money` refuses cross-currency arithmetic | `contracts.values.money`, Phase 1 |
| Currency is fixed per organization by `BillingAccount` | `billing/models.py` |
| A subscription's currency is fixed at creation | `Subscription.currency` |
| A resolved quote in another currency is refused | `_quote_for` in subscriptions |
| A contract price in another currency raises | `test_cross_currency_contract_price_is_refused_explicitly` |
| A client-supplied currency is ignored | `test_a_purchase_cannot_be_made_in_an_arbitrary_currency` |

**Unsupported FX fails closed.** There is no conversion anywhere. A
conversion needs a rate, a rate needs a date and a source, and silently
choosing one produces numbers nobody can reconcile later. If an organization
bills in INR and only a GBP price exists, the purchase is **refused** —
not priced in GBP.

## 8. Tax

`tax_behaviour` records whether an amount is tax-inclusive. No rate is stored
anywhere in the catalog: a catalog that embeds VAT rates is wrong within a
quarter. Calculation belongs at invoicing time, which is a later phase.
