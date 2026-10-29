"use client";

import { useId, useState } from "react";
import { adminApi, type ConsoleIntegration, type ConsoleProduct } from "@/lib/api/controlPlane";
import { AuthenticationEditor, AuthenticationFacts, CredentialsPanel, RegisterIntegration } from "../IntegrationAuthentication";
import { ReasonAction, Status, when } from "../ui";
import { TextField } from "../widgets";
import { INTEGRATION_BADGE, INTEGRATION_LABEL, integrationState, looksLikeHttps } from "./model";
import cp from "../ControlPlane.module.css";
import styles from "./ProductManager.module.css";

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

/** Launch, health and contact settings - the registry's own audited manifest
 * write (moved here from the Phase 11 products console unchanged). */
function IntegrationEditor({
  integration,
  busy,
  onSave,
}: {
  integration: ConsoleIntegration;
  busy: boolean;
  onSave: (changes: Partial<Pick<ConsoleIntegration, "display_name" | "launch_url" | "health_url" | "contact_email">>, message: string) => void;
}) {
  const [draft, setDraft] = useState({
    display_name: integration.display_name,
    launch_url: integration.launch_url,
    health_url: integration.health_url,
    contact_email: integration.contact_email,
  });
  const ids = { name: useId(), launch: useId(), health: useId(), contact: useId() };
  const field = (key: keyof typeof draft) => ({
    value: draft[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setDraft((d) => ({ ...d, [key]: e.target.value })),
  });
  return (
    <form
      className={cp.form}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft, "Integration updated.");
      }}
    >
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.name}>
          Display name
        </label>
        <input id={ids.name} className="w360-input" required maxLength={128} {...field("display_name")} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.launch}>
          Launch URL (https)
        </label>
        <input id={ids.launch} className="w360-input" type="url" required pattern="https://.*" {...field("launch_url")} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.health}>
          Health URL (https, optional - the launch URL is checked otherwise)
        </label>
        <input id={ids.health} className="w360-input" type="url" pattern="https://.*" {...field("health_url")} />
      </div>
      <div className={cp.field}>
        <label className="w360-label" htmlFor={ids.contact}>
          Contact email (optional)
        </label>
        <input id={ids.contact} className="w360-input" type="email" {...field("contact_email")} />
      </div>
      <div className={styles.toolbar}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Save
        </button>
      </div>
    </form>
  );
}

/** The product's integration: status, single sign-on, health and launch URL,
 * with suspend/resume, a health check and its sign-in set-up and credentials
 * folded away. Never a secret: credentials are listed by public reference. */
export function IntegrationPanel({
  code,
  name,
  integration,
  busy,
  manage,
  act,
  reload,
}: {
  code: string;
  name: string;
  integration: ConsoleIntegration | null;
  busy: boolean;
  manage: boolean;
  act: Act;
  reload: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editingAuth, setEditingAuth] = useState(false);
  const [health, setHealth] = useState<string | null>(null);

  if (!integration) {
    return (
      <div className={styles.integration}>
        <p className={styles.sectionLabel}>Integration</p>
        <p className={styles.muted}>No integration registered for this product.</p>
        {manage && <RegisterIntegration code={code} name={name} busy={busy} act={act} />}
      </div>
    );
  }
  return (
    <div className={styles.integration}>
      <div className={styles.integrationHead}>
        <p className={styles.sectionLabel}>Integration</p>
        {manage && (
          <div className={styles.toolbar}>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={busy}
              onClick={() =>
                void adminApi
                  .checkIntegrationHealth(code)
                  .then((r) => {
                    setHealth(`${r.status}${r.detail ? ` (${r.detail})` : ""}`);
                    reload();
                  })
                  .catch(() => setHealth("Could not check right now."))
              }
            >
              Check now
            </button>
            {integration.status === "active" && (
              <ReasonAction
                label="Suspend"
                danger
                busy={busy}
                onConfirm={(r) => act(() => adminApi.setIntegrationStatus(code, "suspended", r), "Integration suspended.")}
              />
            )}
            {integration.status === "suspended" && (
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                disabled={busy}
                onClick={() => act(() => adminApi.setIntegrationStatus(code, "active", ""), "Integration resumed.")}
              >
                Resume
              </button>
            )}
            <button type="button" className="w360-btn w360-btn-secondary" aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
              {editing ? "Cancel" : "Edit"}
            </button>
          </div>
        )}
      </div>
      <dl className={styles.integrationFacts}>
        <div>
          <dt>Status</dt>
          <dd>
            <Status value={integration.status} />
          </dd>
        </div>
        <div>
          <dt>Single sign-on</dt>
          <dd>{integration.sign_in_mode === "local_only" ? "Not used" : integration.sso === "connected" ? "Connected" : "Integration required"}</dd>
        </div>
        <div>
          <dt>Health</dt>
          <dd>
            <Status value={integration.health.status} />
            {integration.health.checked_at && <span className={styles.muted}> checked {when(integration.health.checked_at)}</span>}
          </dd>
        </div>
        <div className={styles.wide}>
          <dt>Launch URL</dt>
          <dd className={styles.url}>{integration.launch_url}</dd>
        </div>
      </dl>
      {integration.status === "suspended" && integration.suspension_reason && (
        <p className={styles.muted}>Suspended: {integration.suspension_reason}</p>
      )}
      {health && <p role="status">{health}</p>}
      {editing && (
        <div className={styles.editor}>
          <IntegrationEditor
            integration={integration}
            busy={busy}
            onSave={(changes, message) => {
              void act(() => adminApi.updateIntegration(code, changes), message);
              setEditing(false);
            }}
          />
        </div>
      )}
      <details className={styles.disclosure}>
        <summary>
          <h3 className={styles.disclosureTitle}>Authentication &amp; credentials</h3>
          <span className={styles.disclosureSub}>Client ID, redirect URIs, sign-in mode, scopes</span>
          <span aria-hidden="true" className={styles.chevron}>
            ›
          </span>
        </summary>
        <div className={styles.disclosureBody}>
          <AuthenticationFacts integration={integration} />
          {manage && (
            <div className={styles.toolbar}>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setEditingAuth((v) => !v)}>
                {editingAuth ? "Cancel" : "Edit authentication"}
              </button>
              {integration.status === "draft" && (
                <button
                  type="button"
                  className="w360-btn w360-btn-secondary"
                  disabled={busy}
                  onClick={() => act(() => adminApi.setIntegrationStatus(code, "active", ""), "Integration activated.")}
                >
                  Activate
                </button>
              )}
            </div>
          )}
          {editingAuth && <AuthenticationEditor code={code} integration={integration} busy={busy} act={act} onDone={() => setEditingAuth(false)} />}
          <CredentialsPanel code={code} manage={manage} busy={busy} act={act} />
        </div>
      </details>
    </div>
  );
}

