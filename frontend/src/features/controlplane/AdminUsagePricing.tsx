"use client";

import { useRef, useState } from "react";
import { usagePricingApi, type CreditPackRow, type UsageRateRow } from "@/lib/api/adminPanel";
import { creditsText, ReasonField, reasonIsValid, ToggleFilter, wholeNumber } from "./AdminWallets";
import { can, useAdmin } from "./ControlShell";
import { FormDialog, dialog } from "./Modal";
import { LoadGate, useAction, useLoad } from "./ui";
import { SUPPORTED_CURRENCIES, toMajor, toMinor } from "./amounts";
import { TextField } from "./widgets";
import {
  Alert,
  Badge,
  Button,
  DataTable,
  EMPTY,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SectionHeader,
  Select,
  Stack,
  StatusBadge,
  formatDateTime,
  formatMoney,
  formatNumber,
  useConfirm,
  useToast,
  type Column,
} from "./kit";
import styles from "./AdminWallets.module.css";

/**
 * Admin → Products & Pricing → Usage rates and Credit packs (2026-11-09). What metered
 * usage costs, entered by an Admin. No rate means no charge, so a meter with no rate
 * stays on the page. AI tokens charge whole credits, every other meter charges money,
 * and past charges keep naming the rate they used. Read behind the catalog view grant,
 * written behind `platform.staff_pricing.manage`.
 */

const PRICING = "platform.staff_pricing.manage";
const AI_METER = "ai_tokens";
const CURRENCY_OPTIONS = SUPPORTED_CURRENCIES.map((c) => ({ value: c, label: c }));
const DEFAULT_CURRENCY = "INR";

/** What one unit of each meter is called: [one, many]. */
const UNITS: Record<string, [string, string]> = {
  ai_tokens: ["token", "tokens"],
  whatsapp: ["message", "messages"],
  sms: ["message", "messages"],
  email: ["email", "emails"],
  api_trigger: ["trigger", "triggers"],
  webhook: ["delivery", "deliveries"],
};

const unitsOf = (meter: string) => UNITS[meter] ?? ["unit", "units"];

const capitalized = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** "₹0.50 per message", "₹5.00 per 100 messages", "2 credits per 1,000 tokens". */
function priceText(rate: UsageRateRow): string {
  const [one, many] = unitsOf(rate.meter);
  const per = rate.per_units === 1 ? one : `${formatNumber(rate.per_units)} ${many}`;
  const price = rate.meter === AI_METER ? creditsText(rate.unit_amount) : formatMoney(rate.unit_amount, rate.currency);
  return `${price} per ${per}`;
}

function includedText(rate: UsageRateRow): string {
  if (!rate.included_units) return "None";
  const [one, many] = unitsOf(rate.meter);
  return `${formatNumber(rate.included_units)} ${rate.included_units === 1 ? one : many} a month`;
}

// --- usage rates ----------------------------------------------------------------------

interface RateLine {
  meter: string;
  label: string;
  rate: UsageRateRow | null;
}

type Meter = { meter: string; label: string };

