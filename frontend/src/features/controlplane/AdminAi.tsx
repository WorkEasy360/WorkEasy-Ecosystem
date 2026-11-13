"use client";

import { useId, useState } from "react";
import { adminAiApi, formatMicros, type OrganizationAi, type Registry } from "@/lib/api/intelligence";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, ReasonAction, Status, Table, useAction, useLoad, when } from "./ui";
import {
  Badge,
  BoolBadge,
  Card,
  CopyButton,
  DataTable,
  EmptyState,
  InfoTip,
  Page,
  PageHeader,
  Stack,
  Tooltip,
  formatDateTime,
  sentence,
  type Column,
} from "./kit";
import styles from "./ControlPlane.module.css";
import ai from "./AdminAi.module.css";

/**
 * Super Admin → AI platform (Task 87): which providers and models the platform
 * may use, what they are priced at, and one organization's AI policy and usage.
 *
 * A provider shows whether its credential is configured - never the value:
 * keys live in the secret manager and no API returns one. Endpoints are not
 * editable anywhere: an adapter owns its vendor's endpoint.
 */

type Provider = Registry["providers"][number];
type Model = Registry["models"][number];

const WORDS: Record<string, string> = { gpt: "GPT", ai: "AI", llm: "LLM", mini: "mini", o1: "o1", o3: "o3" };

