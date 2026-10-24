"use client";

import { useId, useState } from "react";
import {
  adminApi,
  type ConsoleCredential,
  type ConsoleIntegration,
  type IntegrationChanges,
  type IssuedConsoleCredential,
} from "@/lib/api/controlPlane";
import { ReasonAction, Status, when } from "./ui";
import styles from "./ControlPlane.module.css";

/**
 * Super Admin -> Products -> a product -> Authentication (ADR-044).
 *
 * How a product signs people in, as Workeasy360 knows it: whether Workeasy360
 * sign-in works for it, the product's own declared mode, its client id and
 * exact redirect URIs, where it may be returned after a Workeasy360 sign-out,
 * its own sign-in page and scopes - and its credentials, by public reference
 * only. A generated client secret is shown once, in the answer to the request
 * that made it, and never again. Every change goes through the server's
 * audited manifest write; nothing here names a product.
 */

export const SIGN_IN_MODE_LABELS: Record<ConsoleIntegration["sign_in_mode"], string> = {
  sso_optional: "Optional SSO - Workeasy360 or the product's own sign-in",
  sso_only: "SSO only - Workeasy360 sign-in only",
  local_only: "Local sign-in only - the product does not use Workeasy360 sign-in",
};

const SSO_STATE: Record<ConsoleIntegration["sso_state"], string> = {
  enabled: "Enabled",
  disabled: "Disabled",
  integration_required: "Integration required",
};

const OPTIONAL_SCOPES = ["profile", "email", "organization"] as const;

const lines = (raw: string) =>
  raw
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

export function AuthenticationFacts({ integration }: { integration: ConsoleIntegration }) {
  return (
    <dl className={styles.factsGrid}>
      <div>
        <dt>Workeasy360 SSO</dt>
        <dd>{SSO_STATE[integration.sso_state] ?? integration.sso_state}</dd>
      </div>
      <div>
        <dt>Mode</dt>
        <dd>{SIGN_IN_MODE_LABELS[integration.sign_in_mode] ?? integration.sign_in_mode}</dd>
      </div>
      <div>
        <dt>Client ID</dt>
        <dd className={styles.mono}>{integration.client_id}</dd>
      </div>
      <div>
        <dt>Redirect URIs (exact match)</dt>
        <dd className={styles.mono}>
          {integration.redirect_uris.length ? integration.redirect_uris.map((u) => <div key={u}>{u}</div>) : "None"}
        </dd>
      </div>
      <div>
        <dt>Sign-out return URIs</dt>
        <dd className={styles.mono}>
          {integration.post_logout_redirect_uris.length
            ? integration.post_logout_redirect_uris.map((u) => <div key={u}>{u}</div>)
            : "None (no sign-out of Workeasy360 from the product)"}
        </dd>
      </div>
      <div>
        <dt>Product sign-in page</dt>
        <dd className={styles.mono}>{integration.login_url || "None"}</dd>
      </div>
      <div>
        <dt>Scopes</dt>
        <dd>{integration.scopes.join(" ")}</dd>
      </div>
    </dl>
  );
}

/** The fields an operator sets for sign-in. Used to register a new
 * integration and to change an existing one. */
