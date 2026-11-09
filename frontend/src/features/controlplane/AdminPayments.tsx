"use client";

import { useState } from "react";
import { adminConsoleApi, type PaymentEventRow, type PaymentProviderRow } from "@/lib/api/adminConsole";
import { ApiError } from "@/lib/api/client";
import { Icon } from "@/components/sidebar/icons";
import { can, useAdmin } from "./ControlShell";
import { FormDialog, FormPopup, dialog } from "./Modal";
import { LoadGate, money, useAction, useLoad } from "./ui";
import { SUPPORTED_CURRENCIES } from "./amounts";
import { Segmented, SelectField, TextField } from "./widgets";
import {
  Avatar,
  Badge,
  Button,
  Card,
  CopyButton,
  DataTable,
  DescriptionList,
  EmptyState,
  FilterBar,
  IdCell,
  Page,
  PageHeader,
  SectionHeader,
  Select,
  Tooltip,
  exactTime,
  formatDateTime,
  formatRelative,
  sentence,
  type Column,
  type Tone,
} from "./kit";
import { iconLabel, popupButton } from "./kit/PopupLabel";
import pay from "./AdminPayments.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Super Admin → Payments (Phase 8 extension): the payment provider accounts
 * that collect customers' invoices, and the provider event log.
 *
 * **No secret is ever typed or shown here.** A provider names *references*;
 * the API key and webhook secret live in the secret manager under the
 * environment variables the server derives from them, and the API reports
 * only whether each one is present. Enabling a provider needs both.
 */

const ADAPTERS = [
  { value: "stripe", label: "Stripe" },
  { value: "razorpay", label: "Razorpay" },
  { value: "fake", label: "Test provider (development)" },
];

const adapterLabel = (adapter: string) => ADAPTERS.find((a) => a.value === adapter)?.label ?? adapter;

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

/** What happened to an event the provider sent. */
const OUTCOME_TONE: Record<string, Tone> = {
  applied: "success",
  succeeded: "success",
  duplicate: "neutral",
  ignored: "neutral",
  rejected: "danger",
  invalid_signature: "danger",
  failed: "danger",
  error: "danger",
};

