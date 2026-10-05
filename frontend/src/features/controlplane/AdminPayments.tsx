"use client";

import { useState } from "react";
import { adminConsoleApi, type PaymentProviderRow } from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, Status, Table, money, useAction, useLoad, when } from "./ui";
import { SUPPORTED_CURRENCIES } from "./amounts";
import { ErrorSummary, Segmented, SelectField, TextField } from "./widgets";
import cp from "./ControlPlane.module.css";
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

export function PaymentsScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_payment.manage");
  const [providers, reloadProviders] = useLoad(() => adminConsoleApi.paymentProviders(), []);
  const [events] = useLoad(() => adminConsoleApi.paymentEvents(), []);
  const { act, busy, feedback } = useAction(reloadProviders);

  return (
    <div className={cp.stack}>
      <div>
        <h1>Payments</h1>
        <p className={cp.lead}>
          Payment providers collect customers&apos; invoices. Their secrets are never entered or shown here: each is
          set in the secret manager as the environment variable named below.
        </p>
      </div>
      {feedback}
      <section className={styles.section} aria-labelledby="providers-h">
        <h2 id="providers-h" className={styles.sectionTitle}>
          Providers
        </h2>
        <LoadGate load={providers} what="payment providers">
          {({ results }) => (
            <Table
              caption="Payment providers"
              head={["Provider", "Adapter", "Mode", "Status", "Currencies", "Secrets", "Webhook URL path", "Actions"]}
              isEmpty={results.length === 0}
              empty="No payment providers yet."
            >
              {results.map((p) => (
                <ProviderRow key={p.key} provider={p} manage={manage} act={act} busy={busy} />
              ))}
            </Table>
          )}
        </LoadGate>
        {manage && <AddProvider act={act} busy={busy} />}
      </section>

      <section className={styles.section} aria-labelledby="events-h">
        <h2 id="events-h" className={styles.sectionTitle}>
          Payment events
        </h2>
        <p className={cp.muted}>What providers reported, newest first. Only a verified signature is ever applied.</p>
        <LoadGate load={events} what="payment events">
          {({ results }) => (
            <Table
              caption="Payment event log"
              head={["Received", "Provider", "Event", "Outcome", "Amount", "Organization"]}
              isEmpty={results.length === 0}
              empty="No payment events yet."
            >
              {results.map((e) => (
                <tr key={e.id}>
                  <td>{when(e.received_at)}</td>
                  <td>{e.provider}</td>
                  <td>{e.kind.replace(/_/g, " ")}</td>
                  <td>
                    <Status value={e.outcome} />
                  </td>
                  <td className={cp.num}>
                    {e.amount_minor !== null && e.currency ? money(e.amount_minor, e.currency) : "—"}
                  </td>
                  <td className={cp.mono}>{e.organization_id ?? "—"}</td>
                </tr>
              ))}
            </Table>
          )}
        </LoadGate>
      </section>
    </div>
  );
}

function SecretState({ label, configured, variable }: { label: string; configured: boolean; variable: string }) {
  return (
    <li>
      <span aria-hidden="true">{configured ? "✓ " : "✗ "}</span>
      {label} {configured ? "configured" : "not set"}
      <div className={cp.muted}>
        {configured ? "In" : "Set"} <code className={cp.mono}>{variable}</code>
      </div>
    </li>
  );
}