function SignInFields({
  draft,
  setDraft,
}: {
  draft: SignInDraft;
  setDraft: (update: (d: SignInDraft) => SignInDraft) => void;
}) {
  const ids = { mode: useId(), login: useId(), redirects: useId(), logout: useId() };
  return (
    <>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.mode}>
          Sign-in mode
        </label>
        <select
          id={ids.mode}
          className="w360-input"
          value={draft.sign_in_mode}
          onChange={(e) => setDraft((d) => ({ ...d, sign_in_mode: e.target.value as SignInDraft["sign_in_mode"] }))}
        >
          {(Object.keys(SIGN_IN_MODE_LABELS) as SignInDraft["sign_in_mode"][]).map((mode) => (
            <option key={mode} value={mode}>
              {SIGN_IN_MODE_LABELS[mode]}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.login}>
          Product sign-in page (https{draft.sign_in_mode === "sso_optional" ? ", required for Optional SSO" : ", optional"})
        </label>
        <input
          id={ids.login}
          className="w360-input"
          type="url"
          required={draft.sign_in_mode === "sso_optional"}
          value={draft.login_url}
          onChange={(e) => setDraft((d) => ({ ...d, login_url: e.target.value }))}
        />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.redirects}>
          Redirect URIs - one per line, matched exactly (no wildcards)
        </label>
        <textarea
          id={ids.redirects}
          className="w360-input"
          rows={3}
          required
          value={draft.redirect_uris}
          onChange={(e) => setDraft((d) => ({ ...d, redirect_uris: e.target.value }))}
        />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.logout}>
          Sign-out return URIs - one per line (optional)
        </label>
        <textarea
          id={ids.logout}
          className="w360-input"
          rows={2}
          value={draft.post_logout_redirect_uris}
          onChange={(e) => setDraft((d) => ({ ...d, post_logout_redirect_uris: e.target.value }))}
        />
      </div>
      <fieldset className={styles.field}>
        <legend className="w360-label">Scopes (openid is always included)</legend>
        {OPTIONAL_SCOPES.map((scope) => (
          <label key={scope} className="w360-label">
            <input
              type="checkbox"
              checked={draft.scopes.includes(scope)}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  scopes: e.target.checked ? [...d.scopes, scope] : d.scopes.filter((s) => s !== scope),
                }))
              }
            />{" "}
            {scope}
          </label>
        ))}
      </fieldset>
    </>
  );
}

interface SignInDraft {
  sign_in_mode: ConsoleIntegration["sign_in_mode"];
  login_url: string;
  redirect_uris: string;
  post_logout_redirect_uris: string;
  scopes: string[];
}

function toChanges(draft: SignInDraft): IntegrationChanges {
  return {
    sign_in_mode: draft.sign_in_mode,
    login_url: draft.login_url.trim(),
    redirect_uris: lines(draft.redirect_uris),
    post_logout_redirect_uris: lines(draft.post_logout_redirect_uris),
    scopes: ["openid", ...draft.scopes.filter((s) => s !== "openid")],
  };
}

export function AuthenticationEditor({
  code,
  integration,
  busy,
  act,
  onDone,
}: {
  code: string;
  integration: ConsoleIntegration;
  busy: boolean;
  act: Act;
  onDone: () => void;
}) {
  const [draft, setDraftState] = useState<SignInDraft>({
    sign_in_mode: integration.sign_in_mode,
    login_url: integration.login_url,
    redirect_uris: integration.redirect_uris.join("\n"),
    post_logout_redirect_uris: integration.post_logout_redirect_uris.join("\n"),
    scopes: integration.scopes.filter((s) => s !== "openid"),
  });
  const setDraft = (update: (d: SignInDraft) => SignInDraft) => setDraftState(update);
  return (
    <form
      className={styles.form}
      aria-label={`Authentication settings for ${integration.display_name || code}`}
      onSubmit={(e) => {
        e.preventDefault();
        void act(() => adminApi.updateIntegration(code, toChanges(draft)), "Authentication settings saved.");
        onDone();
      }}
    >
      <SignInFields draft={draft} setDraft={setDraft} />
      <div className={styles.row}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Save authentication settings
        </button>
      </div>
    </form>
  );
}

/** "Add product -> Integration": register the integration of a catalog
 * product that has none. It starts as a draft with no credential. */
export function RegisterIntegration({ code, name, busy, act }: { code: string; name: string; busy: boolean; act: Act }) {
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState(name);
  const [launchUrl, setLaunchUrl] = useState("");
  const [draft, setDraftState] = useState<SignInDraft>({
    sign_in_mode: "sso_only",
    login_url: "",
    redirect_uris: "",
    post_logout_redirect_uris: "",
    scopes: ["email", "profile", "organization"],
  });
  const setDraft = (update: (d: SignInDraft) => SignInDraft) => setDraftState(update);
  const ids = { name: useId(), launch: useId() };
  if (!open) {
    return (
      <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(true)}>
        Register integration
      </button>
    );
  }
  return (
    <form
      className={styles.form}
      aria-label={`Register the integration for ${name}`}
      onSubmit={(e) => {
        e.preventDefault();
        void act(
          () => adminApi.registerIntegration(code, { display_name: displayName.trim(), launch_url: launchUrl.trim(), ...toChanges(draft) }),
          "Integration registered as a draft. Add a credential, then activate it.",
        );
        setOpen(false);
      }}
    >
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.name}>
          Display name
        </label>
        <input id={ids.name} className="w360-input" required maxLength={128} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </div>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={ids.launch}>
          Launch URL (https)
        </label>
        <input id={ids.launch} className="w360-input" type="url" required value={launchUrl} onChange={(e) => setLaunchUrl(e.target.value)} />
      </div>
      <SignInFields draft={draft} setDraft={setDraft} />
      <div className={styles.row}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Register
        </button>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** Credentials by public reference; add a public key or generate a secret
 * (shown once); revoke. Loaded on demand. Every change goes through `act`,
 * which announces the outcome. */
