"use client";

import Link from "next/link";
import { useState } from "react";
import { LoadGate, MoreButton, Status, Table, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { countryName, countryOptions } from "@/features/controlplane/countries";
import { dialog, FormPopup } from "@/features/controlplane/Modal";
import { SelectField, TextField } from "@/features/controlplane/widgets";
import { partnerApi, type Brand } from "@/lib/api/controlPlane";
import { accountApi } from "@/lib/api/console";
import { ChangePasswordForm } from "@/features/console/ChangePasswordForm";
import { panelApi, type OwnProfile } from "@/lib/api/partnerPanel";
import { NotAvailable, PageHeader, SubNav, usePanel } from "./PanelKit";
import { useFieldChecks } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

// --- settings: the business profile ---------------------------------------------------------

function zones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

/** Settings → My Account & Security (ADR-063): the signed-in person's own
 * password and sessions, and the partner's activity trail. The business profile
 * is under Settings → Business & KYC. */
export function Account() {
  const { can, partner } = usePanel();
  return (
    <div className={styles.page}>
      <PageHeader
        title="My Account & Security"
        lead="How you sign in: your password and where you are signed in. Your business details are under Business & KYC."
      />
      {/* Profile & Security (2026-11-10): the person's own password and sessions.
          Never in a staff guest view - that session is the staff member's own. */}
      {partner?.guest ? (
        <p className={styles.muted}>Your own sign-in is not shown in a guest session.</p>
      ) : (
        <SignInSecurity />
      )}
      {can("activity.view") && <ActivityTrail />}
    </div>
  );
}

/** The business profile, for Settings → Business & KYC (ADR-063). */
export function BusinessProfile() {
  const { id } = usePanel();
  const [load, reload] = useLoad(() => panelApi.profile(id), [id]);
  return (
    <LoadGate load={load} what="your business profile">
      {(p) => <ProfileForm profile={p} onSaved={reload} />}
    </LoadGate>
  );
}

/** The signed-in person's password and sessions: their own Workeasy360 account, the
 * same in every portal, so nothing here depends on the partner's status. */
function SignInSecurity() {
  const [sessions, reloadSessions] = useLoad(() => accountApi.sessions(), []);
  const { act, busy, feedback } = useAction(reloadSessions);
  return (
    <section className={styles.card} aria-labelledby="signin-security">
      <h2 id="signin-security">Your sign-in</h2>
      <p className={styles.muted}>Your own password and where you are signed in. Changing your password signs out every other session.</p>
      <p className={styles.actionsRow}>
        <ChangePasswordForm popup onChanged={reloadSessions} />
        <button
          type="button"
          className="w360-btn w360-btn-secondary"
          disabled={busy}
          onClick={() =>
            void act(async () => {
              await accountApi.revokeOtherSessions();
            }, "Signed out every other session.")
          }
        >
          Sign out all other sessions
        </button>
      </p>
      {feedback}
      <LoadGate load={sessions} what="your sessions">
        {(d) => (
          <Table caption="Where you are signed in" head={["Device", "Last active", ""]} isEmpty={d.sessions.length === 0} empty="No sessions.">
            {d.sessions.map((s) => (
              <tr key={s.session_id}>
                <th scope="row">
                  {s.device_label || "Unknown device"}
                  {s.current ? " (this device)" : ""}
                </th>
                <td>{when(s.last_active_at)}</td>
                <td>
                  {!s.current && (
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      disabled={busy}
                      onClick={() => void act(() => accountApi.revokeSession(s.session_id), `Signed out the session on ${s.device_label || "that device"}.`)}
                    >
                      Sign out
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </LoadGate>
    </section>
  );
}

/** A read-only list of current values (label, then value; "Not set" when empty). */
function Values({ rows }: { rows: [string, string][] }) {
  return (
    <dl className={styles.summary}>
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value || "Not set"}</dd>
        </div>
      ))}
    </dl>
  );
}

function ProfileForm({ profile, onSaved }: { profile: OwnProfile; onSaved: () => void }) {
  const { id, can, active } = usePanel();
  const editable = can("profile.manage") && active;
  const b = profile.business;
  const [name, setName] = useState(b.display_name);
  const [legal, setLegal] = useState(b.legal_name);
  const [country, setCountry] = useState(b.country);
  const [timezone, setTimezone] = useState(b.timezone);
  const [tax, setTax] = useState(b.tax_number);
  const [registration, setRegistration] = useState(b.registration_number);
  const [website, setWebsite] = useState(b.website);
  const [businessEmail, setBusinessEmail] = useState(b.business_email);
  const [phone, setPhone] = useState(b.phone);
  const [supportEmail, setSupportEmail] = useState(profile.support.email);
  const [supportPhone, setSupportPhone] = useState(profile.support.phone);
  const [supportUrl, setSupportUrl] = useState(profile.support.url);
  const primary = profile.contacts.primary ?? {};
  const [pName, setPName] = useState(primary.name ?? "");
  const [pEmail, setPEmail] = useState(primary.email ?? "");
  const billing = profile.contacts.billing ?? { same_as_primary: true };
  const [billingSame, setBillingSame] = useState(billing.same_as_primary !== false);
  const [bName, setBName] = useState(billing.name ?? "");
  const [bEmail, setBEmail] = useState(billing.email ?? "");
  const options = zones();
  const checks = useFieldChecks();

  // Each popup opens on the saved values: what was typed in a cancelled one is dropped.
  function prefill() {
    setName(b.display_name);
    setLegal(b.legal_name);
    setCountry(b.country);
    setTimezone(b.timezone);
    setTax(b.tax_number);
    setRegistration(b.registration_number);
    setWebsite(b.website);
    setBusinessEmail(b.business_email);
    setPhone(b.phone);
    setSupportEmail(profile.support.email);
    setSupportPhone(profile.support.phone);
    setSupportUrl(profile.support.url);
    setPName(primary.name ?? "");
    setPEmail(primary.email ?? "");
    setBillingSame(billing.same_as_primary !== false);
    setBName(billing.name ?? "");
    setBEmail(billing.email ?? "");
  }

  async function save(): Promise<string> {
    checks.check();
    await panelApi.saveProfile(id, profile.version, {
      business: {
        display_name: name.trim(),
        legal_name: legal.trim(),
        country,
        timezone: timezone.trim(),
        tax_number: tax.trim(),
        registration_number: registration.trim(),
        website: website.trim(),
        business_email: businessEmail.trim(),
        phone: phone.trim(),
      },
      support: { email: supportEmail.trim(), phone: supportPhone.trim(), url: supportUrl.trim() },
      contacts: {
        primary: { name: pName.trim(), email: pEmail.trim() },
        billing: billingSame ? { same_as_primary: true } : { name: bName.trim(), email: bEmail.trim() },
      },
    });
    return "Saved.";
  }

  /** One section's Edit button and popup (all three save the whole profile, as the one form did). */
  const edit = (what: string, intro: string | undefined, fields: React.ReactNode) =>
    editable && (
      <div className={styles.actions}>
        <FormPopup
          label="Edit"
          buttonAriaLabel={`Edit ${what}`}
          buttonClassName="w360-btn w360-btn-secondary"
          title={`Edit ${what}`}
          intro={intro}
          submitLabel="Save settings"
          canSubmit={Boolean(name.trim())}
          onOpen={prefill}
          onSubmit={save}
          onDone={onSaved}
        >
          {fields}
          {checks.probe}
        </FormPopup>
      </div>
    );

  const billingContact = billing.same_as_primary !== false ? "Same as the primary contact" : [billing.name, billing.email].filter(Boolean).join(", ");

  return (
    <div className={styles.page}>
      <section className={styles.panel} aria-labelledby="account-business">
        <div className={styles.panelHead}>
          <h2 id="account-business">Business</h2>
          {edit(
            "business details",
            undefined,
            <>
              <TextField label="Business name" value={name} onChange={setName} required maxLength={120} />
              <TextField label="Legal name" value={legal} onChange={setLegal} maxLength={200} />
              <SelectField label="Country" value={country} onChange={setCountry} options={countryOptions("Not set")} />
              <div>
                <TextField label="Time zone" value={timezone} onChange={setTimezone} list="partner-zones" hint="For example Asia/Kolkata" maxLength={64} />
                <datalist id="partner-zones">
                  {options.map((z) => (
                    <option key={z} value={z} />
                  ))}
                </datalist>
              </div>
              <TextField label="Tax identifier" value={tax} onChange={setTax} maxLength={64} />
              <TextField label="Registration number" value={registration} onChange={setRegistration} maxLength={64} />
              <TextField label="Website" value={website} onChange={setWebsite} type="url" hint="Must start with https://" />
              <TextField label="Business email" value={businessEmail} onChange={setBusinessEmail} type="email" />
              <TextField label="Business phone" value={phone} onChange={setPhone} type="tel" />
            </>,
          )}
        </div>
        <dl className={styles.summary}>
          {(
            [
              ["Business name", b.display_name],
              ["Legal name", b.legal_name],
              ["Country", b.country ? countryName(b.country) : ""],
              ["Time zone", b.timezone],
              ["Tax identifier", b.tax_number],
              ["Registration number", b.registration_number],
              ["Website", b.website],
              ["Business email", b.business_email],
              ["Business phone", b.phone],
            ] as [string, string][]
          ).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value || "Not set"}</dd>
            </div>
          ))}
          <div>
            <dt>Payout currency</dt>
            <dd>
              {profile.payout.currency}
              <span className="w360-hint"> Fixed when the account was created: commission is denominated in it.</span>
            </dd>
          </div>
        </dl>
      </section>
      <section className={styles.panel} aria-labelledby="account-support">
        <div className={styles.panelHead}>
          <h2 id="account-support">Support details your customers see</h2>
          {edit(
            "support details",
            "What your customers see when they need help.",
            <>
              <TextField label="Support email" value={supportEmail} onChange={setSupportEmail} type="email" />
              <TextField label="Support phone" value={supportPhone} onChange={setSupportPhone} type="tel" />
              <div className={dialog.span}>
                <TextField label="Support page" value={supportUrl} onChange={setSupportUrl} type="url" hint="Must start with https://" />
              </div>
            </>,
          )}
        </div>
        <Values
          rows={[
            ["Support email", profile.support.email],
            ["Support phone", profile.support.phone],
            ["Support page", profile.support.url],
          ]}
        />
      </section>
      <section className={styles.panel} aria-labelledby="account-contacts">
        <div className={styles.panelHead}>
          <h2 id="account-contacts">Contacts</h2>
          {edit(
            "contacts",
            undefined,
            <>
              <TextField label="Primary contact name" value={pName} onChange={setPName} maxLength={120} />
              <TextField label="Primary contact email" value={pEmail} onChange={setPEmail} type="email" />
              <label className={`w360-label ${dialog.span}`}>
                <input type="checkbox" checked={billingSame} onChange={(e) => setBillingSame(e.target.checked)} /> Billing contact is the same as the primary contact
              </label>
              {!billingSame && (
                <>
                  <TextField label="Billing contact name" value={bName} onChange={setBName} maxLength={120} />
                  <TextField label="Billing contact email" value={bEmail} onChange={setBEmail} type="email" />
                </>
              )}
            </>,
          )}
        </div>
        <Values
          rows={[
            ["Primary contact name", primary.name ?? ""],
            ["Primary contact email", primary.email ?? ""],
            ["Billing contact", billingContact],
          ]}
        />
      </section>
      {!editable && <p className={styles.muted}>You can read these details; changing them needs an administrator.</p>}
    </div>
  );
}

// --- authentication ------------------------------------------------------------------------------

export function AuthConfiguration() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.authentication(id), [id]);
  const [features] = useLoad(() => panelApi.features(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Auth Configuration" lead="How you and your customers sign in. This is Workeasy360's secure sign-in; a partner chooses only how it is presented, never how it is secured." />
      <LoadGate load={load} what="authentication">
        {(a) => (
          <>
            <section className={styles.panel} aria-labelledby="signin">
              <h2 id="signin">Sign-in</h2>
              <dl className={styles.facts}>
                <dt>Method</dt>
                <dd>Workeasy360 secure sign-in</dd>
                <dt>Where</dt>
                <dd className={styles.mono}>{a.sign_in_url}</dd>
                <dt>On a custom domain</dt>
                <dd>{a.summary}</dd>
                <dt>Brand domains</dt>
                <dd>
                  {a.brand_domains.length === 0
                    ? "None"
                    : a.brand_domains.map((d) => (
                        <span key={d.hostname}>
                          {d.hostname} <Status value={d.status} />{" "}
                        </span>
                      ))}
                </dd>
                <dt>Your team</dt>
                <dd>
                  {a.team.members} {a.team.members === 1 ? "person" : "people"} can sign in for this partner
                </dd>
              </dl>
            </section>
            <div className={styles.twoCol}>
              <section className={styles.panel} aria-labelledby="wl">
                <h2 id="wl">Controlled by Workeasy360</h2>
                <ul>
                  {a.controlled_by_workeasy360.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              </section>
              <section className={styles.panel} aria-labelledby="you">
                <h2 id="you">Yours to choose</h2>
                <ul>
                  {a.partner_choices.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
                <Link href={`${base}/brand-settings`}>Brand settings</Link>
              </section>
            </div>
            <LoadGate load={features} what="what is available">
              {(f) => <NotAvailable title="Not a partner setting" reasons={[f.not_implemented.auth_policy ?? ""]} />}
            </LoadGate>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- communication ---------------------------------------------------------------------------------

function CommunicationNav() {
  const { base } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/communication/channel`, label: "Channel" },
        { href: `${base}/communication/templates`, label: "Templates" },
        // ADR-053: Alerts and Logs have no capability behind them yet, so no tab
        // leads to a page that could only say so.
      ]}
    />
  );
}

export function CommunicationChannels() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.communication(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Communication" lead="How the platform contacts your customers on your behalf." />
      <CommunicationNav />
      <LoadGate load={load} what="channels">
        {(c) => (
          <>
            <Table caption="Channels" head={["Channel", "Status", "Sent through", "Sender name", "Reply-to"]}>
              {c.channels.map((ch) => (
                <tr key={ch.key}>
                  <th scope="row">{ch.label}</th>
                  <td>
                    <Status value={ch.status === "active" ? "active" : "disabled"} />
                    {ch.status !== "active" && <span className={styles.muted}> not available</span>}
                  </td>
                  <td>{ch.via ?? "—"}</td>
                  <td>{ch.sender_name || (ch.status === "active" ? "Workeasy360" : "—")}</td>
                  <td>{ch.reply_to || "—"}</td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>
              The sender name and reply-to for emails sent in your name are set in <Link href={`${base}/brand-settings`}>Brand settings</Link>. The sending address always stays Workeasy360&apos;s,
              so mail is authenticated.
            </p>
            <NotAvailable title="What is not offered" reasons={[c.not_implemented.partner_smtp ?? "", c.not_implemented.whatsapp_sms_webhook ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function CommunicationTemplates() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.communication(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Communication" lead="The emails sent in your name. Their wording is the platform's; only approved variables can appear in them." />
      <CommunicationNav />
      <LoadGate load={load} what="templates">
        {(c) => (
          <>
            <Table caption="Templates" head={["Email", "Subject", "Variables", "When it is sent"]}>
              {c.templates.map((t) => (
                <tr key={t.key}>
                  <th scope="row">{t.name}</th>
                  <td>{t.subject}</td>
                  <td className={styles.mono}>{t.variables.join(", ")}</td>
                  <td>{t.description}</td>
                </tr>
              ))}
            </Table>
            <NotAvailable title="Editing templates" reasons={[c.not_implemented.partner_templates ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function CommunicationAlerts() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.communication(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Communication" lead="Choosing who is told about which event." />
      <CommunicationNav />
      <LoadGate load={load} what="alerts">
        {(c) => <NotAvailable title="Alerts are not available yet" reasons={[c.not_implemented.partner_alerts ?? ""]} />}
      </LoadGate>
    </div>
  );
}

export function CommunicationLogs() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.communication(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Communication" lead="Delivery history for messages sent in your name." />
      <CommunicationNav />
      <LoadGate load={load} what="logs">
        {(c) => <NotAvailable title="No delivery log yet" reasons={[c.not_implemented.logs ?? ""]} />}
      </LoadGate>
    </div>
  );
}

// --- integrations and shared resources ---------------------------------------------------------

export function Integrations() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.features(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Integrations" lead="Connecting your account to other systems." />
      <LoadGate load={load} what="integrations">
        {(f) => <NotAvailable title="Not available for partner accounts" reasons={[f.not_implemented.integrations ?? ""]} />}
      </LoadGate>
    </div>
  );
}

export function SharedResources() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.features(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Shared resources" lead="Brand assets, documents and collateral you share." />
      <LoadGate load={load} what="shared resources">
        {(f) => <NotAvailable title="Not available yet" reasons={[f.not_implemented.shared_resources ?? ""]} />}
      </LoadGate>
    </div>
  );
}

// --- activity ---------------------------------------------------------------------------------------

/** The partner's own activity trail, from the platform's tamper-evident audit
 * log: what was done on the account and by whom. It lives on the Account page
 * (and its newest entries on the dashboard) rather than as a menu of its own. */
function ActivityTrail() {
  const { id } = usePanel();
  const paged = usePaged((cursor) => panelApi.activity(id, cursor), [id]);
  return (
    <section className={styles.panel} aria-labelledby="activity-title">
      <h2 id="activity-title">Activity</h2>
      <p className={styles.muted}>What has been done on your account, and by whom. This comes from the platform&apos;s tamper-evident audit log.</p>
      {paged.state === "loading" && <p aria-live="polite">Loading activity…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to activity.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load activity.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Activity" head={["When", "What", "About", "By"]} isEmpty={paged.rows.length === 0} empty="Nothing has happened yet.">
            {paged.rows.map((a) => (
              <tr key={a.id}>
                <td>{when(a.occurred_at)}</td>
                <th scope="row">{a.label}</th>
                <td>{a.target || "—"}</td>
                <td>{a.actor}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </section>
  );
}

// --- appearance ---------------------------------------------------------------------------------------

/** Colour schemes. Each is a fixed set of six hex tokens the server validates;
 * nothing here is markup, CSS or script, and a scheme only changes how the
 * brand colours are drawn. */
const SCHEMES: {
  key: string;
  label: string;
  dot: "dotEmerald" | "dotBlue" | "dotViolet" | "dotAmber" | "dotRose" | "dotSlate";
  accent: string;
  theme: Record<string, string>;
}[] = [
  { key: "emerald", label: "Emerald", dot: "dotEmerald", accent: "#0a8043", theme: { primary: "#0a8043", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#0f1b2d", accent: "#16c060", border: "#d9e6df" } },
  { key: "blue", label: "Blue", dot: "dotBlue", accent: "#2549b8", theme: { primary: "#2549b8", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#0f1b2d", accent: "#5b7fe0", border: "#d6defa" } },
  { key: "violet", label: "Violet", dot: "dotViolet", accent: "#6b3fb8", theme: { primary: "#6b3fb8", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#1b1030", accent: "#9d79e0", border: "#e2d8f5" } },
  { key: "amber", label: "Amber", dot: "dotAmber", accent: "#8a5a00", theme: { primary: "#8a5a00", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#2a1c00", accent: "#d99a1c", border: "#eddcb4" } },
  { key: "rose", label: "Rose", dot: "dotRose", accent: "#b3265b", theme: { primary: "#b3265b", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#2c0f1c", accent: "#e0709b", border: "#f3d3df" } },
  { key: "slate", label: "Slate", dot: "dotSlate", accent: "#334155", theme: { primary: "#334155", on_primary: "#ffffff", surface: "#ffffff", on_surface: "#0f172a", accent: "#64748b", border: "#d5dbe3" } },
];

export function Appearance() {
  const { id, can, active, base } = usePanel();
  const [load, reload] = useLoad(() => partnerApi.brand(id), [id]);
  const [features] = useLoad(() => panelApi.features(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Appearance" lead="How your branded customer portal looks. Colours come from checked sets and values; arbitrary CSS or scripts are not allowed." />
      <LoadGate load={load} what="your appearance">
        {(d) =>
          d.brand ? (
            <>
              <section className={styles.panel} aria-labelledby="scheme-title">
                <h2 id="scheme-title">Themes and colour scheme</h2>
                <SchemePicker brand={d.brand} editable={can("branding.manage") && active} onSaved={reload} />
              </section>
              <ThemeTokens brand={d.brand} editable={can("branding.manage") && active} onSaved={reload} />
            </>
          ) : (
            <NotAvailable title="No brand yet" reasons={["Set up your brand first; a colour scheme applies to it."]} />
          )
        }
      </LoadGate>
      <LoadGate load={features} what="what is available">
        {(f) => (
          <NotAvailable
            title="Not offered"
            reasons={[f.not_implemented.appearance_layout ?? "", f.not_implemented.custom_css ?? "", f.not_implemented.brand_assets ?? ""].filter(Boolean)}
          />
        )}
      </LoadGate>
      <Link href={`${base}/brand-settings`}>Brand settings</Link>
    </div>
  );
}

/** The brand colour tokens, as colour pickers. These replace "custom CSS": a fixed
 * set of six tokens, each a six-digit hex colour the server checks again. There is
 * no stylesheet, no selector and no value that is anything but a colour. */
const TOKENS: { key: string; label: string }[] = [
  { key: "primary", label: "Primary colour" },
  { key: "on_primary", label: "Text on primary" },
  { key: "surface", label: "Surface colour" },
  { key: "on_surface", label: "Text on surface" },
  { key: "accent", label: "Accent colour" },
  { key: "border", label: "Border colour" },
];

const HEX = /^#[0-9a-f]{6}$/;

/** WCAG relative-luminance contrast of two six-digit colours. */
export function contrastRatio(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5].map((i) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

function ThemeTokens({ brand, editable, onSaved }: { brand: Brand; editable: boolean; onSaved: () => void }) {
  const { id } = usePanel();
  const saved = () => Object.fromEntries(TOKENS.map((t) => [t.key, brand.theme[t.key] ?? (t.key === "primary" ? brand.accent_color : "")]));
  const [tokens, setTokens] = useState<Record<string, string>>(saved);
  const valid = TOKENS.every((t) => HEX.test(tokens[t.key] ?? ""));
  const pairs = [
    { label: "Text on primary", ratio: valid ? contrastRatio(tokens.on_primary!, tokens.primary!) : 0 },
    { label: "Text on surface", ratio: valid ? contrastRatio(tokens.on_surface!, tokens.surface!) : 0 },
  ];
  const readable = valid && pairs.every((p) => p.ratio >= 4.5);
  const current = saved();
  return (
    <section className={styles.panel} aria-labelledby="tokens-title">
      <div className={styles.panelHead}>
        <h2 id="tokens-title">Advanced theme tokens</h2>
        {editable && (
          <div className={styles.actions}>
            <FormPopup
              label="Edit"
              buttonAriaLabel="Edit theme tokens"
              buttonClassName="w360-btn w360-btn-secondary"
              title="Edit theme tokens"
              intro="Six colours, nothing else. Text must stay readable: 4.5:1 or more against its background."
              submitLabel="Save theme tokens"
              canSubmit={readable}
              onOpen={() => setTokens(saved())}
              onSubmit={async () => {
                await partnerApi.saveBrand(
                  id,
                  {
                    brand_name: brand.brand_name,
                    accent_color: tokens.primary!,
                    theme: tokens,
                    support_email: brand.support_email,
                    support_url: brand.support_url,
                    logo_file_id: brand.logo_file_id ?? undefined,
                  },
                  brand.version,
                );
                return "Theme tokens saved.";
              }}
              onDone={onSaved}
            >
              {TOKENS.map((t) => (
                <div key={t.key}>
                  <label className="w360-label" htmlFor={`token-${t.key}`}>
                    {t.label}
                  </label>
                  <input
                    id={`token-${t.key}`}
                    className="w360-input"
                    type="color"
                    value={HEX.test(tokens[t.key] ?? "") ? tokens[t.key] : "#000000"}
                    onChange={(e) => setTokens((cur) => ({ ...cur, [t.key]: e.target.value.toLowerCase() }))}
                  />
                  <span className={styles.mono}>{tokens[t.key] || "not set"}</span>
                </div>
              ))}
              <ul className={`${styles.list} ${dialog.span}`} aria-label="Contrast checks">
                {pairs.map((p) => (
                  <li key={p.label} className={styles.listItem}>
                    <span>{p.label}</span>
                    <span>{valid ? `${p.ratio.toFixed(1)}:1 ${p.ratio >= 4.5 ? "readable" : "too low - needs 4.5:1"}` : "—"}</span>
                  </li>
                ))}
              </ul>
            </FormPopup>
          </div>
        )}
      </div>
      <p className={styles.muted}>Six colours, nothing else. In place of custom CSS, which is not available because it can inject script and leak styling between customers.</p>
      <dl className={styles.summary}>
        {TOKENS.map((t) => (
          <div key={t.key}>
            <dt>{t.label}</dt>
            <dd className={styles.mono}>{current[t.key] || "not set"}</dd>
          </div>
        ))}
      </dl>
      {!editable && <p className={styles.muted}>Changing colours needs the brand permission.</p>}
    </section>
  );
}

function SchemePicker({ brand, editable, onSaved }: { brand: Brand; editable: boolean; onSaved: () => void }) {
  const { id } = usePanel();
  const { act, busy, feedback } = useAction(onSaved);
  const current = SCHEMES.find((s) => s.accent === brand.accent_color)?.key ?? "";
  return (
    <>
      {feedback}
      <div className={styles.swatches} role="group" aria-label="Colour scheme">
        {SCHEMES.map((s) => (
          <button
            key={s.key}
            type="button"
            className={styles.swatch}
            aria-pressed={current === s.key}
            disabled={!editable || busy}
            onClick={() =>
              void act(
                () =>
                  partnerApi.saveBrand(
                    id,
                    {
                      brand_name: brand.brand_name,
                      accent_color: s.accent,
                      theme: s.theme,
                      support_email: brand.support_email,
                      support_url: brand.support_url,
                      // A brand save replaces the brand: keep the logo it has.
                      logo_file_id: brand.logo_file_id ?? undefined,
                    },
                    brand.version,
                  ),
                `${s.label} applied.`,
              )
            }
          >
            <span className={`${styles.swatchDot} ${styles[s.dot]}`} aria-hidden="true" />
            <span>{s.label}</span>
          </button>
        ))}
      </div>
      {!editable && <p className={styles.muted}>Changing the scheme needs the brand permission.</p>}
    </>
  );
}
