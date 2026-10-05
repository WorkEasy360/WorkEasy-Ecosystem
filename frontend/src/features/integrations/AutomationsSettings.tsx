"use client";

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  createRule,
  getEventCatalog,
  listConnections,
  listExecutions,
  listRules,
  setRuleStatus,
} from "@/lib/api/integrations";
import type {
  AutomationExecution,
  AutomationRule,
  CatalogEvent,
  ConditionRule,
  Connection,
  RuleDefinition,
  RuleStatus,
} from "@/lib/api/types";
import { useStepUp } from "@/features/security/useStepUp";
import { describeError } from "./ConnectionsSettings";
import styles from "./Integrations.module.css";

type Load =
  | { status: "loading" }
  | { status: "forbidden" }
  | { status: "error" }
  | { status: "ready"; rules: AutomationRule[]; catalog: CatalogEvent[]; connections: Connection[] };

const OPERATORS: { value: ConditionRule["op"]; label: string }[] = [
  { value: "equals", label: "equals" },
  { value: "not_equals", label: "does not equal" },
  { value: "greater_than", label: "is greater than" },
  { value: "less_than", label: "is less than" },
  { value: "contains", label: "contains" },
  { value: "exists", label: "exists" },
];

const MAX_CONDITIONS = 5;

const BADGE: Record<RuleStatus, string> = {
  draft: "w360-badge w360-badge-neutral",
  active: "w360-badge w360-badge-success",
  paused: "w360-badge w360-badge-warning",
  archived: "w360-badge w360-badge-neutral",
};

/** A condition value as typed: numbers stay numbers, true/false booleans -
 * the server compares types strictly, so "250" and 250 are different. */
export function parseValue(raw: string, op: ConditionRule["op"]): ConditionRule["value"] {
  if (op === "exists") return raw.trim().toLowerCase() !== "false";
  const trimmed = raw.trim();
  if (trimmed === "true" || trimmed === "false") return trimmed === "true";
  if (trimmed !== "" && !Number.isNaN(Number(trimmed))) return Number(trimmed);
  return raw;
}

/**
 * Settings → Automations (Task 80): name, trigger, conditions, one action,
 * activate/pause. Deliberately a form, not a canvas: one trigger, a short
 * list of conditions, an action.
 */
