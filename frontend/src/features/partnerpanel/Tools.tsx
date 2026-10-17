"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { LoadGate, Status, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type ApiKeyRow,
  type ApiKeysOverview,
  type ChannelState,
  type IssuedKey,
  type TranslationsOverview,
} from "@/lib/api/partnerPanel";
import { NotAvailable, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

// --- channel manager -------------------------------------------------------------------

/** Channel Manager: the state of each outbound channel. Only the platform's own
 * email exists; the rest are shown as not available, never as a fake connection. */
export function ChannelManager() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.channels(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Channel Manager"
        lead="Where messages to your customers can go, and whether each channel is ready. A channel appears as connected only when the platform really has a provider for it."
        actions={<Link href={`${base}/communication/channel`}>Communication settings</Link>}
      />
      <LoadGate load={load} what="channels">
        {(d) => (
          <>
            <Table caption="Channels" head={["Channel", "State", "Details"]}>
              {d.channels.map((c) => (
                <tr key={c.key}>
                  <th scope="row">{c.label}</th>
                  <td>
                    <Status value={c.state satisfies ChannelState} />
                  </td>
                  <td>
                    {c.detail}
                    {c.state === "needs_setup" && c.editable && (
                      <>
                        {" "}
                        <Link href={`${base}/brand-settings`}>Set the sender</Link>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            <section className={styles.panel} aria-labelledby="channel-states">
              <h2 id="channel-states">What the states mean</h2>
              <dl className={styles.facts}>
                {d.states.flatMap((s) => [
                  <dt key={`${s.key}-t`}>{s.label}</dt>,
                  <dd key={`${s.key}-d`}>{s.meaning}</dd>,
                ])}
              </dl>
            </section>
            <NotAvailable title="What is not offered" reasons={Object.values(d.not_implemented)} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- API keys ----------------------------------------------------------------------------

/** API Key Manager: scoped credentials for the partner's own systems. The secret
 * is shown once, here, in memory only - it is never stored by the page and the
 * server keeps only a hash. Create, rotate and revoke ask for the second factor. */
export function ApiKeys() {
  const { id, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.apiKeys(id), [id]);
  const [issued, setIssued] = useState<IssuedKey | null>(null);
  const { act, busy, feedback } = useAction(reload);
  return (
    <div className={styles.page}>
      <PageHeader
        title="API Key Manager"
        lead="Keys let your own systems read a limited part of your partner data over a secure connection. A key shows its secret once, when it is made."
      />
      {feedback}
      {issued && <NewSecret issued={issued} onDone={() => setIssued(null)} />}
      <LoadGate load={load} what="API keys">
        {(d) => (
          <>
            <KeysTable
              data={d}
              manage={d.can_manage && active}
              busy={busy}
              onRotate={(key) =>
                void act(async () => {
                  setIssued(await panelApi.rotateApiKey(id, key.id, d.default_expiry_days));
                }, `${key.name} was rotated. The old secret no longer works.`)
              }
              onRevoke={(key) => void act(() => panelApi.revokeApiKey(id, key.id), `${key.name} was revoked.`)}
            />
            {d.can_manage && active && (
              <CreateKey
                data={d}
                busy={busy}
                onCreate={(body) =>
                  void act(async () => {
                    setIssued(await panelApi.createApiKey(id, body));
                  }, `${body.name} was created.`)
                }
              />
            )}
            <section className={styles.panel} aria-labelledby="key-never">
              <h2 id="key-never">What a key can never be</h2>
              <ul>
                {d.never_issued.map((t) => (
                  <li key={t}>{t}</li>
                ))}
                <li>A way to sign in to the portal, or to change anything: keys only read.</li>
              </ul>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}

function NewSecret({ issued, onDone }: { issued: IssuedKey; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className={styles.notice} aria-labelledby="new-secret-title">
      <h2 id="new-secret-title">Copy your key now</h2>
      <p>
        This is the only time <strong>{issued.key.name}</strong> is shown. Workeasy360 keeps only a fingerprint, so it cannot show it again. Store it somewhere safe, and never put it in a web page or
        a message.
      </p>
      <p>
        <code className={styles.secret} data-testid="new-secret">
          {issued.secret}
        </code>
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(issued.secret).then(
              () => setCopied(true),
              () => setCopied(false),
            );
          }}
        >
          Copy key
        </button>
        <button type="button" className="w360-btn w360-btn-primary" onClick={onDone}>
          I have stored it
        </button>
        {copied && <span role="status">Copied.</span>}
      </div>
    </section>
  );
}

function KeysTable({
  data,
  manage,
  busy,
  onRotate,
  onRevoke,
}: {
  data: ApiKeysOverview;
  manage: boolean;
  busy: boolean;
  onRotate: (key: ApiKeyRow) => void;
  onRevoke: (key: ApiKeyRow) => void;
}) {
  const [confirming, setConfirming] = useState<{ id: string; what: "rotate" | "revoke" } | null>(null);
  return (
    <Table
      caption="Your API keys"
      head={["Name", "Key", "Scope", "Status", "Created", "Last used", "Expires", "Actions"]}
      isEmpty={data.keys.length === 0}
      empty="No API keys yet."
    >
      {data.keys.map((k) => (
        <tr key={k.id}>
          <th scope="row">{k.name}</th>
          <td className={styles.mono}>{k.key_id}…</td>
          <td>{k.scopes.map((s) => s.label).join(", ") || "—"}</td>
          <td>
            <Status value={k.status} />
          </td>
          <td>{when(k.created_at)}</td>
          <td>{k.last_used_at ? when(k.last_used_at) : "Never"}</td>
          <td>{when(k.expires_at)}</td>
          <td>
            {manage && k.status === "active" && (
              <div className={styles.actions}>
                {confirming?.id === k.id ? (
                  <>
                    <span>
                      {confirming.what === "rotate" ? "The current secret stops working at once." : "This key stops working at once."}
                    </span>
                    <button
                      type="button"
                      className="w360-btn w360-btn-danger"
                      disabled={busy}
                      onClick={() => {
                        setConfirming(null);
                        (confirming.what === "rotate" ? onRotate : onRevoke)(k);
                      }}
                    >
                      Confirm {confirming.what}
                    </button>
                    <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setConfirming(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} aria-label={`Rotate ${k.name}`} onClick={() => setConfirming({ id: k.id, what: "rotate" })}>
                      Rotate
                    </button>
                    <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} aria-label={`Revoke ${k.name}`} onClick={() => setConfirming({ id: k.id, what: "revoke" })}>
                      Revoke
                    </button>
                  </>
                )}
              </div>
            )}
          </td>
        </tr>
      ))}
    </Table>
  );
}

function CreateKey({
  data,
  busy,
  onCreate,
}: {
  data: ApiKeysOverview;
  busy: boolean;
  onCreate: (body: { name: string; scopes: string[]; expires_in_days: number }) => void;
}) {
  const nameId = useId();
  const expiryId = useId();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [days, setDays] = useState(data.default_expiry_days);
  const toggle = (key: string) => setScopes((l) => (l.includes(key) ? l.filter((x) => x !== key) : [...l, key]));
  return (
    <form
      className={styles.panel}
      onSubmit={(e) => {
        e.preventDefault();
        onCreate({ name: name.trim(), scopes, expires_in_days: days });
        setName("");
        setScopes([]);
      }}
    >
      <h2>Create an API key</h2>
      <div className={styles.formGrid}>
        <div>
          <label className="w360-label" htmlFor={nameId}>
            Key name
          </label>
          <input id={nameId} className="w360-input" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="w360-label" htmlFor={expiryId}>
            Expires after
          </label>
          <select id={expiryId} className="w360-input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {data.expiry_days.map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </div>
      </div>
      <fieldset className={styles.checks}>
        <legend>Scope</legend>
        {data.scopes.map((s) => (
          <label key={s.key}>
            <input type="checkbox" checked={scopes.includes(s.key)} onChange={() => toggle(s.key)} />
            <span>
              {s.label}
              <span className={styles.muted}> — {s.description}</span>
            </span>
          </label>
        ))}
        {data.scopes.length === 0 && <p className={styles.muted}>Your role holds nothing a key could be given.</p>}
      </fieldset>
      <p className={styles.muted}>A key can only be given what your own role can already see. Up to {data.limit} keys can be active at once.</p>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !name.trim() || scopes.length === 0}>
          Create key
        </button>
      </div>
    </form>
  );
}

// --- translations --------------------------------------------------------------------------

/** Translation Manager: the customer-facing words of the partner's brand, by
 * language. Plain text only; a missing string falls back to Workeasy360's, then
 * to English. */
export function Translations() {
  const { id } = usePanel();
  const [locale, setLocale] = useState("en");
  const [load, reload] = useLoad(() => panelApi.translations(id, locale), [id, locale]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Translation Manager"
        lead="Word the labels and messages your customers see in their own language. Anything you leave blank falls back to Workeasy360's wording, then to English."
      />
      <LoadGate load={load} what="translations">
        {(d) => <TranslationForm key={`${d.locale}-${d.strings.map((s) => s.value).join("|")}`} data={d} locale={locale} onLocale={setLocale} onSaved={reload} />}
      </LoadGate>
    </div>
  );
}

function TranslationForm({
  data,
  locale,
  onLocale,
  onSaved,
}: {
  data: TranslationsOverview;
  locale: string;
  onLocale: (l: string) => void;
  onSaved: () => void;
}) {
  const { id, active } = usePanel();
  const { act, busy, feedback } = useAction(onSaved);
  const localeId = useId();
  const [draft, setDraft] = useState<Record<string, string>>(() => Object.fromEntries(data.strings.map((s) => [s.key, s.value])));
  const editable = data.can_manage && active;
  const changed = Object.fromEntries(data.strings.filter((s) => (draft[s.key] ?? "") !== s.value).map((s) => [s.key, draft[s.key] ?? ""]));
  const groups = [...new Set(data.strings.map((s) => s.group))];
  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        void act(() => panelApi.saveTranslations(id, locale, changed), "Translations saved.");
      }}
    >
      {feedback}
      <div>
        <label className="w360-label" htmlFor={localeId}>
          Language
        </label>
        <select id={localeId} className="w360-input" value={locale} onChange={(e) => onLocale(e.target.value)}>
          {data.locales.map((l) => (
            <option key={l.code} value={l.code}>
              {l.name}
              {l.translated > 0 ? ` (${l.translated} worded)` : ""}
            </option>
          ))}
        </select>
      </div>
      {groups.map((group) => (
        <fieldset key={group} className={styles.panel}>
          <legend>{group}</legend>
          {data.strings
            .filter((s) => s.group === group)
            .map((s) => (
              <div key={s.key}>
                <label className="w360-label" htmlFor={`tr-${s.key}`}>
                  {s.label}
                </label>
                <input
                  id={`tr-${s.key}`}
                  className="w360-input"
                  maxLength={s.max_length}
                  disabled={!editable || busy}
                  placeholder={s.english}
                  value={draft[s.key] ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}
                />
                <span className={styles.muted}>
                  Shown now: {s.effective || "nothing"} ({s.source === "partner" ? "your wording" : s.source === "platform" ? "Workeasy360's wording" : "English"})
                </span>
              </div>
            ))}
        </fieldset>
      ))}
      {editable && (
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || Object.keys(changed).length === 0}>
            Save translations
          </button>
        </div>
      )}
      <section className={styles.panel} aria-labelledby="tr-rules">
        <h2 id="tr-rules">How this works</h2>
        <ol>
          {data.fallback.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ol>
        <p className={styles.muted}>Plain words only: no markup, braces or code. These stay Workeasy360&apos;s and cannot be translated here:</p>
        <ul>
          {data.never_translatable.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </section>
    </form>
  );
}