/** Integration tab: the product's application URL (a catalog field) and its
 * registry integration (launch URL, SSO, health, credentials). */
export function IntegrationTab({
  product: p,
  canSeeIntegrations,
  manageCatalog,
  manageIntegration,
  busy,
  act,
  reload,
}: {
  product: ConsoleProduct;
  canSeeIntegrations: boolean;
  manageCatalog: boolean;
  manageIntegration: boolean;
  busy: boolean;
  act: Act;
  reload: () => void;
}) {
  const [appUrl, setAppUrl] = useState(p.application_url);
  const [error, setError] = useState<string | undefined>();
  const state = integrationState(p.integration, p.launch_url);
  return (
    <div className={styles.page}>
      <section className={styles.card} aria-labelledby="integration-summary">
        <h2 id="integration-summary" className={styles.cardTitle}>
          Integration
        </h2>
        <dl className={styles.facts}>
          <div>
            <dt>Integration status</dt>
            <dd>
              <span className={INTEGRATION_BADGE[state]}>{INTEGRATION_LABEL[state]}</span>
            </dd>
          </div>
          <div>
            <dt>Integration type</dt>
            <dd>{!p.integration ? "—" : p.integration.sign_in_mode === "local_only" ? "External" : "OIDC"}</dd>
          </div>
          <div>
            <dt>SSO enabled</dt>
            <dd>{!p.integration ? "—" : p.integration.sign_in_mode === "local_only" ? "No" : "Yes"}</dd>
          </div>
          <div>
            <dt>Launch URL</dt>
            <dd className={styles.url}>{p.launch_url || "Not set"}</dd>
          </div>
        </dl>
        {manageCatalog ? (
          <form
            className={styles.inlineForm}
            onSubmit={(e) => {
              e.preventDefault();
              const value = appUrl.trim();
              if (value && !looksLikeHttps(value)) {
                setError("The application URL must be an https:// address.");
                return;
              }
              setError(undefined);
              void act(() => adminApi.updateProduct(p.id, { application_url: value }), "Application URL saved.");
            }}
          >
            <TextField label="Application URL" type="url" value={appUrl} error={error} placeholder="https://" onChange={setAppUrl} hint="The product's own address." />
            <div>
              <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || appUrl.trim() === p.application_url}>
                Save application URL
              </button>
            </div>
          </form>
        ) : (
          <p>
            Application URL: <span className={styles.url}>{p.application_url || "Not set"}</span>
          </p>
        )}
      </section>
      {canSeeIntegrations ? (
        <section className={styles.card} aria-label="Launch, sign-in and credentials">
          <IntegrationPanel code={p.code} name={p.name} integration={p.integration} busy={busy} manage={manageIntegration} act={act} reload={reload} />
        </section>
      ) : (
        <p className={styles.muted}>Integration details need the integration view permission.</p>
      )}
    </div>
  );
}