/** "claude-opus-5" -> "Claude Opus 5"; "gpt-4o-mini" -> "GPT 4o mini". The id stays one hover or copy away. */
export function modelName(m: { model_id?: string; key: string }): string {
  const id = m.model_id || m.key.split(".").slice(1).join(".") || m.key;
  return id
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => WORDS[w.toLowerCase()] ?? (/^\d/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

/** A provider is usable only when it is active *and* its credential is set. */
function ProviderStatus({ provider: p }: { provider: Provider }) {
  if (p.status === "active" && !p.credential_configured) {
    return (
      <Tooltip content="Active, but its API key is not set in the secret manager, so no request can reach it.">
        <span tabIndex={0} className={ai.focusable}>
          <Badge tone="warning" dot>
            Needs setup
          </Badge>
        </span>
      </Tooltip>
    );
  }
  return <Status value={p.status} />;
}

function Health({ until }: { until: string | null }) {
  if (!until) {
    return (
      <Tooltip content="Requests are flowing normally.">
        <span tabIndex={0} className={ai.focusable}>
          <Badge tone="success" dot>
            Healthy
          </Badge>
        </span>
      </Tooltip>
    );
  }
  return (
    <Tooltip content={`After repeated errors, requests to this model are paused until ${when(until)}, then tried again.`}>
      <span tabIndex={0} className={ai.focusable}>
        <Badge tone="warning" dot>
          Paused until {formatDateTime(until)}
        </Badge>
      </span>
    </Tooltip>
  );
}

const providerName = (registry: Registry, key: string) => registry.providers.find((p) => p.key === key)?.display_name ?? key;

export function AdminAi() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_ai.manage");
  const [load, reload] = useLoad(() => adminAiApi.registry(), []);
  const { act, busy, feedback } = useAction(reload);

  return (
    <Page>
      <PageHeader
        title="AI platform"
        subtitle="The AI providers and models organizations may use, and what they cost."
        info="Organizations use only active models of active providers, within their own and the platform's limits. Costs shown are estimates for spend control, not invoices."
      />
      {feedback}
      <LoadGate load={load} what="the AI registry">
        {(registry) => {
          const providerColumns: Column<Provider>[] = [
            { key: "name", header: "Provider", value: (p) => p.display_name, render: (p) => p.display_name },
            { key: "status", header: "Status", value: (p) => p.status, render: (p) => <ProviderStatus provider={p} /> },
            {
              key: "credential",
              header: "Credential",
              value: (p) => p.credential_configured,
              render: (p) => <BoolBadge value={p.credential_configured} yes="Configured" no="Not configured" />,
            },
            { key: "data", header: "Data handling", wrap: true, render: (p) => p.data_policy_note || "—" },
            {
              key: "actions",
              header: "Actions",
              align: "right",
              render: (p) =>
                manage ? (
                  <ReasonAction
                    size="sm"
                    label={p.status === "active" ? `Disable ${p.display_name}` : `Enable ${p.display_name}`}
                    busy={busy}
                    onConfirm={(reason) =>
                      act(
                        () => adminAiApi.setProviderStatus(p.key, p.status === "active" ? "disabled" : "active", reason),
                        `${p.display_name} is now ${p.status === "active" ? "disabled" : "active"}.`,
                      )
                    }
                  />
                ) : null,
            },
          ];
          const modelColumns: Column<Model>[] = [
            {
              key: "model",
              header: "Model",
              value: (m) => modelName(m),
              render: (m) => (
                <Stack
                  main={
                    <Tooltip content={m.key}>
                      <span tabIndex={0} className={ai.focusable}>
                        {modelName(m)}
                      </span>
                    </Tooltip>
                  }
                  sub={
                    <span className={ai.idLine}>
                      <code className={ai.mono}>{m.key}</code>
                      <CopyButton value={m.key} label={`Copy model id ${m.key}`} />
                    </span>
                  }
                />
              ),
            },
            { key: "provider", header: "Provider", value: (m) => m.provider, render: (m) => providerName(registry, m.provider) },
            { key: "status", header: "Status", value: (m) => m.status, render: (m) => <Status value={m.status} /> },
            { key: "tier", header: "Tier", value: (m) => m.tier, render: (m) => sentence(m.tier) },
            {
              key: "capabilities",
              header: "Capabilities",
              render: (m) => (
                <span className={ai.badges}>
                  {m.capabilities.map((c) => (
                    <Badge key={c} tone="neutral">
                      {sentence(c)}
                    </Badge>
                  ))}
                </span>
              ),
            },
            {
              key: "in",
              header: "Input / M tokens",
              align: "right",
              value: (m) => m.price?.input_micros_per_mtok ?? null,
              render: (m) => (m.price ? formatMicros(m.price.input_micros_per_mtok, m.price.currency) : "No price"),
            },
            {
              key: "out",
              header: "Output / M tokens",
              align: "right",
              value: (m) => m.price?.output_micros_per_mtok ?? null,
              render: (m) => (m.price ? formatMicros(m.price.output_micros_per_mtok, m.price.currency) : "—"),
            },
            { key: "health", header: "Health", value: (m) => m.circuit_open_until ?? "", render: (m) => <Health until={m.circuit_open_until} /> },
            {
              key: "actions",
              header: "Actions",
              align: "right",
              render: (m) =>
                manage ? (
                  <ReasonAction
                    size="sm"
                    label={m.status === "active" ? `Disable ${m.key}` : `Activate ${m.key}`}
                    busy={busy}
                    onConfirm={(reason) =>
                      act(
                        () => adminAiApi.updateModel(m.key, { status: m.status === "active" ? "disabled" : "active", reason }),
                        `${m.key} is now ${m.status === "active" ? "disabled" : "active"}.`,
                      )
                    }
                  />
                ) : null,
            },
          ];
          return (
            <>
              <section className={ai.section} aria-labelledby="ai-providers">
                <h2 id="ai-providers" className={ai.title}>
                  Providers
                </h2>
                <DataTable
                  caption="AI providers"
                  columns={providerColumns.filter((c) => manage || c.key !== "actions")}
                  rows={registry.providers}
                  rowKey={(p) => p.key}
                  pageSize={0}
                  maxHeight={false}
                  hideEmptyColumns={false}
                  empty={<EmptyState icon="sparkles" title="No AI providers" compact />}
                />
              </section>
              <section className={ai.section} aria-labelledby="ai-models">
                <h2 id="ai-models" className={ai.title}>
                  Models{" "}
                  <InfoTip label="About model health">
                    Health: after repeated errors a model is paused for a short while, then tried again. Prices are per million tokens.
                  </InfoTip>
                </h2>
                <DataTable
                  caption="AI models"
                  columns={modelColumns.filter((c) => manage || c.key !== "actions")}
                  rows={registry.models}
                  rowKey={(m) => m.key}
                  pageSize={0}
                  maxHeight={false}
                  hideEmptyColumns={false}
                  empty={<EmptyState icon="sparkles" title="No AI models" compact />}
                />
              </section>
            </>
          );
        }}
      </LoadGate>
      <OrganizationLookup manage={manage} />
    </Page>
  );
}

function OrganizationLookup({ manage }: { manage: boolean }) {
  const inputId = useId();
  const [draft, setDraft] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const valid = /^[0-9a-f-]{36}$/i.test(draft.trim());

  return (
    <Card title="An organization's AI" description="Look up one organization's AI policy and this month's usage by its ID.">
      <form
        className={styles.form}
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) setOrganizationId(draft.trim());
        }}
      >
        <div className={styles.field}>
          <label className="w360-label" htmlFor={inputId}>
            Organization ID
          </label>
          <input id={inputId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} />
        </div>
        <div>
          <button type="submit" className="w360-btn w360-btn-secondary" disabled={!valid}>
            Show AI policy and usage
          </button>
        </div>
      </form>
      {organizationId && <OrganizationAiDetail organizationId={organizationId} manage={manage} />}
    </Card>
  );
}

