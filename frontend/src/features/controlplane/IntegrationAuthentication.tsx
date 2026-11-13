"use client";

import { useId, useState } from "react";
import {
  adminApi,
  type ConsoleCredential,
  type ConsoleIntegration,
  type IntegrationChanges,
  type IssuedConsoleCredential,
} from "@/lib/api/controlPlane";
import { FormDialog, FormPopup, dialog } from "./Modal";
import { ReasonAction, Status, when } from "./ui";
import { TextField } from "./widgets";
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
  sso_optional: "Optional SSO - WorkEasy360 or the product's own sign-in",
  sso_only: "SSO only - WorkEasy360 sign-in only",
  local_only: "Local sign-in only - the product does not use WorkEasy360 sign-in",
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
        <dt>WorkEasy360 SSO</dt>
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
            : "None (no sign-out of WorkEasy360 from the product)"}
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
 * integration and to change an existing one (inside a popup's two columns). */
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
      <div className={`${styles.field} ${dialog.span}`}>
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
      <div className={`${styles.field} ${dialog.span}`}>
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
      <fieldset className={`${styles.field} ${dialog.span}`}>
        <legend className="w360-label">Scopes (openid is always included)</legend>
        <div className={styles.row}>
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
        </div>
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

/** What the sign-in fields need before a save: the redirect URIs, and the
 * product's own sign-in page for Optional SSO (the server checks the rest). */
const signInReady = (draft: SignInDraft) =>
  lines(draft.redirect_uris).length > 0 && (draft.sign_in_mode !== "sso_optional" || Boolean(draft.login_url.trim()));

/** The sign-in settings, in a popup the caller opens (Edit authentication) and
 * `onDone` closes. A refusal stays in the popup; a save is announced through
 * the caller's `act`, which also reloads the product. */
export function AuthenticationEditor({
  code,
  integration,
  act,
  onDone,
}: {
  code: string;
  integration: ConsoleIntegration;
  busy?: boolean;
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
    <FormDialog
      title={`Authentication settings for ${integration.display_name || code}`}
      submitLabel="Save authentication settings"
      canSubmit={signInReady(draft)}
      onSubmit={async () => {
        await adminApi.updateIntegration(code, toChanges(draft));
      }}
      onDone={() => void act(async () => {}, "Authentication settings saved.")}
      onClose={onDone}
    >
      <SignInFields draft={draft} setDraft={setDraft} />
    </FormDialog>
  );
}

const NEW_DRAFT: SignInDraft = {
  sign_in_mode: "sso_only",
  login_url: "",
  redirect_uris: "",
  post_logout_redirect_uris: "",
  scopes: ["email", "profile", "organization"],
};

/** "Add product -> Integration": register the integration of a catalog
 * product that has none, in a popup. It starts as a draft with no credential. */
export function RegisterIntegration({ code, name, busy, act }: { code: string; name: string; busy: boolean; act: Act }) {
  const [displayName, setDisplayName] = useState(name);
  const [launchUrl, setLaunchUrl] = useState("");
  const [draft, setDraftState] = useState<SignInDraft>(NEW_DRAFT);
  const setDraft = (update: (d: SignInDraft) => SignInDraft) => setDraftState(update);
  return (
    <FormPopup
      label="Register integration"
      buttonClassName="w360-btn w360-btn-secondary"
      title={`Register the integration for ${name}`}
      intro="It starts as a draft with no credential. Add a credential, then activate it."
      submitLabel="Register"
      disabled={busy}
      canSubmit={Boolean(displayName.trim() && launchUrl.trim()) && signInReady(draft)}
      onOpen={() => {
        setDisplayName(name);
        setLaunchUrl("");
        setDraftState(NEW_DRAFT);
      }}
      onSubmit={async () => {
        await adminApi.registerIntegration(code, { display_name: displayName.trim(), launch_url: launchUrl.trim(), ...toChanges(draft) });
      }}
      // The panel turns into the registered integration's, so the outcome is the page's.
      onDone={() => void act(async () => {}, "Integration registered as a draft. Add a credential, then activate it.")}
    >
      <TextField label="Display name" required maxLength={128} value={displayName} onChange={setDisplayName} />
      <TextField label="Launch URL (https)" type="url" required value={launchUrl} onChange={setLaunchUrl} />
      <SignInFields draft={draft} setDraft={setDraft} />
    </FormPopup>
  );
}

/** Credentials by public reference; add a public key or generate a secret
 * (shown once), each in a popup; revoke. Loaded on demand. Every change is
 * announced through `act`, which also reloads the product. */
export function CredentialsPanel({ code, manage, busy, act }: { code: string; manage: boolean; busy: boolean; act: Act }) {
  const [rows, setRows] = useState<ConsoleCredential[] | null>(null);
  const [issued, setIssued] = useState<IssuedConsoleCredential | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [pem, setPem] = useState("");
  const [label, setLabel] = useState("");
  const working = busy;
  const pemId = useId();

  const reload = () =>
    adminApi
      .integrationCredentials(code)
      .then((r) => setRows(r.credentials))
      .catch(() => setProblem("Couldn't load the credentials."));

  const add = async (kind: "public_key" | "client_secret") => {
    const result = await adminApi.addIntegrationCredential(code, {
      kind,
      public_key_pem: kind === "public_key" ? pem : undefined,
      label,
    });
    setIssued(result);
    setPem("");
    setLabel("");
    await reload();
  };
  const announce = (kind: "public_key" | "client_secret") =>
    void act(async () => {}, kind === "public_key" ? "Public key added." : "Client secret generated - copy it now; it is shown once.");
  const labelField = <TextField label="Label (optional)" maxLength={100} value={label} onChange={setLabel} />;

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
        <p className={styles.muted}>No credentials. WorkEasy360 sign-in cannot complete until one is added.</p>
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
        <div className={styles.row}>
          <FormPopup
            label="Add public key"
            title="Add a public key"
            submitLabel="Add public key"
            disabled={working}
            canSubmit={Boolean(pem.trim())}
            onOpen={() => {
              setPem("");
              setLabel("");
            }}
            onSubmit={() => add("public_key")}
            onDone={() => announce("public_key")}
          >
            <div className={`${styles.field} ${dialog.span}`}>
              <label className="w360-label" htmlFor={pemId}>
                Product public key (PEM, RSA or EC P-256) - recommended
              </label>
              <textarea id={pemId} className="w360-input" rows={4} value={pem} onChange={(e) => setPem(e.target.value)} />
            </div>
            {labelField}
          </FormPopup>
          <FormPopup
            label="Generate client secret"
            buttonClassName="w360-btn w360-btn-secondary"
            title="Generate a client secret"
            intro="The secret is shown once, in the answer to the request that makes it, and never again."
            size="sm"
            submitLabel="Generate client secret"
            disabled={working}
            onOpen={() => setLabel("")}
            onSubmit={() => add("client_secret")}
            onDone={() => announce("client_secret")}
          >
            {labelField}
          </FormPopup>
        </div>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