export function UsageRatesScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, PRICING);
  const [history, setHistory] = useState(false);
  const [load, reload] = useLoad(() => usagePricingApi.rates(history), [history]);
  const [setting, setSetting] = useState<{ meter: string; rate: UsageRateRow | null } | null>(null);
  const { ask, dialog: confirm } = useConfirm();
  const loaded = load.status === "ready" ? load.data : null;

  const removeRate = (line: RateLine, rate: UsageRateRow) =>
    ask({
      title: `Remove the ${line.label} rate?`,
      message: "This meter stops being charged from now on: usage is still recorded, with no charge. Past charges keep the rate they used.",
      confirmLabel: "Remove rate",
      danger: true,
      reason: true,
      where: "Usage rates",
      onConfirm: async (reason) => {
        await usagePricingApi.removeRate(rate.id, reason);
        reload();
        return `${line.label} is no longer charged.`;
      },
    });

  const columns: Column<RateLine>[] = [
    { key: "meter", header: "Meter", value: (l) => l.label, render: (l) => l.label },
    {
      key: "price",
      header: "Price",
      sortable: false,
      render: (l) => (l.rate ? priceText(l.rate) : <Badge tone="neutral">Not charged: no rate set</Badge>),
    },
    { key: "included", header: "Included each month", sortable: false, render: (l) => (l.rate ? includedText(l.rate) : EMPTY) },
    { key: "from", header: "Effective from", value: (l) => l.rate?.effective_from, render: (l) => (l.rate ? formatDateTime(l.rate.effective_from) : EMPTY) },
    ...(manage
      ? ([
          {
            key: "actions",
            header: "Actions",
            align: "right",
            hideable: false,
            render: (l) => (
              <span className={styles.cellActions}>
                <Button size="sm" aria-haspopup="dialog" aria-label={`Set rate for ${l.label}${l.rate?.currency ? ` in ${l.rate.currency}` : ""}`} onClick={() => setSetting({ meter: l.meter, rate: l.rate })}>
                  Set rate
                </Button>
                {l.rate && (
                  <Button size="sm" variant="danger" aria-label={`Remove rate for ${l.label}${l.rate.currency ? ` in ${l.rate.currency}` : ""}`} onClick={() => removeRate(l, l.rate as UsageRateRow)}>
                    Remove rate
                  </Button>
                )}
              </span>
            ),
          },
        ] as Column<RateLine>[])
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="Usage rates"
        subtitle="What each meter charges. No rate means no charge."
        info="AI tokens charge whole credits from AI Credits. Every other meter charges money from the Consumption Wallet."
        primary={
          manage && loaded ? (
            <Button variant="primary" icon="plus" aria-haspopup="dialog" onClick={() => setSetting({ meter: loaded.meters[0]?.meter ?? AI_METER, rate: null })}>
              Set rate
            </Button>
          ) : undefined
        }
      />
      <Alert tone="info">A new rate applies to future charges only. Past charges keep the rate they used.</Alert>
      <FilterBar label="Rate history">
        <ToggleFilter label="Show closed rates" checked={history} onChange={setHistory} />
      </FilterBar>
      <LoadGate load={load} what="usage rates">
        {({ meters, results }) => {
          const lines = meters.flatMap<RateLine>((m) => {
            const live = results.filter((r) => r.meter === m.meter && !r.effective_to);
            return live.length > 0 ? live.map((rate) => ({ meter: m.meter, label: m.label, rate })) : [{ meter: m.meter, label: m.label, rate: null }];
          });
          const closed = results.filter((r) => r.effective_to);
          return (
            <div className={styles.stack}>
              <DataTable
                caption="Usage rates by meter"
                columns={columns}
                rows={lines}
                rowKey={(l) => l.rate?.id ?? l.meter}
                pageSize={0}
                maxHeight={false}
                hideEmptyColumns={false}
                empty={<EmptyState icon="tag" title="No meters are defined" hint="Meters come from the platform; ask an engineer to check it." />}
              />
              {history && <ClosedRates rates={closed} />}
            </div>
          );
        }}
      </LoadGate>
      {setting && loaded && (
        <SetRateDialog
          meters={loaded.meters}
          meter={setting.meter}
          rate={setting.rate}
          onClose={() => setSetting(null)}
          onDone={reload}
        />
      )}
      {confirm}
    </Page>
  );
}

function ClosedRates({ rates }: { rates: UsageRateRow[] }) {
  return (
    <section className={styles.stack} aria-label="Closed rates">
      <SectionHeader title="Closed rates" description="Rates no longer in force. The charges made under each of them still name it." />
      <DataTable
        caption="Closed rates"
        columns={[
          { key: "meter", header: "Meter", value: (r) => r.meter_label, render: (r) => r.meter_label },
          { key: "price", header: "Price", sortable: false, render: priceText },
          { key: "included", header: "Included each month", sortable: false, render: includedText },
          { key: "from", header: "From", value: (r) => r.effective_from, render: (r) => formatDateTime(r.effective_from) },
          { key: "to", header: "To", value: (r) => r.effective_to, render: (r) => formatDateTime(r.effective_to) },
          { key: "reason", header: "Reason", wrap: true, value: (r) => r.reason, render: (r) => r.reason },
        ]}
        rows={rates}
        rowKey={(r) => r.id}
        pageSize={0}
        maxHeight={false}
        empty={<EmptyState icon="history" title="No closed rates yet" compact />}
      />
    </section>
  );
}