function OrganizationAiDetail({ organizationId, manage }: { organizationId: string; manage: boolean }) {
  const [load, reload] = useLoad<OrganizationAi>(() => adminAiApi.organization(organizationId), [organizationId]);
  const { act, busy, feedback } = useAction(reload);

  return (
    <div className={styles.stack}>
      {feedback}
      <LoadGate load={load} what="this organization's AI">
        {(detail) => (
          <>
            <p>
              AI is {detail.policy.platform_suspended ? "suspended by the platform" : detail.policy.ai_enabled ? "on" : "off (by the organization)"}.
              Monthly cap {formatMicros(detail.policy.organization_cap_micros, detail.currency)}; premium models{" "}
              {detail.policy.premium_models_allowed ? "allowed" : "not allowed"}.
            </p>
            {manage && (
              <div className={styles.row}>
                <ReasonAction
                  size="sm"
                  label={detail.policy.platform_suspended ? "Lift the AI suspension" : "Suspend this organization's AI"}
                  busy={busy}
                  onConfirm={(reason) =>
                    act(
                      () => adminAiApi.setOrganizationPolicy(organizationId, { platform_suspended: !detail.policy.platform_suspended, reason }),
                      detail.policy.platform_suspended ? "AI suspension lifted." : "AI suspended for this organization.",
                    )
                  }
                />
                <ReasonAction
                  size="sm"
                  label={detail.policy.premium_models_allowed ? "Disallow premium models" : "Allow premium models"}
                  busy={busy}
                  onConfirm={(reason) =>
                    act(
                      () =>
                        adminAiApi.setOrganizationPolicy(organizationId, {
                          premium_models_allowed: !detail.policy.premium_models_allowed,
                          reason,
                        }),
                      "Premium model policy updated.",
                    )
                  }
                />
              </div>
            )}
            <Table caption="This month's AI spend by scope" head={["Scope", "Requests", "Estimated cost", "Reserved"]} numeric={["Requests", "Estimated cost", "Reserved"]} isEmpty={detail.periods.length === 0}>
              {detail.periods.map((p) => (
                <tr key={p.scope || "organization"}>
                  <th scope="row">{p.scope || "Whole organization"}</th>
                  <td className={styles.num}>{p.requests}</td>
                  <td className={styles.num}>{formatMicros(p.spent_micros, detail.currency)}</td>
                  <td className={styles.num}>{formatMicros(p.reserved_micros, detail.currency)}</td>
                </tr>
              ))}
            </Table>
            <Table
              caption="Latest AI requests (metadata only)"
              head={["When", "Product", "Operation", "Outcome", "Model", "Estimated cost"]}
              numeric={["Estimated cost"]}
              isEmpty={detail.recent_executions.length === 0}
            >
              {detail.recent_executions.map((e) => (
                <tr key={e.id}>
                  <td>{when(e.created_at)}</td>
                  <td>{e.product_code}</td>
                  <td>{e.skill_key || e.operation}</td>
                  <td>
                    <Status value={e.status} /> {e.outcome}
                  </td>
                  <td className={styles.mono}>{e.model_key || "—"}</td>
                  <td className={styles.num}>{formatMicros(e.cost_micros, detail.currency)}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </LoadGate>
    </div>
  );
}
