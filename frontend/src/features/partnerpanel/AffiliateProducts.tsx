"use client";

import { useId, useState } from "react";
import { LoadGate, Status, Table, money, useAction, useLoad, when } from "@/features/controlplane/ui";
import {
  panelApi,
  type AffiliateLink,
  type LinkKind,
  type LinkRollup,
  type MyProduct,
  type MyProducts as MyProductsData,
  type ReferralTrail,
} from "@/lib/api/partnerPanel";
import { EarningCells, productLabel } from "./AffiliateScreens";
import { Amounts, Kpi, PageHeader, usePanel } from "./PanelKit";
import { CopyButton, percent, useRangeControls } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * My Products (ADR-054): the products Workeasy360 has allowed this affiliate to
 * promote, what each can be linked to - its page, its pricing, its published
 * features and updates - and how every link is doing.
 *
 * The browser sends a kind, a product code, an update id, a campaign id and an
 * optional code; the server checks the promotion grant, builds the address and
 * decides where it leads. A product that is not listed here cannot be linked,
 * whatever is sent. Every figure is the server's.
 */

const KIND_LABELS: Record<LinkKind, string> = {
  general: "Sign-up page",
  product: "Main product page",
  pricing: "Pricing page",
  feature: "Feature",
  update: "Product update",
};

const STATUS_HELP =
  "Pending: inside the refund window. Approved: cleared and payable. Reversed: taken back by a refund. Paid: in a paid payout.";

const linkOpens = (link: AffiliateLink): string => {
  const kind = link.link_kind ?? (link.product_code ? "product" : "general");
  if (kind === "feature" || kind === "update") return `${KIND_LABELS[kind]}: ${link.update_title ?? "—"}`;
  if (kind === "pricing") return `${link.product_name ?? link.product_code} · pricing`;
  return productLabel(link);
};

function RollupKpis({ totals, label }: { totals: LinkRollup; label: string }) {
  const sales = totals.earnings?.map((e) => ({ currency: e.currency, amount_minor: e.sales_minor })) ?? null;
  const commission = totals.earnings?.map((e) => ({ currency: e.currency, amount_minor: e.commission_minor })) ?? null;
  return (
    <ul className={styles.kpis} aria-label={label}>
      <Kpi label="Clicks" value={totals.clicks} />
      <Kpi label="Leads" value={totals.registrations} note="Created an account" />
      <Kpi label="Trials" value={totals.trials} />
      <Kpi label="Conversions" value={totals.conversions} note={`Rate ${percent(totals.conversion_rate_bp)}`} />
      <Kpi label="Revenue" value={<Amounts amounts={sales} />} unavailable={sales === null ? "Needs commission access." : undefined} />
      <Kpi
        label="Commission"
        value={<Amounts amounts={commission} />}
        unavailable={commission === null ? "Needs commission access." : undefined}
      />
    </ul>
  );
}

