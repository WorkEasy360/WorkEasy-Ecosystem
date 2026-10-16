"use client";

import Link from "next/link";
import { useState } from "react";
import { LoadGate, MoreButton, Status, Table, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { countryOptions } from "@/features/controlplane/countries";
import { SelectField, TextField } from "@/features/controlplane/widgets";
import { partnerApi, type Brand } from "@/lib/api/controlPlane";
import { panelApi, type OwnProfile } from "@/lib/api/partnerPanel";
import { NotAvailable, PageHeader, SubNav, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

// --- settings: the business profile ---------------------------------------------------------

function zones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    return [];
  }
}

export function Settings() {
  const { id } = usePanel();
  const [load, reload] = useLoad(() => panelApi.profile(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Settings" lead="Your business details. These are how Workeasy360 reaches and invoices you; they are not customer records." />
      <LoadGate load={load} what="your settings">
        {(p) => <ProfileForm key={p.version} profile={p} onSaved={reload} />}
      </LoadGate>
    </div>
  );
}

function ProfileForm({ profile, onSaved }: { profile: OwnProfile; onSaved: () => void }) {
  const { id, can, active } = usePanel();
  const editable = can("profile.manage") && active;
  const { act, busy, feedback } = useAction(onSaved);
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

  return (
    <form
      className={styles.page}
      onSubmit={(e) => {
        e.preventDefault();
        void act(
          () =>
            panelApi.saveProfile(id, profile.version, {
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
            }),
          "Saved.",
        );
      }}
    >
      {feedback}
      <fieldset className={styles.panel} disabled={!editable}>
        <legend>Business</legend>
        <div className={styles.formGrid}>
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
          <div>
            <span className="w360-label">Payout currency</span>
            <p>{profile.payout.currency}</p>
            <p className="w360-hint">Fixed when the account was created: commission is denominated in it.</p>
          </div>
        </div>
      </fieldset>
      <fieldset className={styles.panel} disabled={!editable}>
        <legend>Support details your customers see</legend>
        <div className={styles.formGrid}>
          <TextField label="Support email" value={supportEmail} onChange={setSupportEmail} type="email" />
          <TextField label="Support phone" value={supportPhone} onChange={setSupportPhone} type="tel" />
          <TextField label="Support page" value={supportUrl} onChange={setSupportUrl} type="url" hint="Must start with https://" />
        </div>
      </fieldset>
      <fieldset className={styles.panel} disabled={!editable}>
        <legend>Contacts</legend>
        <div className={styles.formGrid}>
          <TextField label="Primary contact name" value={pName} onChange={setPName} maxLength={120} />
          <TextField label="Primary contact email" value={pEmail} onChange={setPEmail} type="email" />
        </div>
        <label className={styles.checks}>
          <span>
            <input type="checkbox" checked={billingSame} onChange={(e) => setBillingSame(e.target.checked)} /> Billing contact is the same as the primary contact
          </span>
        </label>
        {!billingSame && (
          <div className={styles.formGrid}>
            <TextField label="Billing contact name" value={bName} onChange={setBName} maxLength={120} />
            <TextField label="Billing contact email" value={bEmail} onChange={setBEmail} type="email" />
          </div>
        )}
      </fieldset>
      {editable ? (
        <div>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !name.trim()}>
            Save settings
          </button>
        </div>
      ) : (
        <p className={styles.muted}>You can read these details; changing them needs an administrator.</p>
      )}
    </form>
  );
}

// --- authentication ------------------------------------------------------------------------------

export function Authentication() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.authentication(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Authentication" lead="How you and your customers sign in. This is Workeasy360's secure sign-in; it is not something a partner changes." />
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
                  {a.team.with_second_factor} of {a.team.members} have two-step sign-in set up
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
                <Link href={`${base}/branding`}>Brand settings</Link>
              </section>
            </div>
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
        { href: `${base}/communication/channels`, label: "Channels" },
        { href: `${base}/communication/templates`, label: "Templates" },
        { href: `${base}/communication/alerts`, label: "Alerts" },
        { href: `${base}/communication/logs`, label: "Logs" },
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
              The sender name and reply-to for emails sent in your name are set in <Link href={`${base}/branding`}>Brand settings</Link>. The sending address always stays Workeasy360&apos;s,
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

export function Activity() {
  const { id } = usePanel();
  const paged = usePaged((cursor) => panelApi.activity(id, cursor), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Activity" lead="What has been done on your account, and by whom. This comes from the platform's tamper-evident audit log." />
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
    </div>
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
      <PageHeader title="Appearance" lead="Pick a colour scheme for your branded customer portal. Schemes are fixed sets of colours that Workeasy360 checks; arbitrary CSS or scripts are not allowed." />
      <LoadGate load={load} what="your appearance">
        {(d) =>
          d.brand ? (
            <SchemePicker brand={d.brand} editable={can("branding.manage") && active} onSaved={reload} />
          ) : (
            <NotAvailable title="No brand yet" reasons={["Set up your brand first; a colour scheme applies to it."]} />
          )
        }
      </LoadGate>
      <LoadGate load={features} what="what is available">
        {(f) => <NotAvailable title="Not offered" reasons={[f.not_implemented.custom_css ?? "", f.not_implemented.brand_assets ?? ""]} />}
      </LoadGate>
      <Link href={`${base}/branding`}>Brand settings</Link>
    </div>
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
