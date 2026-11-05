"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { consoleApi, type SecurityPolicy } from "@/lib/api/console";
import { LoadGate, useLoad, when } from "@/features/controlplane/ui";
import { FormPopup } from "@/features/controlplane/Modal";
import cp from "@/features/controlplane/ControlPlane.module.css";
import styles from "./Console.module.css";
import local from "./ConsoleScreens.module.css";
import { PageHeader } from "./consoleUi";
import { useOrgAccess } from "./OrgAccess";

/**
 * The organization's own access policy: authentication rules and the network
 * allow-list. Two pages, one policy row with one version - every save sends
 * the version it was loaded at, so two administrators saving at once get a
 * conflict instead of silently overwriting each other. Each page shows the
 * settings as they are; they are changed in an Edit popup (2026-11-05).
 */

const MAX_PASSWORD_LENGTH = 64;

const SESSION_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "No limit" },
  { value: "60", label: "1 hour" },
  { value: "240", label: "4 hours" },
  { value: "480", label: "8 hours" },
  { value: "1440", label: "24 hours" },
  { value: "10080", label: "7 days" },
];

function isConflict(err: unknown): boolean {
  return err instanceof ApiError && err.code === "concurrent_modification";
}

function ConflictNotice({ onReload }: { onReload: () => void }) {
  return (
    <div className={local.notice}>
      <p>Someone else changed these settings while you were editing. Reload to see their changes, then make yours again.</p>
      <p>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onReload}>
          Reload settings
        </button>
      </p>
    </div>
  );
}

// --- authentication ---------------------------------------------------------------------

/** A session length in words, for the page (the popup offers the same list). */
function sessionLabel(minutes: number | null): string {
  if (minutes == null) return "No limit";
  return SESSION_OPTIONS.find((o) => o.value === String(minutes))?.label ?? `${minutes} minutes`;
}

export function AuthenticationPolicyScreen() {
  const { slug, can } = useOrgAccess();
  const [load, reload] = useLoad(() => consoleApi.securityPolicy(slug), [slug]);
  const [conflict, setConflict] = useState(false);
  const canEdit = can("platform.security.update");

  return (
    <div className={styles.page}>
      <PageHeader
        title="Authentication"
        lead="How members of this organization sign in: how long a sign-in lasts, and how long passwords must be."
      />
      {conflict && (
        <ConflictNotice
          onReload={() => {
            setConflict(false);
            reload();
          }}
        />
      )}
      <LoadGate load={load} what="the authentication settings">
        {(policy) => (
          <AuthenticationSettings
            policy={policy}
            canEdit={canEdit}
            onSave={async (body) => {
              try {
                await consoleApi.updateSecurityPolicy(slug, body);
                setConflict(false);
              } catch (err) {
                if (isConflict(err)) setConflict(true);
                throw err;
              }
            }}
            onDone={reload}
          />
        )}
      </LoadGate>
    </div>
  );
}

