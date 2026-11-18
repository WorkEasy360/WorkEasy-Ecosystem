"use client";

import { useState } from "react";
import Link from "next/link";
import {
  adminConsoleApi,
  INTERVAL_LABELS,
  type CatalogOverview,
  type CatalogPrice,
} from "@/lib/api/adminConsole";
import { ApiError } from "@/lib/api/client";
import { can, useAdmin } from "./ControlShell";
import { FormPopup } from "./Modal";
import { LoadGate, Table, money, useLoad } from "./ui";
import { DEFAULT_INTERVALS } from "./catalogUi";
import { PartnerProgramme } from "./PartnerProgramme";
import { SelectField, TextField } from "./widgets";
import { Alert, BoolBadge, ButtonLink, Card, EmptyState, Page, PageHeader, formatDate, sentence } from "./kit";
import { iconLabel, popupButton } from "./kit/PopupLabel";
import cp from "./ControlPlane.module.css";

/**
 * Super Admin → Currency & pricing (Phase 8 extension): the price books list
 * prices are published into, and a read-only view of every price in force.
 * Prices themselves are published from the plan builder, one per billing
 * cycle; a book is the (channel, currency, region) segment they belong to.
 */
/** `productCode` (the Product Manager's "Manage Pricing", ADR-051) narrows the
 * current prices to that product's plans and the suites that include it. */
export function PricingScreen({ productCode }: { productCode?: string } = {}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_pricing.manage");
  const [load, reload] = useLoad(() => adminConsoleApi.catalogOverview(), []);

  return (
    <Page>
      <PageHeader
        title="Currency & pricing"
        subtitle="Price books per currency, the prices in force, and partner margins."
        info="A price book holds the list prices for one currency (and optionally one region). Amounts are stored exactly in the currency's smallest unit (paise, cents) and are never converted between currencies."
      />
      <LoadGate load={load} what="price books">
        {(overview) => (
          <>
            <Card
              title="Price books"
              description="Where published prices live: one book per channel, currency and region."
              actions={manage ? <NewPriceBook currencies={overview.currencies} onCreated={reload} /> : undefined}
            >
              <Table
                caption="Price books"
                head={["Code", "Name", "Channel", "Currency", "Region", "Priority", "Active", "Valid from"]}
                numeric={["Priority"]}
                isEmpty={overview.price_books.length === 0}
                empty="No price books yet"
                emptyHint="Create one to publish prices."
              >
                {overview.price_books.map((b) => (
                  <tr key={b.id}>
                    <th scope="row" className={cp.mono}>
                      {b.code}
                    </th>
                    <td>{b.name}</td>
                    <td>{sentence(b.channel)}</td>
                    <td>{b.currency}</td>
                    <td>{b.region || "All regions"}</td>
                    <td className={cp.num}>{b.priority}</td>
                    <td>
                      <BoolBadge value={b.is_active} yes="Active" no="Inactive" noTone="neutral" />
                    </td>
                    <td>{formatDate(b.valid_from)}</td>
                  </tr>
                ))}
              </Table>
              {!manage && <p className={cp.muted}>Creating price books needs the pricing management permission.</p>}
            </Card>
            {productCode && (
              <Alert tone="info">
                Showing prices for {overview.products.find((p) => p.code === productCode)?.name ?? productCode} only.{" "}
                <Link href="/admin/pricing">Show all products</Link>
              </Alert>
            )}
            <CurrentPrices overview={overview} productCode={productCode} />
            <Card
              title="Partner set-up fees"
              description="What a partner pays to upgrade to Reseller or White-label is set on one page: Upgrade fees."
              actions={
                <ButtonLink href="/admin/partners/upgrade-fees" iconEnd="chevronRight">
                  Open Upgrade fees
                </ButtonLink>
              }
            >
              {null}
            </Card>
            <PartnerProgramme
              manage={can(permissions, "platform.staff_commission.manage")}
              manageFees={can(permissions, "platform.staff_partner.manage")}
              products={overview.products.map((p) => ({ code: p.code, name: p.name }))}
              fees={false}
            />
          </>
        )}
      </LoadGate>
    </Page>
  );
}

const BOOK_CODE = /^[a-z0-9][a-z0-9_-]{1,63}$/;
const REGION = /^[A-Za-z]{2}$/;

/** Before any call: the fields say what is wrong, the popup how many. */
function invalid(problems: Record<string, string>): ApiError {
  const list = Object.values(problems);
  return new ApiError(400, { detail: `Fix ${list.length === 1 ? "this" : `these ${list.length} things`} before saving: ${list.join(" ")}` });
}

