"use client";

import { useState } from "react";
import { adminApi, type CommissionTerms, type RuleRow } from "@/lib/api/controlPlane";
import { adminProgrammeApi, type AffiliatePolicy } from "@/lib/api/adminProgramme";
import { formatBasisPoints, toBasisPoints, toMinor } from "./amounts";
import { can, useAdmin } from "./ControlShell";
import {
  Badge,
  Button,
  ButtonLink,
  Card,
  DataTable,
  DescriptionList,
  EmptyState,
  Page,
  PageHeader,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  sentence,
  useToast,
  type Column,
} from "./kit";
import { FormDialog, FormPopup, dialog } from "./Modal";
import { LoadGate, autoReason, describeError, useLoad } from "./ui";
import { SelectField, TextField } from "./widgets";
import local from "./AdminCommissionRules.module.css";

/**
 * Partners → Commission rules (2026-11-09): what earns a commission, how a sale is credited
 * to a partner, how long renewals keep earning, when an earning can be paid, how it is
 * reversed and on what terms it is paid out.
 *
 * Rules are versioned on the server: changing one appends a version and earlier sales keep
 * the version they were made under, so nothing here edits a figure already earned. Clearance
 * and withdrawal terms are platform settings, shown exactly as the server reports them.
 */

const RATE_MODE = { rate: "A percentage of the payment", fixed: "A fixed amount per payment" } as const;

function pays(rule: RuleRow): string {
  if (rule.fixed_amount_minor !== null && rule.fixed_amount_minor !== undefined) {
    return `${formatMoney(rule.fixed_amount_minor, rule.fixed_currency)} per payment`;
  }
  return formatBasisPoints(rule.rate_bp);
}

function renewals(rule: RuleRow): string {
  if (!rule.recurring) return "First sale only";
  return rule.recurring_months ? `Renewals for ${rule.recurring_months} months` : "Every paid renewal";
}

function appliesTo(rule: RuleRow): string {
  const who = rule.partner_name || (rule.partner_id ? "One partner" : "All partners");
  const what = rule.plan_code ? `${rule.product_code}, plan ${rule.plan_code}` : rule.product_code || "every product";
  return `${who}, ${what}`;
}

function stateOf(rule: RuleRow, now: number): { label: string; tone: "success" | "info" | "neutral" } {
  if (rule.retired_at && Date.parse(rule.retired_at) <= now) return { label: "Replaced", tone: "neutral" };
  if (Date.parse(rule.effective_from) > now) return { label: "Scheduled", tone: "info" };
  return { label: "In force", tone: "success" };
}

/** A new rule, or the next version of `base`. Who and what a rule covers is fixed once it
 * exists, so a new version only asks for what may change. */
