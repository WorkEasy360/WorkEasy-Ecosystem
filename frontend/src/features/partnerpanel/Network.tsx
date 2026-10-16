"use client";

import { useId, useState } from "react";
import { LoadGate, MoreButton, ReasonAction, Status, Table, money, useAction, useLoad, usePaged, when } from "@/features/controlplane/ui";
import { partnerApi } from "@/lib/api/controlPlane";
import { panelApi, type MemberRow, type SubPartnerRow } from "@/lib/api/partnerPanel";
import { PartnerPayouts } from "@/features/controlplane/PartnerScreens";
import { NotAvailable, PageHeader, SubNav, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

// --- sub-partners --------------------------------------------------------------------

export function SubPartners() {
  const { id, can, active } = usePanel();
  const paged = usePaged((cursor) => panelApi.subPartners(id, cursor), [id]);
  const { act, busy, feedback } = useAction(paged.reload);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Sub-partners"
        lead="Partners you have set up under your own account. Each signs in on its own, sees only its own customers, and can use only what you give it."
      />
      {feedback}
      {paged.state === "loading" && <p aria-live="polite">Loading sub-partners…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to sub-partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load sub-partners.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="Your sub-partners"
            head={["Partner", "Status", "Administrator", "Country", "Currency", "Customers", "Products", "Last sign-in", "Added", "Actions"]}
            isEmpty={paged.rows.length === 0}
            empty="You have no sub-partners yet."
          >
            {paged.rows.map((p) => (
              <SubPartnerRowView key={p.id} row={p} manage={can("subpartner.manage") && active} act={act} busy={busy} onChanged={paged.reload} partnerId={id} />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
      {can("subpartner.manage") && active && <NewSubPartner onCreated={paged.reload} />}
    </div>
  );
}

function SubPartnerRowView({
  row,
  manage,
  act,
  busy,
  partnerId,
  onChanged,
}: {
  row: SubPartnerRow;
  manage: boolean;
  act: (a: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
  partnerId: string;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <>
      <tr>
        <th scope="row">
          {row.name}
          <div className={styles.muted}>{row.experiences.join(" · ") || "No capabilities yet"}</div>
        </th>
        <td>
          <Status value={row.status} />
        </td>
        <td>
          {row.administrator.name || "—"}
          <div className={styles.muted}>{row.administrator.email}</div>
        </td>
        <td>{row.country || "—"}</td>
        <td>{row.currency}</td>
        <td className={styles.num}>{row.customers}</td>
        <td>{row.products.join(", ") || "—"}</td>
        <td>{when(row.administrator.last_login_at)}</td>
        <td>{when(row.created_at)}</td>
        <td>
          {manage && (
            <div className={styles.actions}>
              {row.status === "draft" && (
                <button
                  type="button"
                  className="w360-btn w360-btn-primary"
                  disabled={busy}
                  onClick={() => void act(() => panelApi.subPartnerAction(partnerId, row.id, "activate"), `${row.name} is active. Its administrator has been emailed a link to set a password.`)}
                >
                  Activate
                </button>
              )}
              {row.status === "active" && (
                <ReasonAction
                  label="Suspend"
                  confirmLabel="Suspend"
                  busy={busy}
                  onConfirm={(reason) => void act(() => panelApi.subPartnerAction(partnerId, row.id, "suspend", reason), `${row.name} is suspended.`)}
                />
              )}
              {row.status === "suspended" && (
                <button
                  type="button"
                  className="w360-btn w360-btn-secondary"
                  disabled={busy}
                  onClick={() => void act(() => panelApi.subPartnerAction(partnerId, row.id, "resume"), `${row.name} is active again.`)}
                >
                  Resume
                </button>
              )}
              <button type="button" className="w360-btn w360-btn-secondary" aria-expanded={editing} onClick={() => setEditing((e) => !e)}>
                Products & access
              </button>
            </div>
          )}
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={10}>
            <Equip partnerId={partnerId} row={row} onDone={() => { setEditing(false); onChanged(); }} />
          </td>
        </tr>
      )}
    </>
  );
}

function Equip({ partnerId, row, onDone }: { partnerId: string; row: SubPartnerRow; onDone: () => void }) {
  const [load] = useLoad(() => panelApi.subPartnerOptions(partnerId), [partnerId]);
  const { act, busy, feedback } = useAction(onDone);
  const [caps, setCaps] = useState<string[]>(row.capabilities);
  const [products, setProducts] = useState<string[]>(row.products);
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <LoadGate load={load} what="what you can give">
      {(o) => (
        <form
          className={styles.page}
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => panelApi.equipSubPartner(partnerId, row.id, caps, products), "Saved.");
          }}
        >
          {feedback}
          <div className={styles.twoCol}>
            <fieldset className={styles.checks}>
              <legend>What {row.name} can do</legend>
              {o.capabilities.map((c) => (
                <label key={c.key}>
                  <input type="checkbox" checked={caps.includes(c.key)} onChange={() => setCaps((l) => toggle(l, c.key))} />
                  <span>
                    {c.label}
                    <span className={styles.muted}> — {c.description}</span>
                  </span>
                </label>
              ))}
              {o.capabilities.length === 0 && <p className={styles.muted}>You hold nothing you may hand on.</p>}
            </fieldset>
            <fieldset className={styles.checks}>
              <legend>Products it may sell</legend>
              {o.products.map((p) => (
                <label key={p.code}>
                  <input type="checkbox" checked={products.includes(p.code)} onChange={() => setProducts((l) => toggle(l, p.code))} />
                  <span>{p.name}</span>
                </label>
              ))}
              {o.products.length === 0 && <p className={styles.muted}>You hold no product you may hand on.</p>}
            </fieldset>
          </div>
          <p className={styles.muted}>
            Anything money-related (commission, payouts) and custom domains are Workeasy360&apos;s to enable. Only powers you hold yourself can be given.
          </p>
          <div className={styles.actions}>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
              Save
            </button>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={onDone}>
              Close
            </button>
          </div>
        </form>
      )}
    </LoadGate>
  );
}