function NewPriceBook({ currencies, onCreated }: { currencies: string[]; onCreated: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [currency, setCurrency] = useState(currencies[0] ?? "");
  const [region, setRegion] = useState("");
  const [priority, setPriority] = useState("100");
  const [errors, setErrors] = useState<Record<string, string>>({});

  function reset() {
    setCode("");
    setName("");
    setCurrency(currencies[0] ?? "");
    setRegion("");
    setPriority("100");
    setErrors({});
  }

  async function submit() {
    const problems: Record<string, string> = {};
    if (!BOOK_CODE.test(code.trim())) problems.code = "Enter a code of 2–64 lowercase letters, digits, “-” or “_”.";
    if (!name.trim()) problems.name = "Enter a name.";
    if (!currency) problems.currency = "Choose a currency.";
    if (region.trim() && !REGION.test(region.trim())) problems.region = "Use a two-letter country code, or leave it empty.";
    if (!/^\d{1,4}$/.test(priority.trim())) problems.priority = "Priority is a whole number (lower wins).";
    setErrors(problems);
    if (Object.keys(problems).length > 0) throw invalid(problems);
    await adminConsoleApi.createPriceBook({
      code: code.trim(),
      name: name.trim(),
      currency,
      region: region.trim().toUpperCase() || undefined,
      priority: Number(priority.trim()),
    });
    return `Price book “${name.trim()}” created.`;
  }

  return (
    <div>
      <FormPopup label={iconLabel("New price book")} buttonClassName={popupButton()} title="New price book" submitLabel="Create price book" onOpen={reset} onSubmit={submit} onDone={onCreated}>
        <TextField label="Code" value={code} onChange={(v) => setCode(v.toLowerCase())} maxLength={64} error={errors.code} autoComplete="off" />
        <TextField label="Name" value={name} onChange={setName} maxLength={128} error={errors.name} />
        <SelectField
          label="Currency"
          value={currency}
          onChange={setCurrency}
          options={currencies.map((c) => ({ value: c, label: c }))}
          error={errors.currency}
        />
        <TextField
          label="Region (optional)"
          value={region}
          onChange={setRegion}
          maxLength={2}
          hint="A two-letter country code for a regional book; empty is the default book for the currency."
          error={errors.region}
        />
        <TextField
          label="Priority"
          value={priority}
          onChange={setPriority}
          inputMode="numeric"
          hint="When several books match, the lower number wins."
          error={errors.priority}
        />
      </FormPopup>
    </div>
  );
}

interface PriceLine {
  offer: string;
  version: number;
  price: CatalogPrice;
}

function rank(interval: string): number {
  const at = DEFAULT_INTERVALS.indexOf(interval);
  return at === -1 ? DEFAULT_INTERVALS.length : at;
}

function sortLines(lines: PriceLine[]): PriceLine[] {
  return [...lines].sort(
    (a, b) => a.offer.localeCompare(b.offer) || rank(a.price.interval) - rank(b.price.interval) || a.price.price_book_code.localeCompare(b.price.price_book_code),
  );
}

/** Prices in force for each offer's latest published version, grouped by
 * product (packages and add-ons) and then plans (bundles). Read-only. */
function CurrentPrices({ overview, productCode }: { overview: CatalogOverview; productCode?: string }) {
  const groups: { title: string; lines: PriceLine[] }[] = [];
  for (const product of overview.products) {
    if (productCode && product.code !== productCode) continue;
    const lines: PriceLine[] = [];
    for (const plan of overview.plans) {
      if (plan.product.id !== product.id) continue;
      const version = plan.versions.find((v) => v.published_at);
      if (!version) continue;
      for (const price of version.prices) {
        lines.push({ offer: `${plan.name}${plan.kind === "add_on" ? " (add-on)" : ""}`, version: version.version, price });
      }
    }
    if (lines.length > 0) groups.push({ title: product.name, lines: sortLines(lines) });
  }
  const bundleLines: PriceLine[] = [];
  for (const bundle of overview.bundles) {
    const version = bundle.versions.find((v) => v.published_at);
    if (!version) continue;
    if (productCode && !version.items.some((i) => i.product_code === productCode)) continue;
    for (const price of version.prices) bundleLines.push({ offer: bundle.name, version: version.version, price });
  }
  if (bundleLines.length > 0) groups.push({ title: "Plans (suites)", lines: sortLines(bundleLines) });

  return (
    <Card
      title="Current prices"
      description="Read-only. Publish a new version from Plans & add-ons to change a price."
      actions={<ButtonLink href="/admin/plans">Open Plans &amp; add-ons</ButtonLink>}
    >
      {groups.length === 0 ? (
        <EmptyState
          icon="tag"
          title="No prices are published yet."
          hint="Publish a plan or package version with prices to see them here."
          action={{ label: "Go to Plans & add-ons", href: "/admin/plans", icon: "chevronRight" }}
          compact
        />
      ) : (
        groups.map((group) => (
          <Table
            key={group.title}
            caption={`${group.title}: current prices`}
            head={["Offer", "Version", "Billing cycle", "Price book", "Price"]}
            numeric={["Price"]}
          >
            {group.lines.map((line) => (
              <tr key={line.price.price_id}>
                <th scope="row">{line.offer}</th>
                <td>v{line.version}</td>
                <td>{INTERVAL_LABELS[line.price.interval] ?? line.price.interval}</td>
                <td className={cp.mono}>{line.price.price_book_code}</td>
                <td className={cp.num}>
                  {line.price.amount_minor === null ? "Tiered" : money(line.price.amount_minor, line.price.currency)}
                </td>
              </tr>
            ))}
          </Table>
        ))
      )}
    </Card>
  );
}
