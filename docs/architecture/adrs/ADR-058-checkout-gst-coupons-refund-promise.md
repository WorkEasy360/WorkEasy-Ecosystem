# ADR-058: Store checkout - GST on INR invoices, coupons, and a refund promise per product

Date: 2026-11-05. Status: accepted (built locally, not deployed).

## Context

The owner sent the checkout mock-up for a product page a third time (OneClickAPI): plan choice, quantity, a coupon field, an order summary with a "GST (18%)" line and "Total Payable", and a "60-Day Refund Policy" badge. Until now the platform had:

- no tax engine. `tax_minor` was always `None`, and the page showed "GST: As applicable".
- no coupon model.
- no refund promise.

The owner decided (2026-11-05):

- **GST:** "Add 18% GST everywhere". The product page, the checkout, the payment and the invoice all add 18% to INR prices.
- **Coupons:** build full coupons. The Super Admin creates codes, and the checkout applies them to the real payment.
- **Refund promise:** "refund wala admin krega set". The Super Admin sets it per product.

## Decision

1. **GST is its own invoice line** (`billing/tax.py`, `InvoiceLine.kind = "tax"`).
   - **Where it applies:** every INR invoice. That means an order's invoice (`payments.orders._finish`) and every period renewal (`payments.invoicing._issue`). Other currencies carry no tax line.
   - **What it is charged on:** what is owed now. That is the period's price (or its prorated rest), less the coupon, plus any set-up fee.
   - **Rounding:** to whole rupees, half up. ₹1,511.82 becomes ₹1,512, so ₹8,399 + ₹1,512 = ₹9,911, as in the mock-up.
   - **The rate** is `settings.GST_RATE_BP` (env `W360_GST_RATE_BP`, default 1800). 0 turns GST off.
   - The product page estimates GST with the same rule. The quote is the amount charged.
2. **Only charges earn commission.** `InvoiceLine.kind` is `charge`, `setup_fee` or `tax`. `_link_invoice` links only `charge` lines to a subscription line. So `paid_item_lines`, which is what commission reads, never includes GST or a set-up fee. Existing rows default to `charge`, and existing set-up-fee lines stay unlinked as before.
3. **Coupons live in pricing** (`pricing.Coupon`, `pricing.CouponRedemption`, `pricing/coupons.py`).
   - **The discount:** percent (1-99%) or a fixed amount in one currency. It can be limited to some products, a start and end, a total use limit, and once per customer.
   - **Where it applies:** to the **first payment's charge only**, never to a set-up fee or a renewal. It never takes more than 99% off, so a coupon cannot turn a paid order into an unpaid one, which would skip the pay-first path.
   - **Who prices it:** the browser sends only `coupon_code`, which is on the order allowlist (customer and storefront orders only, not partner-placed orders). The server prices it in `_quote`.
   - **Counting uses:** `reserve` records the use in the order's own transaction, with the coupon row locked. `release` gives it back when the order is cancelled, expires, is voided or fails after payment.
   - `CouponRedemption` is a control-plane table, exempt from W360.E001 like `OrderSummary`. The reason: a use limit counts every customer's orders at once.
   - **The product page's "Apply"** is `GET public/store/products/<slug>/coupon`. It is a preview that cannot see who is buying. It is rate-limited to 20 checks per 10 minutes per address, so codes cannot be guessed by trying many.
4. **Refund promise:** `Product.refund_days` (1-365, empty means none).
   - The Super Admin sets it in Product Manager > Store merchandising.
   - The product page shows "N-Day Refund Policy" only when it is set.
   - It is presentation only. Refunds themselves stay with staff (`payments.refunds`).
5. **Super Admin > Store > Coupons** lists, creates, disables and enables codes. Writes need `platform.staff_pricing.manage`; the list needs `platform.staff_catalog.view`. A code and its discount never change. A different discount is a new code.

## Consequences

- Revenue figures that sum `amount_due_minor` or collected amounts (Store overview, collections) now include GST on INR orders. They show gross amounts collected, not net revenue.
- Partner upgrade fees (`PartnerFeePayment`) are not invoices and carry no GST. That is left for the owner to decide.
- All prices are treated as **exclusive** of GST. `PlanPrice.tax_behaviour` is always `exclusive`, and nothing in the product sets `inclusive`.
- Migrations: `catalog.0011` (one nullable column), `payments.0006` (three order columns and `InvoiceLine.kind`, all with database defaults) and `pricing.0005` (two new tables). All are overlap-safe.