function RuleDialog({
  base,
  products,
  onDone,
  onClose,
}: {
  base: RuleRow | null;
  products: { code: string; name: string }[];
  onDone: (message: string | undefined) => void;
  onClose: () => void;
}) {
  const startsFixed = base?.fixed_amount_minor !== null && base?.fixed_amount_minor !== undefined;
  const [code, setCode] = useState(base?.code ?? "");
  const [product, setProduct] = useState(base?.product_code ?? "");
  const [mode, setMode] = useState<keyof typeof RATE_MODE>(startsFixed ? "fixed" : "rate");
  const [rate, setRate] = useState(base && !startsFixed ? formatBasisPoints(base.rate_bp).replace("%", "") : "");
  const [fixed, setFixed] = useState("");
  const [currency, setCurrency] = useState(base?.fixed_currency ?? "");
  const [earnsOnRenewals, setEarnsOnRenewals] = useState(base?.recurring ? "yes" : "no");
  const [months, setMonths] = useState(base?.recurring_months ? String(base.recurring_months) : "");
  const [from, setFrom] = useState("");

  const rateBp = toBasisPoints(rate);
  const cleanCurrency = currency.trim().toUpperCase();
  const validCurrency = /^[A-Z]{3}$/.test(cleanCurrency);
  const fixedMinor = validCurrency ? toMinor(fixed, cleanCurrency) : null;
  const monthCount = months.trim() ? Number(months) : null;
  const monthsValid = monthCount === null || (Number.isInteger(monthCount) && monthCount >= 1 && monthCount <= 120);
  const codeValid = /^[a-z0-9-]{1,48}$/.test(code.trim().toLowerCase());
  const amountValid = mode === "rate" ? rateBp !== null : fixedMinor !== null && fixedMinor > 0;

  return (
    <FormDialog
      title={base ? `Change ${base.code}` : "New commission rule"}
      intro="Sales already made keep the commission they were made under. A change applies from the date below."
      submitLabel={base ? "Save new version" : "Create rule"}
      canSubmit={codeValid && amountValid && monthsValid}
      onSubmit={async () => {
        await adminApi.createRule({
          code: code.trim().toLowerCase(),
          reason: autoReason(base ? `Changed commission rule ${base.code}` : "Created a commission rule"),
          product_code: base ? base.product_code : product,
          plan_code: base?.plan_code ?? "",
          partner_id: base?.partner_id ?? undefined,
          rate_bp: mode === "rate" ? (rateBp ?? 0) : 0,
          fixed_amount_minor: mode === "fixed" ? fixedMinor : null,
          fixed_currency: mode === "fixed" ? cleanCurrency : "",
          recurring: earnsOnRenewals === "yes",
          recurring_months: earnsOnRenewals === "yes" ? monthCount : null,
          effective_from: from ? `${from}T00:00:00+00:00` : undefined,
        });
        return base ? `Version ${base.version + 1} of ${base.code} saved.` : "Commission rule created.";
      }}
      onDone={onDone}
      onClose={onClose}
    >
      {base ? (
        <p className={`${dialog.span} ${local.scope}`}>Applies to: {appliesTo(base)}</p>
      ) : (
        <>
          <TextField
            label="Rule code"
            value={code}
            onChange={setCode}
            maxLength={48}
            required
            showRequired
            hint="Lowercase letters, digits and hyphens."
            error={code && !codeValid ? "Use lowercase letters, digits and hyphens only." : undefined}
          />
          <SelectField
            label="Product"
            value={product}
            onChange={setProduct}
            options={[{ value: "", label: "Every product" }, ...products.map((p) => ({ value: p.code, label: p.name }))]}
            hint="A rule for one partner is set on that partner's page."
          />
        </>
      )}
      <SelectField
        label="Pays"
        value={mode}
        onChange={(value) => setMode(value as keyof typeof RATE_MODE)}
        options={Object.entries(RATE_MODE).map(([value, label]) => ({ value, label }))}
      />
      {mode === "rate" ? (
        <TextField
          label="Commission %"
          value={rate}
          onChange={setRate}
          inputMode="decimal"
          required
          showRequired
          hint="0 to 100, up to two decimals."
          error={rate && rateBp === null ? "Enter 0 to 100, with up to two decimals." : undefined}
        />
      ) : (
        <>
          <TextField
            label="Currency"
            value={currency}
            onChange={setCurrency}
            maxLength={3}
            required
            showRequired
            hint="The three-letter code, for example INR."
            error={currency && !validCurrency ? "Enter a three-letter currency code." : undefined}
          />
          <TextField
            label="Fixed amount"
            value={fixed}
            onChange={setFixed}
            inputMode="decimal"
            required
            showRequired
            error={fixed && validCurrency && !amountValid ? "Enter an amount above zero." : undefined}
          />
        </>
      )}
      <SelectField
        label="Renewals"
        value={earnsOnRenewals}
        onChange={setEarnsOnRenewals}
        options={[
          { value: "no", label: "First sale only" },
          { value: "yes", label: "Also earns on paid renewals" },
        ]}
      />
      {earnsOnRenewals === "yes" && (
        <TextField
          label="For how many months"
          value={months}
          onChange={setMonths}
          inputMode="numeric"
          hint="Empty means for as long as the customer stays."
          error={monthsValid ? undefined : "Enter a whole number from 1 to 120."}
        />
      )}
      <TextField label="Starts on (UTC)" type="date" value={from} onChange={setFrom} hint="Empty means now. It can't be in the past." />
    </FormDialog>
  );
}