function NewSubPartner({ onCreated }: { onCreated: () => void }) {
  const { id } = usePanel();
  const [options] = useLoad(() => panelApi.subPartnerOptions(id), [id]);
  const { act, busy, feedback } = useAction(onCreated);
  const nameId = useId();
  const emailId = useId();
  const personId = useId();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [person, setPerson] = useState("");
  const [caps, setCaps] = useState<string[]>([]);
  const [products, setProducts] = useState<string[]>([]);
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <section id="new-sub-partner" className={styles.panel} aria-labelledby="new-sub-partner-title">
      <h2 id="new-sub-partner-title">Add a sub-partner</h2>
      <LoadGate load={options} what="the options">
        {(o) =>
          !o.depth.can_have_children ? (
            <p className={styles.muted}>A sub-partner cannot have sub-partners of its own.</p>
          ) : (
            <form
              className={styles.page}
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await panelApi.createSubPartner(id, {
                    display_name: name.trim(),
                    administrator_email: email.trim(),
                    administrator_name: person.trim(),
                    capabilities: caps,
                    products,
                  });
                  setName("");
                  setEmail("");
                  setPerson("");
                  setCaps([]);
                  setProducts([]);
                }, "Sub-partner created in draft. Activate it when you are ready: its administrator is emailed a link to set a password then.");
              }}
            >
              {feedback}
              <div className={styles.formGrid}>
                <div>
                  <label className="w360-label" htmlFor={nameId}>
                    Partner name
                  </label>
                  <input id={nameId} className="w360-input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div>
                  <label className="w360-label" htmlFor={personId}>
                    Administrator&apos;s name
                  </label>
                  <input id={personId} className="w360-input" maxLength={120} value={person} onChange={(e) => setPerson(e.target.value)} />
                </div>
                <div>
                  <label className="w360-label" htmlFor={emailId}>
                    Administrator&apos;s email
                  </label>
                  <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
              </div>
              <div className={styles.twoCol}>
                <fieldset className={styles.checks}>
                  <legend>What it can do</legend>
                  {o.capabilities.map((c) => (
                    <label key={c.key}>
                      <input type="checkbox" checked={caps.includes(c.key)} onChange={() => setCaps((l) => toggle(l, c.key))} />
                      <span>{c.label}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className={styles.checks}>
                  <legend>Products it may sell</legend>
                  {o.products.map((p) => (
                    <label key={p.code}>
                      <input type="checkbox" checked={products.includes(p.code)} onChange={() => setProducts((l) => toggle(l, p.code))} />
                      <span>{p.name}</span>
                    </label>
                  ))}
                </fieldset>
              </div>
              <NotAvailable title="What a sub-partner does not get" reasons={Object.values(o.not_implemented)} />
              <div>
                <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !name.trim() || !email.trim()}>
                  Create sub-partner
                </button>
              </div>
            </form>
          )
        }
      </LoadGate>
    </section>
  );
}

// --- affiliates ------------------------------------------------------------------------

function AffiliateNav() {
  const { base } = usePanel();
  return (
    <SubNav
      items={[
        { href: `${base}/affiliates`, label: "Affiliates" },
        { href: `${base}/affiliates/conversions`, label: "Conversions" },
        { href: `${base}/affiliates/payouts`, label: "Payouts" },
        { href: `${base}/affiliates/settings`, label: "Settings" },
      ]}
    />
  );
}

