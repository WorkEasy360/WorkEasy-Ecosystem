"use client";

import { useId, useState } from "react";
import { adminAiApi, formatMicros, type OrganizationAi } from "@/lib/api/intelligence";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, ReasonAction, Status, Table, useAction, useLoad, when } from "./ui";
import styles from "./ControlPlane.module.css";

/**
 * Super Admin → AI (Task 87): which providers and models the platform may
 * use, what they are priced at, and one organization's AI policy and usage.
 *
 * A provider shows whether its credential is configured - never the value:
 * keys live in the secret manager and no API returns one. Endpoints are not
 * editable anywhere: an adapter owns its vendor's endpoint.
 */
export function AdminAi() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_ai.manage");
  const [load, reload] = useLoad(() => adminAiApi.registry(), []);
  const { act, busy, feedback } = useAction(reload);

  return (
    <div className={styles.stack}>
      <h1>AI platform</h1>
      <p className={styles.lead}>
        Approved AI providers and models. Organizations use only active models of active providers, within their own
        and the platform&apos;s limits. Costs shown are estimates for spend control, not invoices.
      </p>
      {feedback}
      <LoadGate load={load} what="the AI registry">
        {(registry) => (
          <>
            <Table caption="AI providers" head={["Provider", "Status", "Credential", "Data handling", ""]} isEmpty={registry.providers.length === 0}>
              {registry.providers.map((p) => (
                <tr key={p.key}>
                  <th scope="row">{p.display_name}</th>
                  <td>
                    <Status value={p.status} />
                  </td>
                  <td>{p.credential_configured ? "Configured" : "Not configured"}</td>
                  <td>{p.data_policy_note || "—"}</td>
                  <td>
                    {manage && (
                      <ReasonAction
                        label={p.status === "active" ? `Disable ${p.display_name}` : `Enable ${p.display_name}`}
                        busy={busy}
                        onConfirm={(reason) =>
                          act(
                            () => adminAiApi.setProviderStatus(p.key, p.status === "active" ? "disabled" : "active", reason),
                            `${p.display_name} is now ${p.status === "active" ? "disabled" : "active"}.`,
                          )
                        }
                      />
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <Table
              caption="AI models"
              head={["Model", "Provider", "Status", "Tier", "Capabilities", "Price per million tokens (in / out)", "Circuit", ""]}
              isEmpty={registry.models.length === 0}
            >
              {registry.models.map((m) => (
                <tr key={m.key}>
                  <th scope="row" className={styles.mono}>
                    {m.key}
                  </th>
                  <td>{m.provider}</td>
                  <td>
                    <Status value={m.status} />
                  </td>
                  <td>{m.tier}</td>
                  <td>{m.capabilities.join(", ")}</td>
                  <td>
                    {m.price
                      ? `${formatMicros(m.price.input_micros_per_mtok, m.price.currency)} / ${formatMicros(m.price.output_micros_per_mtok, m.price.currency)} (v${m.price.version})`
                      : "No price"}
                  </td>
                  <td>{m.circuit_open_until ? `Open until ${when(m.circuit_open_until)}` : "Closed"}</td>
                  <td>
                    {manage && (
                      <ReasonAction
                        label={m.status === "active" ? `Disable ${m.key}` : `Activate ${m.key}`}
                        busy={busy}
                        onConfirm={(reason) =>
                          act(
                            () => adminAiApi.updateModel(m.key, { status: m.status === "active" ? "disabled" : "active", reason }),
                            `${m.key} is now ${m.status === "active" ? "disabled" : "active"}.`,
                          )
                        }
                      />
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </LoadGate>
      <OrganizationLookup manage={manage} />
    </div>
  );
}

function OrganizationLookup({ manage }: { manage: boolean }) {
  const inputId = useId();
  const [draft, setDraft] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const valid = /^[0-9a-f-]{36}$/i.test(draft.trim());

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby="ai-org-lookup">
      <h2 id="ai-org-lookup" className={styles.cardTitle}>
        An organization&apos;s AI
      </h2>
      <form
        className={styles.form}
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) setOrganizationId(draft.trim());
        }}
      >
        <div className={styles.field}>
          <label className="w360-label" htmlFor={inputId}>
            Organization id
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
    </section>
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
            <Table caption="This month's AI spend by scope" head={["Scope", "Requests", "Estimated cost", "Reserved"]} isEmpty={detail.periods.length === 0}>
              {detail.periods.map((p) => (
                <tr key={p.scope || "organization"}>
                  <th scope="row">{p.scope || "Whole organization"}</th>
                  <td>{p.requests}</td>
                  <td>{formatMicros(p.spent_micros, detail.currency)}</td>
                  <td>{formatMicros(p.reserved_micros, detail.currency)}</td>
                </tr>
              ))}
            </Table>
            <Table
              caption="Latest AI requests (metadata only)"
              head={["When", "Product", "Operation", "Outcome", "Model", "Estimated cost"]}
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
                  <td>{formatMicros(e.cost_micros, detail.currency)}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </LoadGate>
    </div>
  );
}