function SetRateDialog({
  meters,
  meter: firstMeter,
  rate,
  onClose,
  onDone,
}: {
  meters: Meter[];
  meter: string;
  /** The live rate this replaces, which fills the form in. */
  rate: UsageRateRow | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [meter, setMeter] = useState(firstMeter);
  const [currency, setCurrency] = useState(rate?.currency || DEFAULT_CURRENCY);
  const [amount, setAmount] = useState(() => {
    if (!rate) return "";
    return rate.meter === AI_METER ? String(rate.unit_amount) : toMajor(rate.unit_amount, rate.currency);
  });
  const [perUnits, setPerUnits] = useState(rate ? String(rate.per_units) : "1");
  const [included, setIncluded] = useState(rate ? String(rate.included_units) : "0");
  const [reason, setReason] = useState("");

  const isAi = meter === AI_METER;
  const [, many] = unitsOf(meter);
  const unitAmount = isAi ? wholeNumber(amount) : toMinor(amount, currency);
  const per = wholeNumber(perUnits);
  const free = wholeNumber(included);
  const amountBad = amount.trim() !== "" && (unitAmount === null || unitAmount <= 0);
  const perBad = perUnits.trim() !== "" && (per === null || per <= 0);
  const freeBad = included.trim() !== "" && free === null;
  const ready = unitAmount !== null && unitAmount > 0 && per !== null && per > 0 && free !== null && reasonIsValid(reason);
  const label = meters.find((m) => m.meter === meter)?.label ?? meter;

  return (
    <FormDialog
      title="Set rate"
      intro="This replaces the meter's live rate. Future charges use it; past charges keep the rate they used."
      submitLabel="Set rate"
      canSubmit={ready}
      onSubmit={async () => {
        await usagePricingApi.setRate({
          meter,
          ...(isAi ? {} : { currency }),
          unit_amount: unitAmount as number,
          per_units: per as number,
          included_units: free as number,
          reason: reason.trim(),
        });
        return `Rate set for ${label}.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <Select label="Meter" showLabel fullWidth value={meter} onChange={setMeter} options={meters.map((m) => ({ value: m.meter, label: m.label }))} />
      {!isAi && <Select label="Currency" showLabel fullWidth value={currency} onChange={setCurrency} options={CURRENCY_OPTIONS} />}
      <TextField
        label={isAi ? "Credits charged" : `Price (${currency})`}
        required
        inputMode={isAi ? "numeric" : "decimal"}
        autoComplete="off"
        value={amount}
        onChange={setAmount}
        hint={isAi ? "A whole number of credits, never money." : "Like 0.50."}
        error={amountBad ? (isAi ? "Enter a whole number of credits above zero." : "Enter an amount above zero, like 0.50.") : undefined}
      />
      <TextField
        label={`Per how many ${many}`}
        required
        inputMode="numeric"
        autoComplete="off"
        value={perUnits}
        onChange={setPerUnits}
        error={perBad ? "Enter a whole number above zero." : undefined}
      />
      <TextField
        label={`${capitalized(many)} included each month`}
        required
        inputMode="numeric"
        autoComplete="off"
        value={included}
        onChange={setIncluded}
        hint="0 means none."
        error={freeBad ? "Enter a whole number." : undefined}
      />
      <ReasonField value={reason} onChange={setReason} />
    </FormDialog>
  );
}

// --- credit packs ---------------------------------------------------------------------

export function CreditPacksScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, PRICING);
  const [load, reload] = useLoad(() => usagePricingApi.packs(), []);
  const [editing, setEditing] = useState<{ pack: CreditPackRow | null } | null>(null);
  const { ask, dialog: confirm } = useConfirm();
  const { act, busy, feedback } = useAction(reload);

  const bodyOf = (p: CreditPackRow, status: string) => ({ name: p.name, credits: p.credits, price_minor: p.price_minor, currency: p.currency, status });

  const columns: Column<CreditPackRow>[] = [
    { key: "name", header: "Name", value: (p) => p.name, render: (p) => <Stack main={p.name} sub={p.code} /> },
    { key: "credits", header: "Credits", align: "right", value: (p) => p.credits, render: (p) => creditsText(p.credits) },
    { key: "price", header: "Price", align: "right", value: (p) => p.price_minor, render: (p) => formatMoney(p.price_minor, p.currency) },
    { key: "status", header: "Status", value: (p) => p.status, render: (p) => <StatusBadge value={p.status} label={p.status === "active" ? "On sale" : "Archived"} /> },
    ...(manage
      ? ([
          {
            key: "actions",
            header: "Actions",
            align: "right",
            hideable: false,
            render: (p) => (
              <span className={styles.cellActions}>
                <Button size="sm" aria-haspopup="dialog" aria-label={`Edit ${p.name}`} onClick={() => setEditing({ pack: p })}>
                  Edit
                </Button>
                {p.status === "active" ? (
                  <Button
                    size="sm"
                    variant="danger"
                    aria-label={`Archive ${p.name}`}
                    onClick={() =>
                      ask({
                        title: `Archive ${p.name}?`,
                        message: "It comes off sale at once. Customers who already bought it keep their credits.",
                        confirmLabel: "Archive pack",
                        danger: true,
                        onConfirm: async () => {
                          await usagePricingApi.updatePack(p.id, bodyOf(p, "archived"));
                          reload();
                          return `${p.name} is archived.`;
                        },
                      })
                    }
                  >
                    Archive
                  </Button>
                ) : (
                  <Button size="sm" disabled={busy} aria-label={`Put ${p.name} back on sale`} onClick={() => void act(() => usagePricingApi.updatePack(p.id, bodyOf(p, "active")), `${p.name} is back on sale.`)}>
                    Put back on sale
                  </Button>
                )}
              </span>
            ),
          },
        ] as Column<CreditPackRow>[])
      : []),
  ];

  return (
    <Page>
      <PageHeader
        title="Credit packs"
        subtitle="The AI credits customers can buy, each at the price set here."
        info="An order for a pack is placed from the AI Credits page, and the credits arrive when its invoice is paid."
        primary={
          manage ? (
            <Button variant="primary" icon="plus" aria-haspopup="dialog" onClick={() => setEditing({ pack: null })}>
              New credit pack
            </Button>
          ) : undefined
        }
      />
      {feedback}
      <LoadGate load={load} what="credit packs">
        {({ results }) => (
          <DataTable
            caption="Credit packs"
            columns={columns}
            rows={results}
            rowKey={(p) => p.id}
            pageSize={0}
            maxHeight={false}
            hideEmptyColumns={false}
            empty={
              <EmptyState
                icon="sparkles"
                title="No credit packs yet"
                hint="Customers can buy AI credits once a pack is on sale."
                action={
                  manage ? (
                    <Button variant="primary" icon="plus" aria-haspopup="dialog" onClick={() => setEditing({ pack: null })}>
                      Create the first pack
                    </Button>
                  ) : undefined
                }
              />
            }
          />
        )}
      </LoadGate>
      {editing && <PackDialog pack={editing.pack} onClose={() => setEditing(null)} onDone={reload} />}
      {confirm}
    </Page>
  );
}

function PackDialog({ pack, onClose, onDone }: { pack: CreditPackRow | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(pack?.name ?? "");
  const [credits, setCredits] = useState(pack ? String(pack.credits) : "");
  const [currency, setCurrency] = useState(pack?.currency ?? DEFAULT_CURRENCY);
  const [price, setPrice] = useState(pack ? toMajor(pack.price_minor, pack.currency) : "");
  const creditCount = wholeNumber(credits);
  const minor = toMinor(price, currency);
  const creditsBad = credits.trim() !== "" && (creditCount === null || creditCount <= 0);
  const priceBad = price.trim() !== "" && (minor === null || minor <= 0);
  const ready = name.trim() !== "" && creditCount !== null && creditCount > 0 && minor !== null && minor > 0;
  return (
    <FormDialog
      title={pack ? `Edit ${pack.name}` : "New credit pack"}
      submitLabel={pack ? "Save pack" : "Create pack"}
      canSubmit={ready}
      onSubmit={async () => {
        const body = { name: name.trim(), credits: creditCount as number, price_minor: minor as number, currency };
        if (pack) {
          await usagePricingApi.updatePack(pack.id, { ...body, status: pack.status });
          return `${body.name} saved.`;
        }
        await usagePricingApi.createPack(body);
        return `${body.name} created.`;
      }}
      onDone={(message) => {
        if (message) toast.success(message);
        onDone();
      }}
      onClose={onClose}
    >
      <div className={dialog.span}>
        <TextField label="Name" required maxLength={120} autoComplete="off" value={name} onChange={setName} />
      </div>
      <div className={dialog.span}>
        <TextField
          label="Credits"
          required
          inputMode="numeric"
          autoComplete="off"
          value={credits}
          onChange={setCredits}
          hint="Whole credits. Credits are never money."
          error={creditsBad ? "Enter a whole number above zero." : undefined}
        />
      </div>
      <TextField
        label={`Price (${currency})`}
        required
        inputMode="decimal"
        autoComplete="off"
        value={price}
        onChange={setPrice}
        error={priceBad ? "Enter an amount above zero, like 499.00." : undefined}
      />
      <Select label="Currency" showLabel fullWidth value={currency} onChange={setCurrency} options={CURRENCY_OPTIONS} />
    </FormDialog>
  );
}

// --- a plan's rules ---------------------------------------------------------------------

const RENEWAL_RULES = [
  { value: "auto_renew", label: "Renews automatically" },
  { value: "manual", label: "Customer renews by hand" },
  { value: "none", label: "Does not renew" },
];

const CHANGE_RULES = [
  { value: "any", label: "Upgrade or downgrade" },
  { value: "upgrade_only", label: "Upgrade only" },
  { value: "none", label: "No plan changes" },
];

/** A plan's renewal rule, change rule and included usage. A catalog answer that does not
 * carry them yet shows the defaults, and says that saving replaces what is stored. */
export function PlanRulesDialog({
  plan,
  onClose,
}: {
  plan: { id: string; name: string; renewal_rule?: string; change_rule?: string; included_usage?: Record<string, number> };
  onClose: (outcome: { saved: boolean; changed: boolean; message?: string }) => void;
}) {
  const known = plan.renewal_rule !== undefined;
  const [renewal, setRenewal] = useState(plan.renewal_rule ?? "auto_renew");
  const [change, setChange] = useState(plan.change_rule ?? "any");
  const [usage, setUsage] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(plan.included_usage ?? {}).map(([meter, units]) => [meter, String(units)])),
  );
  const [load] = useLoad(() => usagePricingApi.rates(), []);
  // FormDialog closes after its onDone; the outcome is told once.
  const told = useRef(false);

  const invalid = Object.values(usage).some((text) => text.trim() !== "" && wholeNumber(text) === null);

  return (
    <FormDialog
      title={`Rules for ${plan.name}`}
      intro="How a new subscription on this plan renews, which plan changes it allows, and the usage it includes each month."
      submitLabel="Save rules"
      canSubmit={!invalid && load.status === "ready"}
      onSubmit={async () => {
        const included_usage: Record<string, number> = {};
        for (const [meter, text] of Object.entries(usage)) {
          const units = wholeNumber(text);
          if (units) included_usage[meter] = units;
        }
        await usagePricingApi.setPlanRules(plan.id, { renewal_rule: renewal, change_rule: change, included_usage });
        return `Rules saved for ${plan.name}.`;
      }}
      onDone={(message) => {
        told.current = true;
        onClose({ saved: true, changed: true, message });
      }}
      onClose={() => {
        if (!told.current) onClose({ saved: false, changed: false });
      }}
    >
      {!known && (
        <div className={dialog.span}>
          <Alert tone="warning">This page could not read the plan&apos;s saved rules, so these are the defaults. Saving replaces what is stored.</Alert>
        </div>
      )}
      <Select label="Renewal rule" showLabel fullWidth value={renewal} onChange={setRenewal} options={RENEWAL_RULES} />
      <Select label="Change rule" showLabel fullWidth value={change} onChange={setChange} options={CHANGE_RULES} />
      <fieldset className={dialog.span}>
        <legend className={`w360-label ${styles.fieldsetLegend}`}>Included usage each month</legend>
        <LoadGate load={load} what="the meters">
          {({ meters }) => (
            <div className={dialog.grid}>
              {meters.map((m) => {
                const text = usage[m.meter] ?? "";
                return (
                  <TextField
                    key={m.meter}
                    label={m.label}
                    inputMode="numeric"
                    autoComplete="off"
                    value={text}
                    onChange={(next) => setUsage((all) => ({ ...all, [m.meter]: next }))}
                    hint="Leave blank for none."
                    error={text.trim() !== "" && wholeNumber(text) === null ? "Enter a whole number." : undefined}
                  />
                );
              })}
            </div>
          )}
        </LoadGate>
      </fieldset>
    </FormDialog>
  );
}