function ProviderRow({
  provider: p,
  manage,
  act,
  busy,
}: {
  provider: PaymentProviderRow;
  manage: boolean;
  act: Act;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const ready = p.credential_configured && p.webhook_secret_configured;
  const enabled = p.status === "enabled";
  return (
    <tr>
      <th scope="row">
        {p.display_name}
        <div className={`${cp.mono} ${cp.muted}`}>{p.key}</div>
        <div className={cp.muted}>Priority {p.priority}</div>
      </th>
      <td>{adapterLabel(p.adapter)}</td>
      <td>{p.mode === "live" ? "Live" : "Test"}</td>
      <td>
        <Status value={p.status} />
      </td>
      <td>
        {editing ? (
          <CurrencyEditor
            provider={p}
            busy={busy}
            onCancel={() => setEditing(false)}
            onSave={(currencies) =>
              act(async () => {
                await adminConsoleApi.updatePaymentProvider(p.key, { currencies, version: p.version });
                setEditing(false);
              }, `${p.display_name}: currencies updated.`)
            }
          />
        ) : (
          p.currencies.join(", ") || "—"
        )}
      </td>
      <td>
        <ul className={styles.plainList}>
          <SecretState label="API key" configured={p.credential_configured} variable={p.credential_variable} />
          <SecretState label="Webhook secret" configured={p.webhook_secret_configured} variable={p.webhook_secret_variable} />
        </ul>
      </td>
      <td>
        <code className={cp.mono}>{p.webhook_path}</code>
        <div className={cp.muted}>Register this path, on the API host, with the provider.</div>
      </td>
      <td>
        {manage && (
          <div className={styles.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
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
            </button>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              aria-label={`Switch ${p.display_name} to ${p.mode === "live" ? "test" : "live"} mode`}
              disabled={busy}
              onClick={() =>
                act(
                  () => adminConsoleApi.updatePaymentProvider(p.key, { mode: p.mode === "live" ? "test" : "live", version: p.version }),
                  `${p.display_name} is now in ${p.mode === "live" ? "test" : "live"} mode.`,
                )
              }
            >
              Switch to {p.mode === "live" ? "test" : "live"}
            </button>
            {!editing && (
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                aria-label={`Edit currencies of ${p.display_name}`}
                onClick={() => setEditing(true)}
              >
                Edit currencies
              </button>
            )}
            {!enabled && !ready && <p className={cp.muted}>Set both secrets before enabling.</p>}
          </div>
        )}
      </td>
    </tr>
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

function CurrencyEditor({
  provider,
  busy,
  onSave,
  onCancel,
}: {
  provider: PaymentProviderRow;
  busy: boolean;
  onSave: (currencies: string[]) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState<string[]>(provider.currencies);
  return (
    <form
      className={cp.field}
      onSubmit={(e) => {
        e.preventDefault();
        if (value.length > 0) onSave(value);
      }}
    >
      <CurrencyChecks
        legend={`Currencies ${provider.display_name} collects`}
        value={value}
        onChange={setValue}
        error={value.length === 0 ? "Choose at least one currency." : undefined}
      />
      <div className={styles.actions}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || value.length === 0}>
          Save currencies
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

const KEY = /^[a-z0-9-]{1,32}$/;
const REF = /^[A-Za-z0-9_-]{1,48}$/;

function AddProvider({ act, busy }: { act: Act; busy: boolean }) {
  const [key, setKey] = useState("");
  const [adapter, setAdapter] = useState("stripe");
  const [displayName, setDisplayName] = useState("");
  const [mode, setMode] = useState<"test" | "live">("test");
  const [credentialRef, setCredentialRef] = useState("");
  const [webhookRef, setWebhookRef] = useState("");
  const [currencies, setCurrencies] = useState<string[]>([]);
  const [priority, setPriority] = useState("100");
  const [errors, setErrors] = useState<Record<string, string>>({});

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const problems: Record<string, string> = {};
    if (!KEY.test(key.trim())) problems.key = "Key: 1–32 lowercase letters, digits or “-”.";
    if (!REF.test(credentialRef.trim())) problems.credentialRef = "API key reference: 1–48 letters, digits, “_” or “-”.";
    if (!REF.test(webhookRef.trim())) problems.webhookRef = "Webhook secret reference: 1–48 letters, digits, “_” or “-”.";
    if (currencies.length === 0) problems.currencies = "Choose at least one currency.";
    if (!/^\d{1,4}$/.test(priority.trim())) problems.priority = "Priority is a whole number (lower is tried first).";
    setErrors(problems);
    if (Object.keys(problems).length > 0) return;
    void act(async () => {
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
      setKey("");
      setDisplayName("");
      setCredentialRef("");
      setWebhookRef("");
      setCurrencies([]);
    }, "Provider added. It starts disabled: set its two secrets in the secret manager, then enable it.");
  }

  return (
    <div className={`w360-card ${cp.card}`}>
      <form className={cp.form} onSubmit={submit} noValidate>
        <h3 className={cp.cardTitle}>Add provider</h3>
        <p className={cp.muted}>
          Secrets are never typed here. You name two <em>references</em>; the server turns each into an environment
          variable name (shown in the providers table once added) that an operator sets in the secret manager.
        </p>
        <ErrorSummary errors={Object.values(errors)} />
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
        <CurrencyChecks legend="Currencies it collects" value={currencies} onChange={setCurrencies} error={errors.currencies} />
        <TextField label="Priority" value={priority} onChange={setPriority} inputMode="numeric" hint="Lower is tried first." error={errors.priority} />
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
            Add provider
          </button>
        </div>
      </form>
    </div>
  );
}