export function PaymentsScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payment.manage");
  const [providers, reloadProviders] = useLoad(() => adminConsoleApi.paymentProviders(), []);
  const [events] = useLoad(() => adminConsoleApi.paymentEvents(), []);
  const { act, busy, feedback } = useAction(reloadProviders);
  const eventRows = events.status === "ready" ? events.data.results : [];

  return (
    <Page>
      <PageHeader
        title="Payments"
        subtitle="The providers that collect customers' invoices, and what they report."
        info="Secrets are never entered or shown here. Each provider's API key and webhook secret are set by an operator in the secret manager; this page only says whether each one is present."
      />
      {feedback}
      <section aria-labelledby="providers-h" className={pay.section}>
        <SectionHeader
          id="providers-h"
          title="Providers"
          description="A provider is enabled once both of its secrets are set."
          actions={manage ? <AddProvider onAdded={reloadProviders} /> : undefined}
        />
        <LoadGate load={providers} what="payment providers">
          {({ results }) =>
            results.length === 0 ? (
              <Card as="div">
                <EmptyState
                  icon="card"
                  title="No payment providers yet"
                  hint="Add Stripe or Razorpay to collect customers' invoices."
                  compact
                />
              </Card>
            ) : (
              <ul className={pay.providers} aria-label="Payment providers">
                {results.map((p) => (
                  <li key={p.key}>
                    <ProviderCard provider={p} events={eventRows} manage={manage} act={act} busy={busy} />
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      </section>

      <section aria-labelledby="events-h" className={pay.section}>
        <SectionHeader id="events-h" title="Payment events" description="What providers reported, newest first. Only a verified signature is ever applied." />
        <LoadGate load={events} what="payment events">
          {({ results }) => <EventLog events={results} />}
        </LoadGate>
      </section>
    </Page>
  );
}

function EventLog({ events }: { events: PaymentEventRow[] }) {
  const [provider, setProvider] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const providers = [...new Set(events.map((e) => e.provider))].sort();
  const outcomes = [...new Set(events.map((e) => e.outcome))].sort();
  const rows = events.filter((e) => (provider === "all" || e.provider === provider) && (outcome === "all" || e.outcome === outcome));
  const columns: Column<PaymentEventRow>[] = [
    {
      key: "received",
      header: "Received",
      value: (e) => e.received_at,
      render: (e) => <time dateTime={e.received_at} title={exactTime(e.received_at)}>{formatDateTime(e.received_at)}</time>,
    },
    { key: "provider", header: "Provider", value: (e) => e.provider, render: (e) => <code className={pay.mono}>{e.provider}</code> },
    { key: "event", header: "Event", value: (e) => e.kind, render: (e) => sentence(e.kind) },
    {
      key: "outcome",
      header: "Outcome",
      value: (e) => e.outcome,
      render: (e) => (
        <Badge tone={OUTCOME_TONE[e.outcome] ?? "neutral"} dot>
          {sentence(e.outcome)}
        </Badge>
      ),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      value: (e) => e.amount_minor,
      render: (e) => (e.amount_minor !== null && e.currency ? money(e.amount_minor, e.currency) : "—"),
    },
    { key: "org", header: "Organization", value: (e) => e.organization_id, render: (e) => <IdCell value={e.organization_id} label="Copy organization ID" /> },
  ];
  const filtered = provider !== "all" || outcome !== "all";
  return (
    <>
      {events.length > 0 && (
        <FilterBar
          label="Filter payment events"
          onClear={
            filtered
              ? () => {
                  setProvider("all");
                  setOutcome("all");
                }
              : undefined
          }
        >
          <Select
            label="Provider"
            prefix="Provider:"
            value={provider}
            onChange={setProvider}
            options={[{ value: "all", label: "All" }, ...providers.map((p) => ({ value: p, label: p }))]}
          />
          <Select
            label="Outcome"
            prefix="Outcome:"
            value={outcome}
            onChange={setOutcome}
            options={[{ value: "all", label: "All" }, ...outcomes.map((o) => ({ value: o, label: sentence(o) }))]}
          />
        </FilterBar>
      )}
      <DataTable
        caption="Payment event log"
        columns={columns}
        rows={rows}
        rowKey={(e) => e.id}
        hideEmptyColumns={false}
        empty={
          events.length === 0 ? (
            <EmptyState icon="receipt" title="No payment events yet" hint="Events appear here when a provider calls its webhook." />
          ) : (
            <EmptyState icon="search" title="No event matches these filters" />
          )
        }
      />
    </>
  );
}

/** Set or not set - the variable an operator sets is in the tooltip. */
function SecretState({ label, configured, variable }: { label: string; configured: boolean; variable: string }) {
  return (
    <span className={pay.secret}>
      <Badge tone={configured ? "success" : "warning"} icon={configured ? "check" : "x"}>
        {configured ? "Set" : "Not set"}
      </Badge>
      <Tooltip content={<>Set in the secret manager as {variable}</>}>
        <button type="button" className={pay.info} aria-label={`Where the ${label.toLowerCase()} is set`}>
          <Icon name="info" size={14} />
        </button>
      </Tooltip>
    </span>
  );
}

function ProviderCard({
  provider: p,
  events,
  manage,
  act,
  busy,
}: {
  provider: PaymentProviderRow;
  events: PaymentEventRow[];
  manage: boolean;
  act: Act;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const ready = p.credential_configured && p.webhook_secret_configured;
  const enabled = p.status === "enabled";
  const last = events.filter((e) => e.provider === p.key).map((e) => e.received_at).sort().at(-1);
  const titleId = `provider-${p.key}`;
  return (
    <section className={pay.card} aria-labelledby={titleId}>
      <div className={pay.cardHead}>
        <Avatar name={adapterLabel(p.adapter)} />
        <div className={pay.cardTitle}>
          <h3 id={titleId}>{p.display_name}</h3>
          <code className={pay.mono}>{p.key}</code>
        </div>
        <div className={pay.badges}>
          {enabled ? (
            <Badge tone="success" dot>
              Enabled
            </Badge>
          ) : ready ? (
            <Badge tone="neutral" dot>
              Disabled
            </Badge>
          ) : (
            <Badge tone="warning" dot>
              Needs setup
            </Badge>
          )}
          <Badge tone={p.mode === "live" ? "success" : "info"}>{p.mode === "live" ? "Live" : "Test"}</Badge>
        </div>
      </div>
      <DescriptionList
        items={[
          { label: "Adapter", value: adapterLabel(p.adapter) },
          { label: "Currencies", value: p.currencies.join(", ") },
          { label: "Priority", value: p.priority },
          { label: "API key", value: <SecretState label="API key" configured={p.credential_configured} variable={p.credential_variable} /> },
          {
            label: "Webhook secret",
            value: <SecretState label="Webhook secret" configured={p.webhook_secret_configured} variable={p.webhook_secret_variable} />,
          },
          {
            label: "Webhook path",
            value: (
              <span className={pay.path}>
                <Tooltip content="Register this path, on the API host, with the provider.">
                  <code className={pay.mono} tabIndex={0}>
                    {p.webhook_path}
                  </code>
                </Tooltip>
                <CopyButton value={p.webhook_path} label="Copy webhook path" />
              </span>
            ),
          },
          {
            label: "Last webhook",
            value: last ? <time dateTime={last} title={exactTime(last)}>{formatRelative(last)}</time> : "None received yet",
          },
        ]}
      />
      {manage && (
        <div className={pay.actions}>
          <Button
            variant={enabled ? "secondary" : "primary"}
            size="sm"
            aria-label={`${enabled ? "Disable" : "Enable"} ${p.display_name}`}
            disabled={busy || (!enabled && !ready)}
            onClick={() =>
              act(
                () => adminConsoleApi.updatePaymentProvider(p.key, { status: enabled ? "disabled" : "enabled", version: p.version }),
                `${p.display_name} is now ${enabled ? "disabled" : "enabled"}.`,
              )
            }
          >
            {enabled ? "Disable" : "Enable"}
          </Button>
          <Button
            size="sm"
            aria-label={`Switch to ${p.mode === "live" ? "test" : "live"} mode: ${p.display_name}`}
            disabled={busy}
            onClick={() =>
              act(
                () => adminConsoleApi.updatePaymentProvider(p.key, { mode: p.mode === "live" ? "test" : "live", version: p.version }),
                `${p.display_name} is now in ${p.mode === "live" ? "test" : "live"} mode.`,
              )
            }
          >
            Switch to {p.mode === "live" ? "test" : "live"}
          </Button>
          <Button
            size="sm"
            aria-label={`Edit currencies of ${p.display_name}`}
            aria-haspopup="dialog"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            Edit currencies
          </Button>
          {!enabled && !ready && <span className={pay.hint}>Set both secrets before enabling.</span>}
        </div>
      )}
      {editing && (
        <CurrencyEditor
          provider={p}
          onClose={() => setEditing(false)}
          onSaved={() => void act(async () => {}, `${p.display_name}: currencies updated.`)}
        />
      )}
    </section>
  );
}

function CurrencyChecks({
  legend,
  value,
  onChange,
  error,
}: {
  legend: string;
  value: string[];
  onChange: (value: string[]) => void;
  error?: string;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className="w360-label">{legend}</legend>
      <div className={styles.actions}>
        {SUPPORTED_CURRENCIES.map((c) => (
          <label key={c} className={styles.check}>
            <input
              type="checkbox"
              checked={value.includes(c)}
              onChange={(e) => onChange(e.target.checked ? [...value, c].sort() : value.filter((v) => v !== c))}
            />
            <span>{c}</span>
          </label>
        ))}
      </div>
      {error && <p className="w360-error-text">{error}</p>}
    </fieldset>
  );
}

/** A provider's currencies, in a popup opened from its row. A refusal stays in
 * the popup; a save is announced (and the list reloaded) by the page. */
function CurrencyEditor({ provider, onClose, onSaved }: { provider: PaymentProviderRow; onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState<string[]>(provider.currencies);
  return (
    <FormDialog
      title={`Edit currencies of ${provider.display_name}`}
      size="sm"
      submitLabel="Save currencies"
      canSubmit={value.length > 0}
      onSubmit={async () => {
        await adminConsoleApi.updatePaymentProvider(provider.key, { currencies: value, version: provider.version });
      }}
      onDone={onSaved}
      onClose={onClose}
    >
      <CurrencyChecks
        legend={`Currencies ${provider.display_name} collects`}
        value={value}
        onChange={setValue}
        error={value.length === 0 ? "Choose at least one currency." : undefined}
      />
    </FormDialog>
  );
}

const KEY = /^[a-z0-9-]{1,32}$/;
const REF = /^[A-Za-z0-9_-]{1,48}$/;

/** Before any call: the fields say what is wrong, the popup how many. */
function invalid(problems: Record<string, string>): ApiError {
  const list = Object.values(problems);
  return new ApiError(400, { detail: `Fix ${list.length === 1 ? "this" : `these ${list.length} things`} before saving: ${list.join(" ")}` });
}

function AddProvider({ onAdded }: { onAdded: () => void }) {
  const [key, setKey] = useState("");
  const [adapter, setAdapter] = useState("stripe");
  const [displayName, setDisplayName] = useState("");
  const [mode, setMode] = useState<"test" | "live">("test");
  const [credentialRef, setCredentialRef] = useState("");
  const [webhookRef, setWebhookRef] = useState("");
  const [currencies, setCurrencies] = useState<string[]>([]);
  const [priority, setPriority] = useState("100");
  const [errors, setErrors] = useState<Record<string, string>>({});

  function reset() {
    setKey("");
    setAdapter("stripe");
    setDisplayName("");
    setMode("test");
    setCredentialRef("");
    setWebhookRef("");
    setCurrencies([]);
    setPriority("100");
    setErrors({});
  }

  async function submit() {
    const problems: Record<string, string> = {};
    if (!KEY.test(key.trim())) problems.key = "Key: 1–32 lowercase letters, digits or “-”.";
    if (!REF.test(credentialRef.trim())) problems.credentialRef = "API key reference: 1–48 letters, digits, “_” or “-”.";
    if (!REF.test(webhookRef.trim())) problems.webhookRef = "Webhook secret reference: 1–48 letters, digits, “_” or “-”.";
    if (currencies.length === 0) problems.currencies = "Choose at least one currency.";
    if (!/^\d{1,4}$/.test(priority.trim())) problems.priority = "Priority is a whole number (lower is tried first).";
    setErrors(problems);
    if (Object.keys(problems).length > 0) throw invalid(problems);
    await adminConsoleApi.createPaymentProvider({
      key: key.trim(),
      adapter,
      display_name: displayName.trim() || key.trim(),
      mode,
      credential_ref: credentialRef.trim().toUpperCase(),
      webhook_secret_ref: webhookRef.trim().toUpperCase(),
      currencies,
      priority: Number(priority.trim()),
    });
    return "Provider added. It starts disabled: set its two secrets in the secret manager, then enable it.";
  }

  return (
    <div>
      <FormPopup
        label={iconLabel("Add provider")}
        buttonClassName={popupButton()}
        title="Add provider"
        intro={
          <>
            Secrets are never typed here. You name two <em>references</em>; the server turns each into an environment variable name (shown in the
            providers table once added) that an operator sets in the secret manager.
          </>
        }
        submitLabel="Add provider"
        onOpen={reset}
        onSubmit={submit}
        onDone={onAdded}
      >
        <TextField label="Key" value={key} onChange={(v) => setKey(v.toLowerCase())} maxLength={32} autoComplete="off" hint="Used in the webhook URL, e.g. stripe-eu." error={errors.key} />
        <SelectField label="Adapter" value={adapter} onChange={setAdapter} options={ADAPTERS} />
        <TextField label="Display name" value={displayName} onChange={setDisplayName} maxLength={64} />
        <Segmented
          legend="Mode"
          options={[
            { value: "test", label: "Test" },
            { value: "live", label: "Live" },
          ]}
          value={mode}
          onChange={setMode}
        />
        <TextField
          label="API key reference"
          value={credentialRef}
          onChange={(v) => setCredentialRef(v.toUpperCase())}
          maxLength={48}
          autoComplete="off"
          spellCheck={false}
          hint="A name such as STRIPE_EU - never the key itself."
          error={errors.credentialRef}
        />
        <TextField
          label="Webhook secret reference"
          value={webhookRef}
          onChange={(v) => setWebhookRef(v.toUpperCase())}
          maxLength={48}
          autoComplete="off"
          spellCheck={false}
          hint="A name such as STRIPE_EU - never the secret itself."
          error={errors.webhookRef}
        />
        <div className={dialog.span}>
          <CurrencyChecks legend="Currencies it collects" value={currencies} onChange={setCurrencies} error={errors.currencies} />
        </div>
        <TextField label="Priority" value={priority} onChange={setPriority} inputMode="numeric" hint="Lower is tried first." error={errors.priority} />
      </FormPopup>
    </div>
  );
}