export function CredentialsPanel({ code, manage, busy, act }: { code: string; manage: boolean; busy: boolean; act: Act }) {
  const [rows, setRows] = useState<ConsoleCredential[] | null>(null);
  const [issued, setIssued] = useState<IssuedConsoleCredential | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pem, setPem] = useState("");
  const [label, setLabel] = useState("");
  const working = busy;
  const ids = { pem: useId(), label: useId() };

  const reload = () =>
    adminApi
      .integrationCredentials(code)
      .then((r) => setRows(r.credentials))
      .catch(() => setProblem("Couldn't load the credentials."));

  const add = (kind: "public_key" | "client_secret") =>
    act(async () => {
      const result = await adminApi.addIntegrationCredential(code, {
        kind,
        public_key_pem: kind === "public_key" ? pem : undefined,
        label,
      });
      setIssued(result);
      setPem("");
      setLabel("");
      await reload();
    }, kind === "public_key" ? "Public key added." : "Client secret generated - copy it now; it is shown once.");

  if (rows === null) {
    return (
      <div className={styles.row}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void reload()}>
          Show credentials
        </button>
        {problem && <p role="alert">{problem}</p>}
      </div>
    );
  }
  return (
    <div className={styles.stack}>
      <h3 className={styles.cardTitle}>Credentials</h3>
      {issued?.client_secret && (
        <div role="status" className={`w360-card ${styles.card}`}>
          <p>
            <strong>Client secret for credential {issued.credential_id}:</strong>
          </p>
          <p className={styles.mono}>{issued.client_secret}</p>
          <p className={styles.muted}>{issued.notice}</p>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setIssued(null)}>
            I have stored it - hide
          </button>
        </div>
      )}
      {issued && !issued.client_secret && <p role="status">Public key added as credential {issued.credential_id}.</p>}
      {rows.length === 0 ? (
        <p className={styles.muted}>No credentials. Workeasy360 sign-in cannot complete until one is added.</p>
      ) : (
        <ul className={styles.stack}>
          {rows.map((c) => (
            <li key={c.credential_id} className={styles.row}>
              <span className={styles.mono}>{c.credential_id}</span>
              <span>{c.kind === "public_key" ? `public key (${c.algorithm})` : "client secret"}</span>
              <Status value={c.status} />
              {c.expires_at && <span className={styles.muted}>expires {when(c.expires_at)}</span>}
              {c.label && <span className={styles.muted}>{c.label}</span>}
              {manage && c.status === "active" && (
                <ReasonAction
                  label="Revoke"
                  busy={working}
                  onConfirm={(reason) =>
                    void act(async () => {
                      await adminApi.revokeIntegrationCredential(code, c.credential_id, reason);
                      await reload();
                    }, "Credential revoked.")
                  }
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {manage && (
        <form
          className={styles.form}
          aria-label="Add a credential"
          onSubmit={(e) => {
            e.preventDefault();
            void add("public_key");
          }}
        >
          <div className={styles.field}>
            <label className="w360-label" htmlFor={ids.pem}>
              Product public key (PEM, RSA or EC P-256) - recommended
            </label>
            <textarea id={ids.pem} className="w360-input" rows={4} value={pem} onChange={(e) => setPem(e.target.value)} />
          </div>
          <div className={styles.field}>
            <label className="w360-label" htmlFor={ids.label}>
              Label (optional)
            </label>
            <input id={ids.label} className="w360-input" maxLength={100} value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div className={styles.row}>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={working || !pem.trim()}>
              Add public key
            </button>
            <button type="button" className="w360-btn w360-btn-secondary" disabled={working} onClick={() => void add("client_secret")}>
              Generate client secret
            </button>
          </div>
        </form>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