export function AutomationsSettings({ orgSlug }: { orgSlug: string }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { run, stepUp } = useStepUp();

  function refresh() {
    Promise.all([listRules(orgSlug), getEventCatalog(orgSlug), listConnections(orgSlug)])
      .then(([rules, catalog, connections]) =>
        setLoad({
          status: "ready",
          rules: rules.results,
          catalog: catalog.results,
          connections: connections.results.filter((c) => c.status === "active"),
        }),
      )
      .catch((err: unknown) =>
        setLoad({ status: err instanceof ApiError && err.status === 403 ? "forbidden" : "error" }),
      );
  }

  useEffect(refresh, [orgSlug]);

  async function act(action: () => Promise<void>, success: string) {
    setError(null);
    setMessage(null);
    try {
      await run(async () => {
        await action();
        setMessage(success);
        refresh();
      });
    } catch (err) {
      setError(describeError(err));
    }
  }

  if (load.status === "loading") return <p aria-live="polite">Loading automations…</p>;
  if (load.status === "forbidden") return <p role="alert">You don&apos;t have permission to view automations.</p>;
  if (load.status === "error") return <p role="alert">Couldn&apos;t load automations right now.</p>;

  return (
    <div className={styles.stack}>
      {stepUp}
      {message && <p role="status">{message}</p>}
      {error && (
        <p className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      <RuleForm
        catalog={load.catalog}
        connections={load.connections}
        onCreate={(name, definition) =>
          act(() => createRule(orgSlug, name, definition).then(), "Automation saved as a draft.")
        }
      />
      {load.rules.length === 0 ? (
        <p>No automations yet.</p>
      ) : (
        load.rules.map((rule) => (
          <RuleCard key={rule.id} orgSlug={orgSlug} rule={rule} onAct={act} />
        ))
      )}
    </div>
  );
}

function RuleForm({
  catalog,
  connections,
  onCreate,
}: {
  catalog: CatalogEvent[];
  connections: Connection[];
  onCreate: (name: string, definition: RuleDefinition) => void;
}) {
  const nameId = useId();
  const triggerId = useId();
  const intervalId = useId();
  const eventId = useId();
  const connectionId = useId();
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState<"event" | "schedule">("event");
  const [eventRef, setEventRef] = useState(catalog[0]?.ref ?? "");
  const [minutes, setMinutes] = useState("60");
  const [target, setTarget] = useState(connections[0]?.id ?? "");
  const [conditions, setConditions] = useState<{ path: string; op: ConditionRule["op"]; value: string }[]>([]);

  const canSubmit =
    name.trim() !== "" && target !== "" && (trigger === "schedule" || eventRef !== "");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onCreate(name.trim(), {
      trigger:
        trigger === "event"
          ? { kind: "event", event: eventRef }
          : { kind: "schedule", schedule: { kind: "interval", every_minutes: Number(minutes) } },
      conditions: {
        match: "all",
        rules: conditions
          .filter((c) => c.path.trim())
          .map((c) => ({ path: `payload.${c.path.trim()}`, op: c.op, value: parseValue(c.value, c.op) })),
      },
      actions: [{ type: "connector.invoke", config: { connection_id: target } }],
    });
  }

  return (
    <form className={`w360-card ${styles.card} ${styles.form}`} onSubmit={submit}>
      <h2 className={styles.cardTitle}>New automation</h2>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={nameId}>
          Name
        </label>
        <input id={nameId} className="w360-input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={triggerId}>
          Run when
        </label>
        <select id={triggerId} className="w360-input" value={trigger} onChange={(e) => setTrigger(e.target.value as "event" | "schedule")}>
          <option value="event">An event arrives</option>
          <option value="schedule">On a schedule</option>
        </select>
      </div>
      {trigger === "event" ? (
        <div className={styles.field}>
          <label className="w360-label" htmlFor={eventId}>
            Event
          </label>
          <select id={eventId} className="w360-input" value={eventRef} onChange={(e) => setEventRef(e.target.value)}>
            {catalog.length === 0 && <option value="">No events available</option>}
            {catalog.map((event) => (
              <option key={event.ref} value={event.ref}>
                {event.ref}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <div className={styles.field}>
          <label className="w360-label" htmlFor={intervalId}>
            Every (minutes, at least 15)
          </label>
          <input id={intervalId} className="w360-input" type="number" min={15} step={15} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </div>
      )}

      {trigger === "event" && (
        <fieldset className={styles.fieldset}>
          <legend>Only if (all must match)</legend>
          {conditions.map((condition, index) => (
            <ConditionRow
              key={index}
              index={index}
              value={condition}
              onChange={(next) => setConditions(conditions.map((c, i) => (i === index ? next : c)))}
              onRemove={() => setConditions(conditions.filter((_, i) => i !== index))}
            />
          ))}
          {conditions.length < MAX_CONDITIONS && (
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              onClick={() => setConditions([...conditions, { path: "", op: "equals", value: "" }])}
            >
              Add condition
            </button>
          )}
        </fieldset>
      )}

      <div className={styles.field}>
        <label className="w360-label" htmlFor={connectionId}>
          Send the event to
        </label>
        <select id={connectionId} className="w360-input" value={target} onChange={(e) => setTarget(e.target.value)}>
          {connections.length === 0 && <option value="">No active connections</option>}
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.display_name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={!canSubmit}>
          Save automation
        </button>
      </div>
    </form>
  );
}

function ConditionRow({
  index,
  value,
  onChange,
  onRemove,
}: {
  index: number;
  value: { path: string; op: ConditionRule["op"]; value: string };
  onChange: (next: { path: string; op: ConditionRule["op"]; value: string }) => void;
  onRemove: () => void;
}) {
  const pathId = useId();
  const opId = useId();
  const valueId = useId();
  return (
    <div className={styles.conditionRow}>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={pathId}>
          Field {index + 1} (in the event data)
        </label>
        <input id={pathId} className="w360-input" placeholder="order.total" value={value.path} onChange={(e) => onChange({ ...value, path: e.target.value })} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={opId}>
          Operator {index + 1}
        </label>
        <select id={opId} className="w360-input" value={value.op} onChange={(e) => onChange({ ...value, op: e.target.value as ConditionRule["op"] })}>
          {OPERATORS.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={valueId}>
          Value {index + 1}
        </label>
        <input id={valueId} className="w360-input" value={value.value} onChange={(e) => onChange({ ...value, value: e.target.value })} />
      </div>
      <button type="button" className="w360-btn w360-btn-secondary" onClick={onRemove} aria-label={`Remove condition ${index + 1}`}>
        Remove
      </button>
    </div>
  );
}

function RuleCard({
  orgSlug,
  rule,
  onAct,
}: {
  orgSlug: string;
  rule: AutomationRule;
  onAct: (action: () => Promise<void>, success: string) => void;
}) {
  const [executions, setExecutions] = useState<AutomationExecution[] | null>(null);
  const transitions: { to: RuleStatus; label: string }[] =
    rule.status === "active"
      ? [{ to: "paused", label: "Pause" }]
      : rule.status === "archived"
        ? []
        : [{ to: "active", label: "Activate" }];

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={`rule-${rule.id}`}>
      <div className={styles.cardHeader}>
        <h2 id={`rule-${rule.id}`} className={styles.cardTitle}>
          {rule.name}
        </h2>
        <span className={BADGE[rule.status]}>{rule.status}</span>
      </div>
      <p className={styles.muted}>
        {rule.trigger_kind === "event" ? <>On <code>{rule.trigger_filter}</code></> : "On a schedule"}
        {rule.next_run_at && <> · next run {new Date(rule.next_run_at).toLocaleString()}</>}
      </p>
      <div className={styles.row}>
        {transitions.map((t) => (
          <button
            key={t.to}
            type="button"
            className={`w360-btn ${t.to === "active" ? "w360-btn-primary" : "w360-btn-secondary"}`}
            onClick={() => onAct(() => setRuleStatus(orgSlug, rule, t.to).then(), `Automation ${t.to}.`)}
          >
            {t.label}
          </button>
        ))}
        {rule.status !== "archived" && (
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => onAct(() => setRuleStatus(orgSlug, rule, "archived").then(), "Automation archived.")}
          >
            Archive
          </button>
        )}
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          aria-expanded={executions !== null}
          onClick={() =>
            executions === null
              ? listExecutions(orgSlug, rule.id)
                  .then((page) => setExecutions(page.results))
                  .catch(() => setExecutions([]))
              : setExecutions(null)
          }
        >
          Recent runs
        </button>
      </div>
      {executions !== null && (
        <ul className={styles.muted}>
          {executions.length === 0 && <li>No runs yet.</li>}
          {executions.map((e) => (
            <li key={e.id}>
              {new Date(e.created_at).toLocaleString()} — {e.status}
              {e.failure_category && <> ({e.failure_category.replaceAll("_", " ")})</>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
