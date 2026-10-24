"use client";

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  formatMicros,
  getAiSettings,
  getAiUsage,
  listAiRequests,
  updateAiSettings,
  type AiPolicy,
  type AiProvider,
  type AiRequestRow,
  type AiUsage,
} from "@/lib/api/intelligence";
import styles from "./Intelligence.module.css";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | {
      status: "ready";
      usage: AiUsage;
      policy: AiPolicy;
      providers: AiProvider[];
      requests: AiRequestRow[];
    };

const STATUS_TEXT: Record<AiUsage["status"], string> = {
  enabled: "AI is on",
  disabled: "AI is off for this organization",
  suspended_by_platform: "AI is suspended by Workeasy360",
};

export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { detail?: string } | undefined;
    if (err.status === 403) return "You don't have permission to change AI settings.";
    if (err.status === 409) return "The settings changed since you opened them. Reload and try again.";
    if (body?.detail) return body.detail;
  }
  return "Something went wrong. Please try again.";
}

/**
 * Settings → AI (Tasks 88, 89): whether AI is on, what it may cost, which
 * providers may see the organization's data, and what it has been used for -
 * as metadata only. No prompt, answer or document ever appears here.
 */
export function AiSettings({ orgSlug }: { orgSlug: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    Promise.all([getAiUsage(orgSlug), getAiSettings(orgSlug), listAiRequests(orgSlug)])
      .then(([usage, settings, requests]) =>
        setLoad({
          status: "ready",
          usage,
          policy: settings.policy,
          providers: settings.providers,
          requests: requests.results,
        }),
      )
      .catch((err: unknown) =>
        setLoad({ status: err instanceof ApiError && err.status === 403 ? "forbidden" : "error" }),
      );
  }

  useEffect(refresh, [orgSlug]);

  if (load.status === "loading") return <p aria-live="polite">Loading AI settings…</p>;
  if (load.status === "forbidden") {
    return <p role="alert">You don&apos;t have permission to view this organization&apos;s AI settings.</p>;
  }
  if (load.status === "error") return <p role="alert">Couldn&apos;t load AI settings right now.</p>;

  const { usage, policy, providers, requests } = load;
  const money = (micros: number) => formatMicros(micros, usage.currency);

  async function save(change: Parameters<typeof updateAiSettings>[1]) {
    setError(null);
    setMessage(null);
    try {
      await updateAiSettings(orgSlug, change);
      setMessage("AI settings saved.");
      refresh();
    } catch (err) {
      setError(describeError(err));
    }
  }

  return (
    <div className={styles.stack}>
      {message && <p role="status">{message}</p>}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}

      <section className={`w360-card ${styles.card}`} aria-labelledby="ai-status">
        <h2 id="ai-status" className={styles.cardTitle}>
          {STATUS_TEXT[usage.status]}
        </h2>
        <p className={styles.muted}>
          This month: {money(usage.organization.spent_micros)} of {money(usage.organization.limit_micros)}.
          Limits reset {new Date(usage.resets_at).toLocaleDateString()}.
        </p>
        <p className={styles.note}>{usage.note}</p>
      </section>

      <section aria-labelledby="ai-products">
        <h2 id="ai-products" className={styles.sectionTitle}>
          Usage by product
        </h2>
        {usage.products.length === 0 ? (
          <p className={styles.muted}>No product uses AI yet.</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className={styles.visuallyHidden}>AI usage by product this month</caption>
              <thead>
                <tr>
                  <th scope="col">Product</th>
                  <th scope="col">AI in plan</th>
                  <th scope="col">Requests</th>
                  <th scope="col">Estimated cost</th>
                  <th scope="col">Product limit</th>
                </tr>
              </thead>
              <tbody>
                {usage.products.map((p) => (
                  <tr key={p.product_code}>
                    <th scope="row">{p.product_code}</th>
                    <td>{p.ai_entitled ? "Yes" : "No"}</td>
                    <td>{p.requests}</td>
                    <td>{money(p.spent_micros)}</td>
                    <td>{p.limit_micros === null ? "No product limit" : money(p.limit_micros)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <SettingsForm policy={policy} providers={providers} currency={usage.currency} onSave={save} />

      <section aria-labelledby="ai-requests">
        <h2 id="ai-requests" className={styles.sectionTitle}>
          Recent AI requests
        </h2>
        {requests.length === 0 ? (
          <p className={styles.muted}>No AI requests yet.</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <caption className={styles.visuallyHidden}>Recent AI requests (metadata only)</caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Product</th>
                  <th scope="col">Operation</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Model</th>
                  <th scope="col">Estimated cost</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.request_id}>
                    <td>{new Date(r.created_at).toLocaleString()}</td>
                    <td>{r.product_code}</td>
                    <td>{r.skill || r.operation}</td>
                    <td>
                      {r.status === "succeeded" ? "Succeeded" : `${r.status} (${r.outcome.replace(/_/g, " ")})`}
                      {r.fallback_used ? " · fallback model" : ""}
                    </td>
                    <td className={styles.mono}>{r.model || "—"}</td>
                    <td>{money(r.cost_micros)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SettingsForm({
  policy,
  providers,
  currency,
  onSave,
}: {
  policy: AiPolicy;
  providers: AiProvider[];
  currency: string;
  onSave: (change: Parameters<typeof updateAiSettings>[1]) => void;
}) {
  const enabledId = useId();
  const budgetId = useId();
  const fallbackId = useId();
  const [enabled, setEnabled] = useState(policy.ai_enabled);
  const [budget, setBudget] = useState(
    policy.monthly_budget_micros === null ? "" : String(policy.monthly_budget_micros / 1_000_000),
  );
  const [allowed, setAllowed] = useState<string[]>(policy.allowed_providers);
  const [fallback, setFallback] = useState(policy.allow_provider_fallback);
  const budgetValue = budget.trim() === "" ? null : Math.round(Number(budget) * 1_000_000);
  const budgetValid = budgetValue === null || (Number.isFinite(budgetValue) && budgetValue >= 0);

  return (
    <form
      className={`w360-card ${styles.card} ${styles.form}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!budgetValid) return;
        onSave({
          revision: policy.revision,
          ai_enabled: enabled,
          monthly_budget_micros: budgetValue,
          allowed_providers: allowed,
          allow_provider_fallback: fallback,
        });
      }}
    >
      <h2 className={styles.cardTitle}>Settings</h2>
      <div className={styles.check}>
        <input id={enabledId} type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <label htmlFor={enabledId}>Allow products to use AI for this organization</label>
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={budgetId}>
          Monthly AI budget ({currency}, leave empty for the platform limit of{" "}
          {formatMicros(policy.platform_cap_micros, currency)})
        </label>
        <input
          id={budgetId}
          className="w360-input"
          inputMode="decimal"
          value={budget}
          aria-invalid={!budgetValid}
          onChange={(e) => setBudget(e.target.value)}
        />
      </div>
      <fieldset className={styles.fieldset}>
        <legend>Providers that may process this organization&apos;s data</legend>
        <p className={styles.muted}>
          None ticked means every approved provider. Deleting knowledge removes it from Workeasy360; a provider may
          keep what it already received for the period its own terms allow.
        </p>
        {providers.map((p) => {
          const id = `${budgetId}-${p.key}`;
          return (
            <div key={p.key} className={styles.check}>
              <input
                id={id}
                type="checkbox"
                checked={allowed.includes(p.key)}
                onChange={(e) =>
                  setAllowed(e.target.checked ? [...allowed, p.key] : allowed.filter((k) => k !== p.key))
                }
              />
              <label htmlFor={id}>
                {p.display_name}
                <span className={styles.muted}> — {describeProvider(p)}</span>
              </label>
            </div>
          );
        })}
      </fieldset>
      <div className={styles.check}>
        <input id={fallbackId} type="checkbox" checked={fallback} onChange={(e) => setFallback(e.target.checked)} />
        <label htmlFor={fallbackId}>
          If a provider is unavailable, allow another approved provider to answer instead
        </label>
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={!budgetValid}>
          Save AI settings
        </button>
      </div>
    </form>
  );
}

/** The provider's data-handling terms as the platform records them - a description, not a guarantee. */
function describeProvider(p: AiProvider): string {
  const parts = [
    p.data_region ? `region ${p.data_region}` : "region not stated",
    p.retains_inputs_days === null ? "input retention not stated" : `keeps inputs ${p.retains_inputs_days} days`,
    p.trains_on_inputs ? "may train on inputs" : "does not train on inputs",
  ];
  if (p.data_policy_note) parts.push(p.data_policy_note);
  return parts.join("; ");
}