export function Affiliates() {
  const { id, can, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.affiliates(id), [id]);
  const { act, busy, feedback } = useAction(reload);
  const labelId = useId();
  const [label, setLabel] = useState("");
  return (
    <div className={styles.page}>
      <PageHeader title="Affiliates" lead="Your referral links, and the sub-partners that refer customers for you." />
      <AffiliateNav />
      {feedback}
      <LoadGate load={load} what="affiliates">
        {(d) => (
          <>
            <ul className={styles.kpis} aria-label="Referral totals">
              {Object.entries(d.summary).map(([k, v]) => (
                <li key={k} className={styles.kpi}>
                  <span className={styles.kpiLabel}>{k.replace(/_/g, " ")}</span>
                  <span className={styles.kpiValue}>{v}</span>
                </li>
              ))}
            </ul>
            <Table caption="Your referral links" head={["Link", "Label", "Status"]} isEmpty={d.codes.length === 0} empty="No referral links yet.">
              {d.codes.map((c) => (
                <tr key={c.id}>
                  <th scope="row" className={styles.mono}>
                    {c.url}
                  </th>
                  <td>{c.label}</td>
                  <td>
                    <Status value={c.status} />
                  </td>
                </tr>
              ))}
            </Table>
            {can("referral.create") && active && (
              <form
                className={styles.panel}
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await partnerApi.createReferralCode(id, label.trim());
                    setLabel("");
                  }, "Referral link created.");
                }}
              >
                <h2>Create a referral link</h2>
                <label className="w360-label" htmlFor={labelId}>
                  Campaign label (only you see this)
                </label>
                <input id={labelId} className="w360-input" required maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !label.trim()}>
                    Create link
                  </button>
                </div>
              </form>
            )}
            {d.affiliates.length > 0 && (
              <Table caption="Sub-partners that refer for you" head={["Partner", "Status", "Administrator", "Active links", "Captured", "Converted"]}>
                {d.affiliates.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">{a.name}</th>
                    <td>
                      <Status value={a.status} />
                    </td>
                    <td>{a.administrator_email}</td>
                    <td className={styles.num}>{a.active_codes}</td>
                    <td className={styles.num}>{a.captured}</td>
                    <td className={styles.num}>{a.converted}</td>
                  </tr>
                ))}
              </Table>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}