function RulesCard({ manage }: { manage: boolean }) {
  const toast = useToast();
  const [showReplaced, setShowReplaced] = useState(false);
  const [editing, setEditing] = useState<{ base: RuleRow | null } | null>(null);
  const [load, reload] = useLoad(() => adminApi.rules({ all: showReplaced }), [showReplaced]);
  const [products] = useLoad(() => (manage ? adminApi.products() : Promise.resolve({ results: [] })), [manage]);
  const [now] = useState(() => Date.now());

  const columns: Column<RuleRow>[] = [
    { key: "code", header: "Rule", render: (r) => `${r.code} v${r.version}`, value: (r) => r.code, sortable: true },
    { key: "applies", header: "Applies to", render: appliesTo, wrap: true },
    { key: "pays", header: "Pays", render: pays },
    { key: "renewals", header: "Renewals", render: renewals },
    { key: "from", header: "Starts", render: (r) => formatDate(r.effective_from), value: (r) => r.effective_from, sortable: true },
    {
      key: "state",
      header: "State",
      render: (r) => {
        const state = stateOf(r, now);
        return <Badge tone={state.tone}>{state.label}</Badge>;
      },
    },
  ];

  return (
    <Card
      title="Rules"
      description="The rule closest to the sale wins: partner before product, product before the programme default."
      actions={
        <div className={local.actions}>
          <Button aria-pressed={showReplaced} onClick={() => setShowReplaced((value) => !value)}>
            {showReplaced ? "Hide replaced versions" : "Show replaced versions"}
          </Button>
          {manage && (
            <Button variant="primary" icon="plus" onClick={() => setEditing({ base: null })}>
              New rule
            </Button>
          )}
        </div>
      }
    >
      <LoadGate load={load} what="the commission rules">
        {(data) => (
          <DataTable
            caption="Commission rules"
            columns={columns}
            rows={data.results}
            rowKey={(r) => r.id}
            rowLabel={(r) => `${r.code} version ${r.version}`}
            rowMenu={
              manage
                ? (r) => (r.retired_at && Date.parse(r.retired_at) <= now ? [] : [{ label: "Change", onSelect: () => setEditing({ base: r }) }])
                : undefined
            }
            empty={
              <EmptyState
                icon="percent"
                title="No commission rules yet"
                hint="With no rule, a sale earns no commission. Partner margins are set separately in Pricing."
              />
            }
          />
        )}
      </LoadGate>
      {editing && (
        <RuleDialog
          base={editing.base}
          products={products.status === "ready" ? products.data.results : []}
          onDone={(message) => {
            setEditing(null);
            if (message) toast.success(message);
            reload();
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </Card>
  );
}

function AttributionCard({ manage, terms }: { manage: boolean; terms: CommissionTerms | null }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.affiliatePolicy(), []);
  const [days, setDays] = useState("");
  const [links, setLinks] = useState("");
  const number = Number(days.trim());
  return (
    <Card title="Referral attribution" description="How a sale is credited to the partner who referred the customer.">
      <LoadGate load={load} what="the attribution policy">
        {(policy: AffiliatePolicy) => {
          const valid = days.trim() !== "" && Number.isInteger(number) && number >= 1 && number <= policy.max_attribution_days;
          return (
            <>
              <DescriptionList
                items={[
                  { label: "Who is credited", value: policy.attribution_rule === "first_touch" ? "The first partner link the customer followed" : sentence(policy.attribution_rule) },
                  { label: "A click can convert for", value: `${formatNumber(policy.attribution_days)} days` },
                  {
                    label: "Sales earn for",
                    value: terms ? `${formatNumber(terms.referral_earning_days)} days after the customer converts` : null,
                  },
                  {
                    label: "After a product is revoked",
                    value: policy.revoked_links === "stop" ? "Existing links stop taking new visitors" : "Existing links keep taking visitors",
                  },
                  { label: "Last changed", value: policy.updated_at ? formatDateTime(policy.updated_at) : "Never changed; the platform default applies" },
                ]}
              />
              {manage && (
                <FormPopup
                  label="Change attribution"
                  title="Change referral attribution"
                  size="sm"
                  submitLabel="Save"
                  buttonClassName="w360-btn w360-btn-secondary"
                  canSubmit={valid}
                  onOpen={() => {
                    setDays(String(policy.attribution_days));
                    setLinks(policy.revoked_links);
                  }}
                  onSubmit={async () => {
                    await adminProgrammeApi.setAffiliatePolicy({ attribution_days: number, revoked_links: links });
                    return "Attribution saved. It applies to new clicks only.";
                  }}
                  onDone={reload}
                >
                  <TextField
                    label="A click can convert for (days)"
                    value={days}
                    onChange={setDays}
                    inputMode="numeric"
                    maxLength={3}
                    hint={`1 to ${policy.max_attribution_days}.`}
                    error={days && !valid ? `Enter a whole number from 1 to ${policy.max_attribution_days}.` : undefined}
                  />
                  <SelectField
                    label="Existing links after a product is revoked"
                    value={links}
                    onChange={setLinks}
                    options={[
                      { value: "stop", label: "Stop taking new visitors" },
                      { value: "continue", label: "Keep taking visitors" },
                    ]}
                  />
                </FormPopup>
              )}
            </>
          );
        }}
      </LoadGate>
    </Card>
  );
}

/** Asks the server to recompute what should have been earned and compare it with the ledger. */
function LedgerCheck() {
  const [state, setState] = useState<{ status: "idle" | "busy" } | { status: "done"; text: string; clean: boolean } | { status: "failed"; text: string }>({
    status: "idle",
  });
  return (
    <div className={local.check}>
      <Button
        loading={state.status === "busy"}
        onClick={() => {
          setState({ status: "busy" });
          adminApi.reconcileCommissions().then(
            (r) => {
              const found = r.missing.length + r.duplicates.length + r.wrong.length + r.unexpected.length;
              setState({
                status: "done",
                clean: r.is_clean,
                text: r.is_clean
                  ? `The ledger matches the sales of ${formatNumber(r.organizations)} organizations.`
                  : `${formatNumber(found)} ${found === 1 ? "difference" : "differences"} found: ${r.missing.length} missing, ${r.duplicates.length} duplicated, ${r.wrong.length} wrong, ${r.unexpected.length} unexpected.`,
              });
            },
            (err) => setState({ status: "failed", text: describeError(err) }),
          );
        }}
      >
        Check the ledger against sales
      </Button>
      {state.status === "done" && (
        <p role="status" className={state.clean ? local.ok : local.problem}>
          {state.text}
        </p>
      )}
      {state.status === "failed" && (
        <p role="alert" className={local.problem}>
          {state.text}
        </p>
      )}
    </div>
  );
}

function TermsCards({ load }: { load: ReturnType<typeof useLoad<CommissionTerms>>[0] }) {
  return (
    <LoadGate load={load} what="the commission terms">
      {(terms) => (
        <>
          <Card title="Commission clearance" description="An earning can't be paid until the refund window has passed.">
            <DescriptionList
              items={[
                { label: "Held for", value: `${formatNumber(terms.clearance_days)} days after the payment` },
                { label: "Set by", value: "A platform setting, changed at deployment" },
              ]}
            />
            <ButtonLink href="/admin/commissions">Open commission entries</ButtonLink>
          </Card>
          <Card title="Reversals" description="A reversal names the entry it undoes and never edits it. Refunds and cancellations reverse automatically.">
            <DescriptionList items={[{ label: "Allowed reasons", value: terms.reversal_reasons.map(sentence).join(", ") }]} />
            <LedgerCheck />
          </Card>
          <Card title="Payout terms" description="Earnings are records until a payout is settled. They are never spendable.">
            <DescriptionList
              items={[
                {
                  label: "Smallest withdrawal",
                  value: `${formatMoney(terms.min_withdrawal_minor, terms.withdrawal_currency)}, or the same figure in a partner's payout currency`,
                },
                { label: "TDS withheld", value: `${formatBasisPoints(terms.tds_rate_bp)} on ${terms.tds_currency} withdrawals` },
                { label: "Approval", value: "The approver can't be the person who prepared the payout" },
                { label: "Marked paid", value: "Only with the reference of the payment made outside the platform" },
              ]}
            />
            <ButtonLink href="/admin/payouts">Open payouts</ButtonLink>
          </Card>
        </>
      )}
    </LoadGate>
  );
}

export function CommissionRulesScreen() {
  const { permissions } = useAdmin();
  const [terms] = useLoad(() => adminApi.commissionTerms(), []);
  return (
    <Page>
      <PageHeader
        title="Commission rules"
        subtitle="What earns a commission, how a sale is credited, and when an earning can be paid."
        info="Commission is earned only after a verified payment. Changing a rule never changes what earlier sales earned."
      />
      <RulesCard manage={can(permissions, "platform.staff_commission.manage")} />
      <div className={local.grid}>
        <AttributionCard manage={can(permissions, "platform.staff_partner.manage")} terms={terms.status === "ready" ? terms.data : null} />
        <Card title="Renewal eligibility" description="Set on each rule, under Renewals in the table above.">
          <DescriptionList
            items={[
              { label: "First sale only", value: "Earns on the sale and not on later renewals" },
              { label: "Every paid renewal", value: "Earns each time the customer pays to renew" },
              { label: "Renewals for a set time", value: "Earns on paid renewals for that many months after the sale" },
            ]}
          />
        </Card>
        <TermsCards load={terms} />
      </div>
    </Page>
  );
}