/** The settings as they are, and an Edit popup for them (2026-11-05, every form in a popup). */
function AuthenticationSettings({
  policy,
  canEdit,
  onSave,
  onDone,
}: {
  policy: SecurityPolicy;
  canEdit: boolean;
  onSave: (body: {
    session_max_age_minutes: number | null;
    password_min_length: number | null;
    version: number;
  }) => Promise<void>;
  onDone: () => void;
}) {
  const sessionId = useId();
  const sessionHintId = useId();
  const lengthId = useId();
  const lengthHintId = useId();
  const initialAge = () => (policy.session_max_age_minutes == null ? "" : String(policy.session_max_age_minutes));
  const initialLength = () => (policy.password_min_length == null ? "" : String(policy.password_min_length));
  const [maxAge, setMaxAge] = useState(initialAge);
  const [minLength, setMinLength] = useState(initialLength);
  const floor = policy.platform_password_min_length;
  const lengthValue = minLength.trim();
  const lengthOk =
    lengthValue === "" ||
    (/^\d{1,3}$/.test(lengthValue) && Number(lengthValue) >= floor && Number(lengthValue) <= MAX_PASSWORD_LENGTH);
  // A length saved by another tool that is not in the list stays selectable
  // rather than being silently changed on the next save.
  const options = SESSION_OPTIONS.some((o) => o.value === maxAge)
    ? SESSION_OPTIONS
    : [...SESSION_OPTIONS, { value: maxAge, label: `${maxAge} minutes` }];

  return (
    <section className={`w360-card ${styles.panel} ${local.section}`} aria-label="Authentication settings">
      {!canEdit && (
        <p className={cp.muted}>You can view these settings. Changing them needs permission to manage security.</p>
      )}
      <dl className={local.details}>
        <div>
          <dt>Session length</dt>
          <dd>{sessionLabel(policy.session_max_age_minutes)}</dd>
        </div>
        <div>
          <dt>Minimum password length</dt>
          <dd>
            {policy.password_min_length == null
              ? `Platform minimum (${floor} characters)`
              : `${policy.password_min_length} characters`}
          </dd>
        </div>
      </dl>
      <p className={local.textBlock}>
        Password rules apply from each member&apos;s next sign-in: anyone whose password is shorter than the minimum is
        asked to choose a new one then. Rules about kinds of characters (capitals, digits, symbols) are deliberately not
        offered - they make passwords harder to remember without making them meaningfully stronger. Length does.
      </p>
      {policy.updated_at && <p className={cp.muted}>Last changed {when(policy.updated_at)}.</p>}
      {canEdit && (
        <div className={local.actions}>
          <FormPopup
            label="Edit"
            buttonClassName="w360-btn w360-btn-secondary"
            buttonAriaLabel="Edit authentication settings"
            title="Edit authentication settings"
            size="sm"
            submitLabel="Save authentication settings"
            canSubmit={lengthOk}
            onOpen={() => {
              setMaxAge(initialAge());
              setMinLength(initialLength());
            }}
            onSubmit={async () => {
              await onSave({
                session_max_age_minutes: maxAge === "" ? null : Number(maxAge),
                password_min_length: lengthValue === "" ? null : Number(lengthValue),
                version: policy.version,
              });
              return "Authentication settings saved.";
            }}
            onDone={onDone}
          >
            <div className={cp.field}>
              <label className="w360-label" htmlFor={sessionId}>
                Session length
              </label>
              <select
                id={sessionId}
                className="w360-input"
                value={maxAge}
                onChange={(e) => setMaxAge(e.target.value)}
                aria-describedby={sessionHintId}
              >
                {options.map((o) => (
                  <option key={o.value || "none"} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <p id={sessionHintId} className="w360-hint">
                How long a sign-in lasts for this organization before members must sign in again.
              </p>
            </div>
            <div className={cp.field}>
              <label className="w360-label" htmlFor={lengthId}>
                Minimum password length
              </label>
              <input
                id={lengthId}
                className={`w360-input ${local.numberInput}`}
                type="number"
                inputMode="numeric"
                min={floor}
                max={MAX_PASSWORD_LENGTH}
                placeholder={String(floor)}
                value={minLength}
                onChange={(e) => setMinLength(e.target.value)}
                aria-invalid={!lengthOk || undefined}
                aria-describedby={lengthHintId}
              />
              <p id={lengthHintId} className={lengthOk ? "w360-hint" : "w360-error-text"}>
                {lengthOk
                  ? `Leave empty to use the platform minimum of ${floor} characters. At most ${MAX_PASSWORD_LENGTH}.`
                  : `Enter a number from ${floor} to ${MAX_PASSWORD_LENGTH}, or leave it empty.`}
              </p>
            </div>
          </FormPopup>
        </div>
      )}
    </section>
  );
}

// --- network -------------------------------------------------------------------------------

let rowSeq = 0;
const row = (value: string) => ({ id: ++rowSeq, value });

export function NetworkPolicyScreen() {
  const { slug, can } = useOrgAccess();
  const [load, reload] = useLoad(() => consoleApi.networkPolicy(slug), [slug]);
  const [conflict, setConflict] = useState(false);
  const [lockout, setLockout] = useState<string | null>(null);
  const canEdit = can("platform.security.update");

  return (
    <div className={styles.page}>
      <PageHeader
        title="Network access"
        lead="Allow this organization to be opened only from networks you trust, such as your office."
      />
      {conflict && (
        <ConflictNotice
          onReload={() => {
            setConflict(false);
            reload();
          }}
        />
      )}
      <LoadGate load={load} what="the network settings">
        {(policy) => (
          <NetworkSettings
            policy={policy}
            canEdit={canEdit}
            lockout={lockout}
            onOpen={() => setLockout(null)}
            onSave={async (body, yourNetwork) => {
              setLockout(null);
              try {
                await consoleApi.updateNetworkPolicy(slug, body);
                setConflict(false);
              } catch (err) {
                if (err instanceof ApiError && err.code === "network_lockout") {
                  const net = (err.body as { your_network?: string } | undefined)?.your_network;
                  setLockout(net || yourNetwork || "your current network");
                }
                if (isConflict(err)) setConflict(true);
                throw err;
              }
            }}
            onDone={reload}
          />
        )}
      </LoadGate>
    </div>
  );
}

/** The allow-list as it is, and an Edit popup for it (2026-11-05, every form in a popup). */
function NetworkSettings({
  policy,
  canEdit,
  lockout,
  onOpen,
  onSave,
  onDone,
}: {
  policy: SecurityPolicy;
  canEdit: boolean;
  lockout: string | null;
  onOpen: () => void;
  onSave: (body: { ip_allowlist_enabled: boolean; ip_allowlist: string[]; version: number }, yourNetwork: string) => Promise<void>;
  onDone: () => void;
}) {
  const enabledId = useId();
  const enabledHintId = useId();
  const listId = useId();
  const inputBase = useId();
  const initialRows = () => (policy.ip_allowlist.length ? policy.ip_allowlist.map(row) : [row("")]);
  const [enabled, setEnabled] = useState(policy.ip_allowlist_enabled);
  const [rows, setRows] = useState(initialRows);
  const inputs = useRef(new Map<number, HTMLInputElement>());
  const addButton = useRef<HTMLButtonElement | null>(null);
  const focusNext = useRef<number | "add" | null>(null);
  const yourNetwork = policy.your_network ?? "";
  const entries = rows.map((r) => r.value.trim()).filter(Boolean);
  const hasMine = yourNetwork !== "" && entries.includes(yourNetwork);
  const emptyWhileOn = enabled && entries.length === 0;

  // Moving focus after a row is added or removed: to the new row, or - when
  // the removed row's button is gone - to the row above it. The request is
  // kept until the element it names exists: an earlier commit's effect may
  // run first and must not use it up.
  useEffect(() => {
    const target = focusNext.current;
    if (target === null) return;
    const element = target === "add" ? addButton.current : inputs.current.get(target);
    if (!element) return;
    focusNext.current = null;
    element.focus();
  }, [rows]);

  function addRow(value = "") {
    const next = row(value);
    focusNext.current = next.id;
    setRows((current) => {
      const blanks = current.filter((r) => r.value.trim() === "");
      // "Add my network" fills an empty row rather than leaving one behind.
      if (value && blanks.length === current.length) return [next];
      return [...current, next];
    });
  }

  function removeRow(id: number) {
    const index = rows.findIndex((r) => r.id === id);
    const remaining = rows.filter((r) => r.id !== id);
    focusNext.current = remaining.length ? remaining[Math.max(0, index - 1)].id : "add";
    setRows(remaining.length ? remaining : [row("")]);
  }

  return (
    <section className={`w360-card ${styles.panel} ${local.section}`} aria-label="Network access settings">
      {!canEdit && (
        <p className={cp.muted}>You can view these settings. Changing them needs permission to manage security.</p>
      )}
      <dl className={local.details}>
        <div>
          <dt>Only allow listed networks</dt>
          <dd>{policy.ip_allowlist_enabled ? "On" : "Off"}</dd>
        </div>
        <div>
          <dt>Allowed networks</dt>
          <dd>
            {policy.ip_allowlist.length ? (
              <ul className={local.networkList}>
                {policy.ip_allowlist.map((net) => (
                  <li key={net} className={cp.mono}>
                    {net}
                  </li>
                ))}
              </ul>
            ) : (
              "None"
            )}
          </dd>
        </div>
        {policy.your_address && (
          <div>
            <dt>You&apos;re connecting from</dt>
            <dd className={cp.mono}>{policy.your_address}</dd>
          </div>
        )}
      </dl>
      {policy.updated_at && <p className={cp.muted}>Last changed {when(policy.updated_at)}.</p>}
      {canEdit && (
        <div className={local.actions}>
          <FormPopup
            label="Edit"
            buttonClassName="w360-btn w360-btn-secondary"
            buttonAriaLabel="Edit network access"
            title="Edit network access"
            columns={1}
            submitLabel="Save network access"
            canSubmit={!emptyWhileOn}
            onOpen={() => {
              onOpen();
              setEnabled(policy.ip_allowlist_enabled);
              setRows(initialRows());
            }}
            onSubmit={async () => {
              await onSave({ ip_allowlist_enabled: enabled, ip_allowlist: entries, version: policy.version }, yourNetwork);
              return "Network access saved.";
            }}
            onDone={onDone}
          >
            <div className={cp.field}>
              <div className={local.check}>
                <input
                  id={enabledId}
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  aria-describedby={enabledHintId}
                />
                <label htmlFor={enabledId}>Only allow access from the networks below</label>
              </div>
              <p id={enabledHintId} className="w360-hint">
                Applies to people opening this organization and signing in to its products. Products&apos; own server-to-server
                calls are not affected.
              </p>
            </div>

            <fieldset className={local.section}>
              <legend className="w360-label">Allowed networks</legend>
              <p className={cp.muted}>
                One IP address or range per row, for example 203.0.113.7 or 203.0.113.0/24.
                {policy.your_address && <> You&apos;re connecting from {policy.your_address}.</>}
              </p>
              <ul id={listId} className={local.networkList}>
                {rows.map((r, index) => (
                  <li key={r.id} className={local.networkRow}>
                    <label className="w360-visually-hidden" htmlFor={`${inputBase}-${r.id}`}>
                      Allowed network {index + 1}
                    </label>
                    <input
                      id={`${inputBase}-${r.id}`}
                      ref={(el) => {
                        if (el) inputs.current.set(r.id, el);
                        else inputs.current.delete(r.id);
                      }}
                      className={`w360-input ${cp.mono}`}
                      value={r.value}
                      spellCheck={false}
                      autoComplete="off"
                      maxLength={64}
                      onChange={(e) =>
                        setRows((current) => current.map((c) => (c.id === r.id ? { ...c, value: e.target.value } : c)))
                      }
                    />
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      aria-label={`Remove allowed network ${index + 1}`}
                      onClick={() => removeRow(r.id)}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              <div className={local.actions}>
                <button ref={addButton} type="button" className="w360-btn w360-btn-secondary" onClick={() => addRow()}>
                  Add a network
                </button>
                {yourNetwork && !hasMine && (
                  <button type="button" className="w360-btn w360-btn-secondary" onClick={() => addRow(yourNetwork)}>
                    Add my network ({yourNetwork})
                  </button>
                )}
              </div>
            </fieldset>

            {lockout && (
              <div className={local.notice}>
                <p>
                  <strong>Not saved - this would lock you out.</strong> Your own network ({lockout}) isn&apos;t in the list, so
                  the next page you open would be refused. Add it, or turn the allow-list off.
                </p>
              </div>
            )}
            {emptyWhileOn && <p className="w360-error-text">Add at least one network before turning the allow-list on.</p>}
          </FormPopup>
        </div>
      )}
    </section>
  );
}