function LinkGenerator({
  product,
  campaigns,
  onCreated,
  preset,
}: {
  product: MyProduct;
  campaigns: MyProductsData["campaign_list"];
  onCreated: () => void;
  preset?: { kind: LinkKind; updateId: string };
}) {
  const { id } = usePanel();
  const formId = useId();
  const [made, setMade] = useState<string | null>(null);
  const { act, busy, feedback } = useAction(onCreated);
  const [kind, setKind] = useState<LinkKind>(preset?.kind ?? "product");
  const [updateId, setUpdateId] = useState(preset?.updateId ?? "");
  const [plan, setPlan] = useState("");
  const [campaign, setCampaign] = useState("");
  const [label, setLabel] = useState("");
  const [code, setCode] = useState("");
  const wanted = kind === "feature" ? "feature" : "release";
  const targets = product.updates.filter((u) => u.kind === wanted);
  const needsUpdate = kind === "feature" || kind === "update";
  return (
    <form
      className={styles.panel}
      aria-label={`Create a link for ${product.name}`}
      onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          const created = await panelApi.createTrackingLink(id, {
            link_kind: kind,
            product_code: product.code,
            update_id: needsUpdate ? updateId || undefined : undefined,
            plan_code: kind === "product" ? plan || undefined : undefined,
            campaign_id: campaign || undefined,
            label: label.trim() || undefined,
            code: code.trim() || undefined,
          });
          setMade(created.url);
          setCode("");
          setLabel("");
        }, "Referral link created.");
      }}
    >
      <h3>Create a link for {product.name}</h3>
      <div className={styles.formGrid}>
        <div>
          <label className="w360-label" htmlFor={`${formId}-kind`}>
            Link opens
          </label>
          <select
            id={`${formId}-kind`}
            className="w360-input"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as LinkKind);
              setUpdateId("");
            }}
          >
            <option value="product">Main product page</option>
            <option value="pricing">Pricing page</option>
            <option value="feature" disabled={!product.updates.some((u) => u.kind === "feature")}>
              A feature
            </option>
            <option value="update" disabled={!product.updates.some((u) => u.kind === "release")}>
              A product update
            </option>
          </select>
        </div>
        {needsUpdate && (
          <div>
            <label className="w360-label" htmlFor={`${formId}-update`}>
              {kind === "feature" ? "Feature" : "Update"}
            </label>
            <select id={`${formId}-update`} className="w360-input" required value={updateId} onChange={(e) => setUpdateId(e.target.value)}>
              <option value="">Choose…</option>
              {targets.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.title}
                </option>
              ))}
            </select>
          </div>
        )}
        {kind === "product" && (
          <div>
            <label className="w360-label" htmlFor={`${formId}-plan`}>
              Plan (optional)
            </label>
            <select id={`${formId}-plan`} className="w360-input" value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="">Any plan</option>
              {product.plans.map((p) => (
                <option key={p.plan_code} value={p.plan_code}>
                  {p.plan_name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="w360-label" htmlFor={`${formId}-campaign`}>
            Campaign (optional)
          </label>
          <select id={`${formId}-campaign`} className="w360-input" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
            <option value="">No campaign</option>
            {campaigns
              .filter((c) => c.status === "active")
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${formId}-label`}>
            Name (optional)
          </label>
          <input id={`${formId}-label`} className="w360-input" maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
        <div>
          <label className="w360-label" htmlFor={`${formId}-code`}>
            Your own code (optional)
          </label>
          <input
            id={`${formId}-code`}
            className="w360-input"
            maxLength={16}
            pattern="[A-Za-z0-9]{4,16}"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
        </div>
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || (needsUpdate && !updateId)}>
          Generate link
        </button>
      </div>
      {feedback}
      {made && (
        <p className={styles.linkRow}>
          <span className={styles.mono}>{made}</span>
          <CopyButton text={made} label="new link" />
        </p>
      )}
    </form>
  );
}

function LinkTable({ links, caption, manage, onChanged }: { links: AffiliateLink[]; caption: string; manage: boolean; onChanged: () => void }) {
  const { id } = usePanel();
  const { act, busy, feedback } = useAction(onChanged);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  return (
    <>
      {feedback}
      <Table
        caption={caption}
        head={["Link", "Opens", "Name", "Clicks", "Leads", "Trials", "Conversions", "Revenue", "Commission", "Status", "Actions"]}
        isEmpty={links.length === 0}
        empty="No links yet."
      >
        {links.map((l) => (
          <tr key={l.id}>
            <th scope="row" className={styles.mono}>
              {l.url ?? l.code}
            </th>
            <td>{linkOpens(l)}</td>
            <td>
              {renaming === l.id ? (
                <form
                  className={styles.linkRow}
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      await panelApi.updateTrackingLink(id, l.id, { label: name });
                      setRenaming(null);
                    }, "Link renamed.");
                  }}
                >
                  <label className="w360-visually-hidden" htmlFor={`rename-${l.id}`}>
                    New name for {l.code}
                  </label>
                  <input id={`rename-${l.id}`} className="w360-input" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
                    Save
                  </button>
                </form>
              ) : (
                l.label || l.campaign
              )}
            </td>
            <td className={styles.num}>{l.clicks}</td>
            <td className={styles.num}>{l.registrations}</td>
            <td className={styles.num}>{l.trials ?? 0}</td>
            <td className={styles.num}>{l.conversions}</td>
            <EarningCells earnings={l.earnings} />
            <td>
              <Status value={l.status} />
            </td>
            <td>
              <div className={styles.actions}>
                {l.url && <CopyButton text={l.url} label={`link ${l.code}`} />}
                {manage && renaming !== l.id && (
                  <button
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    onClick={() => {
                      setRenaming(l.id);
                      setName(l.label);
                    }}
                  >
                    Rename<span className="w360-visually-hidden"> link {l.code}</span>
                  </button>
                )}
                {manage && (
                  <button
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () => panelApi.updateTrackingLink(id, l.id, { active: l.status !== "active" }),
                        l.status === "active" ? "Link switched off. Earlier referrals still count." : "Link switched on.",
                      )
                    }
                  >
                    {l.status === "active" ? "Switch off" : "Switch on"}
                    <span className="w360-visually-hidden"> link {l.code}</span>
                  </button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </Table>
    </>
  );
}

/** Where the link form would be, when it is not drawn: why, and where links come from. */
function NoLinkForm({ product, canCreate }: { product: MyProduct; canCreate: boolean }) {
  const { partner, active } = usePanel();
  const reason = partner?.guest
    ? "You are viewing this portal as a guest, so it is read-only."
    : !active
      ? "This partner account is not active, so no new links can be created."
      : !canCreate
        ? "Your role cannot create links. Ask the account owner for the link-creation permission."
        : null;
  if (!reason) return null;
  return (
    <p className={styles.muted} role="note">
      {reason} When the affiliate signs in, a “Create a link for {product.name}” form appears here: they choose
      what the link opens (main product page, pricing page, a feature or a product update), and optionally a plan,
      a campaign, a name and their own code. Each link gets its own tracking code and is listed below.
    </p>
  );
}

function ProductSection({ product, data, onChanged }: { product: MyProduct; data: MyProductsData; onChanged: () => void }) {
  const { partner, active } = usePanel();
  const headingId = useId();
  const [preset, setPreset] = useState<{ kind: LinkKind; updateId: string } | undefined>();
  const manage = data.can_create && active;
  const banner = product.media.find((m) => m.kind === "banner") ?? product.media.find((m) => m.kind === "logo");
  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <div className={styles.cardHead}>
        <h2 id={headingId}>{product.name}</h2>
        <span className="w360-badge w360-badge-success">May promote</span>
      </div>
      {product.tagline && <p className={styles.muted}>{product.tagline}</p>}
      {product.granted_at && <p className={styles.muted}>Assigned to you on {when(product.granted_at)}.</p>}
      {banner && (
        // eslint-disable-next-line @next/next/no-img-element -- same-origin listing image, sized by CSS
        <img className={styles.mediaThumb} src={banner.url} alt={banner.caption || `${product.name} ${banner.kind}`} loading="lazy" />
      )}
      <RollupKpis totals={product.totals} label={`${product.name} totals`} />

      {product.features.length > 0 && (
        <>
          <h3>Features you can mention</h3>
          <ul className={styles.list}>
            {product.features.map((f) => (
              <li key={f.key} className={styles.listItem}>
                <strong>{f.name}</strong>
                {f.description && <span className={styles.muted}> — {f.description}</span>}
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Features and updates to promote</h3>
      <Table
        caption={`${product.name}: published features and updates`}
        head={["Title", "Kind", "Published", "Links", "Clicks", "Conversions", "Commission", "Promote"]}
        isEmpty={product.updates.length === 0}
        empty={
          partner?.guest
            ? "Nothing has been published for this product yet. Publish a feature or update in Admin → Product Manager → Updates."
            : "Nothing has been published for this product yet. Features and updates appear here when Workeasy360 publishes them."
        }
      >
        {product.updates.map((u) => (
          <tr key={u.id}>
            <th scope="row">
              {u.title}
              {u.summary && <span className={styles.muted}> — {u.summary}</span>}
            </th>
            <td>{u.kind === "feature" ? "Feature" : "Release"}</td>
            <td>{when(u.published_at)}</td>
            <td className={styles.num}>{u.links}</td>
            <td className={styles.num}>{u.clicks}</td>
            <td className={styles.num}>{u.conversions}</td>
            <td className={styles.num}>
              {u.earnings === null ? "—" : <Amounts amounts={u.earnings.map((e) => ({ currency: e.currency, amount_minor: e.commission_minor }))} />}
            </td>
            <td>
              <div className={styles.actions}>
                {u.body && <CopyButton text={u.body} label={`approved text for ${u.title}`} />}
                {manage && (
                  <button
                    type="button"
                    className="w360-btn w360-btn-secondary"
                    onClick={() => setPreset({ kind: u.kind === "feature" ? "feature" : "update", updateId: u.id })}
                  >
                    Create link<span className="w360-visually-hidden"> for {u.title}</span>
                  </button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </Table>

      {manage ? (
        <LinkGenerator
          key={preset ? `${preset.kind}-${preset.updateId}` : "new"}
          product={product}
          campaigns={data.campaign_list}
          preset={preset}
          onCreated={onChanged}
        />
      ) : (
        <NoLinkForm product={product} canCreate={data.can_create} />
      )}
      <LinkTable links={product.links} caption={`Your links for ${product.name}`} manage={manage} onChanged={onChanged} />
    </section>
  );
}

export function MyProducts() {
  const { id, active } = usePanel();
  const controls = useRangeControls();
  const [load, reload] = useLoad(
    () => panelApi.myProducts(id, controls.range, controls.from, controls.to),
    [id, controls.range, controls.from ?? "", controls.to ?? ""],
  );
  return (
    <div className={styles.page}>
      <PageHeader
        title="My Products"
        lead="The products Workeasy360 has assigned you to promote. Create a link for a product, its pricing, a feature or an update, and see how each one does."
        actions={controls.node}
      />
      <LoadGate load={load} what="your products">
        {(d) => (
          <>
            <p className={styles.muted}>
              A visitor is credited to the first link they follow, for {d.policy.attribution_days} days. Commission is earned
              only when a referred customer pays, and only on the product a product link promotes.
            </p>
            {d.products.length === 0 ? (
              <p>No products have been assigned to you yet. Workeasy360 assigns the products you may promote.</p>
            ) : (
              d.products.map((p) => <ProductSection key={p.code} product={p} data={d} onChanged={reload} />)
            )}
            {d.withdrawn.length > 0 && (
              <section className={styles.panel} aria-labelledby="withdrawn-h">
                <h2 id="withdrawn-h">No longer assigned</h2>
                <p className={styles.muted}>
                  {d.policy.revoked_links === "stop"
                    ? "These links no longer take new visitors."
                    : "These links still take visitors, but you cannot create new ones."}{" "}
                  Referrals and commission you earned through them are kept.
                </p>
                {d.withdrawn.map((w) => (
                  <LinkTable key={w.product_code} links={w.links} caption={`Earlier links for ${w.product_name}`} manage={false} onChanged={reload} />
                ))}
              </section>
            )}
            {d.campaigns.length > 0 && (
              <Table
                caption="Campaigns across your products"
                head={["Campaign", "Links", "Clicks", "Leads", "Trials", "Conversions", "Commission"]}
              >
                {d.campaigns.map((c) => (
                  <tr key={c.campaign_id}>
                    <th scope="row">{c.name}</th>
                    <td className={styles.num}>{c.links}</td>
                    <td className={styles.num}>{c.clicks}</td>
                    <td className={styles.num}>{c.registrations}</td>
                    <td className={styles.num}>{c.trials}</td>
                    <td className={styles.num}>{c.conversions}</td>
                    <td className={styles.num}>
                      {c.earnings === null ? "—" : <Amounts amounts={c.earnings.map((e) => ({ currency: e.currency, amount_minor: e.commission_minor }))} />}
                    </td>
                  </tr>
                ))}
              </Table>
            )}
            {!active && <p className={styles.muted}>Your account is not active, so new links cannot be created.</p>}
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- the attribution trail (Conversions) --------------------------------------------------------

const STEP_LABELS: Record<ReferralTrail["steps"][number]["step"], string> = {
  clicked: "Followed the link",
  registered: "Created an account",
  customer: "Became a customer",
  trial: "Started a trial",
  purchased: "First payment",
};

/** One referral's trail. ``fetchTrail`` is the partner's own read or, in the
 * admin console, the staff read - the same shape either way. */
export function ReferralTrailPanel({
  referralId,
  fetchTrail,
  onClose,
}: {
  referralId: string;
  fetchTrail: (referralId: string) => Promise<ReferralTrail>;
  onClose: () => void;
}) {
  const headingId = useId();
  const [load] = useLoad(() => fetchTrail(referralId), [referralId]);
  return (
    <section className={styles.panel} aria-labelledby={headingId}>
      <div className={styles.cardHead}>
        <h2 id={headingId}>Attribution trail</h2>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
          Close<span className="w360-visually-hidden"> attribution trail</span>
        </button>
      </div>
      <LoadGate load={load} what="the attribution trail">
        {(t) => (
          <>
            <dl className={styles.facts}>
              <dt>Customer</dt>
              <dd>{t.customer_ref ?? "Not a customer yet"} (customers are never named)</dd>
              <dt>Link</dt>
              <dd>
                <span className={styles.mono}>{t.link.code}</span> · {KIND_LABELS[t.link.link_kind]}
                {t.link.update_title ? `: ${t.link.update_title}` : ""}
                {t.link.product_code ? ` · ${t.link.product_code}` : ""}
              </dd>
              <dt>Campaign</dt>
              <dd>{t.link.campaign_name ?? (t.link.label || "—")}</dd>
              <dt>Rule</dt>
              <dd>First touch: this link was the first the visitor followed.</dd>
            </dl>
            <ol className={styles.list} aria-label="Steps">
              {t.steps.map((s) => (
                <li key={s.step} className={styles.listItem}>
                  {STEP_LABELS[s.step]}: {s.at ? when(s.at) : "not yet"}
                </li>
              ))}
            </ol>
            <Table
              caption="Paid sales and their commission"
              head={["Paid", "Product", "Kind", "Payment", "Sale", "Commission", "Status"]}
              isEmpty={t.sales.length === 0}
              empty="No paid sale yet. Commission is earned only when a payment is confirmed."
            >
              {t.sales.map((s) => (
                <tr key={s.id}>
                  <th scope="row">{when(s.paid_at)}</th>
                  <td>
                    {s.product_code}
                    {s.plan_code ? ` · ${s.plan_code}` : ""}
                  </td>
                  <td>{s.renewal ? "Renewal" : "First sale"}</td>
                  <td>{s.payment_verified ? "Verified" : "Recorded"}</td>
                  <td className={styles.num}>{s.sale_minor === null ? "—" : money(s.sale_minor, s.currency)}</td>
                  <td className={styles.num}>{s.commission_minor === null ? "—" : money(s.commission_minor, s.currency)}</td>
                  <td>
                    <Status value={s.status} />
                  </td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>{STATUS_HELP}</p>
          </>
        )}
      </LoadGate>
    </section>
  );
}