export function AffiliateConversions() {
  const { id } = usePanel();
  const paged = usePaged((cursor) => panelApi.conversions(id, cursor), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Conversions" lead="Visitors who followed your link and became customers. A conversion never shows who the visitor was." />
      <AffiliateNav />
      {paged.state === "loading" && <p aria-live="polite">Loading conversions…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to conversions.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load conversions.</p>}
      {paged.state === "ready" && (
        <>
          <Table caption="Conversions" head={["Link", "Campaign", "Visited", "Became a customer", "Commission"]} isEmpty={paged.rows.length === 0} empty="No conversions yet.">
            {paged.rows.map((c) => (
              <tr key={c.id}>
                <th scope="row" className={styles.mono}>
                  {c.code}
                </th>
                <td>{c.code_label || "—"}</td>
                <td>{when(c.captured_at)}</td>
                <td>{when(c.converted_at)}</td>
                <td className={styles.num}>{c.currency ? money(c.commission_minor, c.currency) : "—"}</td>
              </tr>
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}
    </div>
  );
}

export function AffiliatePayouts() {
  return (
    <div className={styles.page}>
      <AffiliateNav />
      <PartnerPayouts />
    </div>
  );
}

export function AffiliateSettings() {
  const { id } = usePanel();
  const [load] = useLoad(() => panelApi.affiliates(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader title="Affiliate settings" lead="How the programme works for you. The window and the rates are set by Workeasy360." />
      <AffiliateNav />
      <LoadGate load={load} what="the programme">
        {(d) => (
          <>
            <section className={styles.panel} aria-labelledby="attr">
              <h2 id="attr">Attribution</h2>
              <dl className={styles.facts}>
                <dt>Attribution window</dt>
                <dd>{d.programme.attribution_window_days} days from the visit</dd>
                <dt>First touch</dt>
                <dd>The first partner a visitor arrives through is credited; later links do not replace it.</dd>
                <dt>You can see</dt>
                <dd>{d.programme.visible_to_you.join(", ")}</dd>
                <dt>Never shown</dt>
                <dd>{d.programme.never_shown.join(", ")}</dd>
              </dl>
            </section>
            {d.programme.commission_rules && (
              <Table caption="Commission rules that name you" head={["Rule", "Version", "Product", "Rate", "State"]} isEmpty={d.programme.commission_rules.length === 0} empty="No rule names you; the programme default applies.">
                {d.programme.commission_rules.map((r) => (
                  <tr key={`${r.code}-${r.version}`}>
                    <th scope="row">{r.code}</th>
                    <td className={styles.num}>{r.version}</td>
                    <td>{r.product_code || "All products"}</td>
                    <td className={styles.num}>{(r.rate_bp / 100).toFixed(2)}%</td>
                    <td>
                      <Status value={r.state} />
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            <NotAvailable title="Not configurable here" reasons={Object.values(d.not_implemented)} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- the team --------------------------------------------------------------------------

export function Team() {
  const { id, active } = usePanel();
  const [load, reload] = useLoad(() => panelApi.members(id), [id]);
  const { act, busy, feedback } = useAction(reload);
  const emailId = useId();
  const nameId = useId();
  const roleId = useId();
  const [email, setEmail] = useState("");
  const [person, setPerson] = useState("");
  const [role, setRole] = useState("viewer");
  return (
    <div className={styles.page}>
      <PageHeader title="Users & permissions" lead="The people who work for your partner account, and what each role can do." />
      {feedback}
      <LoadGate load={load} what="your team">
        {(d) => (
          <>
            <Table
              caption="Your team"
              head={["Name", "Email", "Role", "Status", "Two-step sign-in", "Last sign-in", "Joined", "Actions"]}
              isEmpty={d.members.length === 0}
              empty="No users."
            >
              {d.members.map((m) => (
                <MemberLine key={m.id} m={m} roles={d.roles} manage={d.can_manage && active} act={act} busy={busy} partnerId={id} />
              ))}
            </Table>
            {d.can_manage && active && (
              <form
                className={styles.panel}
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await panelApi.inviteMember(id, { email: email.trim(), full_name: person.trim(), role });
                    setEmail("");
                    setPerson("");
                  }, "Invitation sent. If they have no password yet, the email carries a one-time link to choose one.");
                }}
              >
                <h2>Invite someone</h2>
                <div className={styles.formGrid}>
                  <div>
                    <label className="w360-label" htmlFor={nameId}>
                      Name
                    </label>
                    <input id={nameId} className="w360-input" maxLength={120} value={person} onChange={(e) => setPerson(e.target.value)} />
                  </div>
                  <div>
                    <label className="w360-label" htmlFor={emailId}>
                      Email
                    </label>
                    <input id={emailId} className="w360-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
                  </div>
                  <div>
                    <label className="w360-label" htmlFor={roleId}>
                      Role for the new user
                    </label>
                    <select id={roleId} className="w360-input" value={role} onChange={(e) => setRole(e.target.value)}>
                      {d.roles.map((r) => (
                        <option key={r.key} value={r.key}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <p className={styles.muted}>{d.roles.find((r) => r.key === role)?.description}</p>
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !email.trim()}>
                    Send invitation
                  </button>
                </div>
              </form>
            )}
            <section className={styles.panel} aria-labelledby="roles">
              <h2 id="roles">What each role can do</h2>
              <ul className={styles.list}>
                {d.roles.map((r) => (
                  <li key={r.key} className={styles.listItem}>
                    <strong>{r.label}</strong>
                    <span className={styles.muted}>{r.description}</span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}

function MemberLine({
  m,
  roles,
  manage,
  act,
  busy,
  partnerId,
}: {
  m: MemberRow;
  roles: { key: string; label: string }[];
  manage: boolean;
  act: (a: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
  partnerId: string;
}) {
  const roleId = useId();
  return (
    <tr>
      <th scope="row">
        {m.name || "—"} {m.is_you && <span className={styles.muted}>(you)</span>}
      </th>
      <td>{m.email}</td>
      <td>
        {manage && !m.is_you ? (
          <>
            <label className="w360-visually-hidden" htmlFor={roleId}>
              Role of {m.email}
            </label>
            <select
              id={roleId}
              className="w360-input"
              value={m.role}
              disabled={busy}
              onChange={(e) => void act(() => panelApi.changeRole(partnerId, m.id, e.target.value), `Role changed for ${m.email}.`)}
            >
              {roles.map((r) => (
                <option key={r.key} value={r.key}>
                  {r.label}
                </option>
              ))}
            </select>
          </>
        ) : (
          m.role_label
        )}
      </td>
      <td>
        <Status value={m.setup_pending ? "pending" : m.status} />
      </td>
      <td>{m.mfa_enrolled ? "On" : "Not set up"}</td>
      <td>{when(m.last_login_at)}</td>
      <td>{when(m.joined_at)}</td>
      <td>
        {manage && !m.is_you && (
          <div className={styles.actions}>
            {m.setup_pending && (
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                disabled={busy}
                onClick={() => void act(() => panelApi.resendSetup(partnerId, m.id), `A new setup link was sent to ${m.email}.`)}
              >
                Resend link
              </button>
            )}
            <ReasonAction
              label="Remove"
              confirmLabel="Remove"
              busy={busy}
              minLength={3}
              onConfirm={() => void act(() => panelApi.removeMember(partnerId, m.id), `${m.email} was removed.`)}
            />
          </div>
        )}
      </td>
    </tr>
  );
}
