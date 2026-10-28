"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { LoadGate, Status, Table, money, useAction, useLoad, when } from "@/features/controlplane/ui";
import { partnerApi } from "@/lib/api/controlPlane";
import {
  panelApi,
  type AffiliateLink,
  type AffiliateOverview,
  type CurrencyAmount,
  type Earning,
  type PromotableProduct,
} from "@/lib/api/partnerPanel";
import { Amounts, Kpi, NotAvailable, PageHeader, usePanel } from "./PanelKit";
import { CopyButton, percent, useRangeControls } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * The affiliate workspace's own screens (ADR-047, ADR-053): a dashboard, the
 * referral links and codes, campaigns, marketing materials and the leads.
 *
 * Three ideas hold across all of them:
 *
 * - **The server decides.** A link's address and where it leads are built by the
 *   server from the product chosen - the browser sends a product code, a plan
 *   code, a campaign id or a code of the partner's choosing, never a URL. Every
 *   figure (clicks, visitors, sign-ups, sales, commission) is the server's.
 * - **A campaign is tracking.** It groups the partner's links; it never changes
 *   who earns commission or how much. Commission comes only from a verified,
 *   paid sale - never from a click.
 * - **Nothing identifies a visitor.** The rows are dates, a campaign and what was
 *   bought; the platform keeps no name or address of a referred person.
 */

const useOverview = () => {
  const { id } = usePanel();
  const controls = useRangeControls();
  const [load, reload] = useLoad(
    () => panelApi.affiliateOverview(id, controls.range, controls.from, controls.to),
    [id, controls.range, controls.from ?? "", controls.to ?? ""],
  );
  return { id, load, reload, controls };
};

const noMoney = "Needs commission access.";

/** Sales and commission per currency, from the server's ledger figures. */
function EarningCells({ earnings }: { earnings: Earning[] | null }) {
  if (earnings === null) {
    return (
      <>
        <td className={styles.muted}>—</td>
        <td className={styles.muted}>—</td>
      </>
    );
  }
  const sales: CurrencyAmount[] = earnings.map((e) => ({ currency: e.currency, amount_minor: e.sales_minor }));
  const commission: CurrencyAmount[] = earnings.map((e) => ({ currency: e.currency, amount_minor: e.commission_minor }));
  return (
    <>
      <td className={styles.num}>
        <Amounts amounts={sales} />
      </td>
      <td className={styles.num}>
        <Amounts amounts={commission} />
      </td>
    </>
  );
}

const productLabel = (link: AffiliateLink): string =>
  link.product_code ? `${link.product_name ?? link.product_code}${link.plan_code ? ` · ${link.plan_code}` : ""}` : "Sign-up page";

// --- the dashboard ------------------------------------------------------------------------------------------------

function LinkBlock({ link, createHref }: { link: AffiliateLink | null; createHref: string | null }) {
  const inputId = useId();
  if (!link || !link.url) {
    return (
      <section className={styles.linkBox} aria-labelledby={`${inputId}-h`}>
        <h2 id={`${inputId}-h`}>Your Referral Link</h2>
        <p>You have no active referral link yet.</p>
        {createHref && (
          <p>
            <Link className="w360-btn w360-btn-primary" href={createHref}>
              Create your first link
            </Link>
          </p>
        )}
      </section>
    );
  }
  return (
    <section className={styles.linkBox} aria-labelledby={`${inputId}-h`}>
      <h2 id={`${inputId}-h`}>Your Referral Link</h2>
      <div className={styles.linkRow}>
        <label className="w360-visually-hidden" htmlFor={inputId}>
          Your referral link
        </label>
        <input id={inputId} className="w360-input" readOnly value={link.url} onFocus={(e) => e.currentTarget.select()} />
        <CopyButton text={link.url} label="Link" />
      </div>
      <p className={styles.muted}>
        Opens: {productLabel(link)}. Campaign: {link.campaign}. You earn commission when a visitor who followed it buys
        and their payment is confirmed.
      </p>
    </section>
  );
}

/** `grow` is the upgrade offer (ADR-049), drawn last: the dashboard is otherwise unchanged. */
export function AffiliateDashboard({ grow }: { grow?: React.ReactNode } = {}) {
  const { partner, base, can, active } = usePanel();
  const { load, controls } = useOverview();
  return (
    <div className={styles.page}>
      <PageHeader
        title={`Welcome, ${partner?.display_name ?? ""}`}
        lead="Share your link, see who followed it and what you have earned. Every figure is calculated by Workeasy360."
        actions={controls.node}
      />
      <LoadGate load={load} what="your dashboard">
        {(d) => (
          <>
            <LinkBlock link={d.primary_link} createHref={can("referral.create") && active ? `${base}/referral-links` : null} />
            <Kpis d={d} />
            <div className={styles.twoCol}>
              <section className={styles.panel} aria-labelledby="by-campaign">
                <h2 id="by-campaign">By campaign</h2>
                <Table
                  caption="Clicks, sign-ups and conversions by campaign"
                  head={["Campaign", "Links", "Clicks", "Sign-ups", "Conversions"]}
                  isEmpty={d.campaigns.length === 0}
                  empty="No links yet."
                >
                  {d.campaigns.map((c) => (
                    <tr key={c.campaign}>
                      <th scope="row">{c.campaign}</th>
                      <td className={styles.num}>{c.links}</td>
                      <td className={styles.num}>{c.clicks}</td>
                      <td className={styles.num}>{c.registrations}</td>
                      <td className={styles.num}>{c.conversions}</td>
                    </tr>
                  ))}
                </Table>
              </section>
              <section className={styles.panel} aria-labelledby="top-products">
                <h2 id="top-products">Top products</h2>
                {d.top_products == null ? (
                  <p className={styles.muted}>{noMoney}</p>
                ) : (
                  <Table
                    caption="The products your referrals bought most"
                    head={["Product", "Sales", "Commission"]}
                    isEmpty={d.top_products.length === 0}
                    empty="No referred purchases in this period."
                  >
                    {d.top_products.map((p) => (
                      <tr key={p.product_code}>
                        <th scope="row">{p.product_name}</th>
                        <td className={styles.num}>{p.sales}</td>
                        <td className={styles.num}>
                          <Amounts amounts={p.earnings.map((e) => ({ currency: e.currency, amount_minor: e.commission_minor }))} />
                        </td>
                      </tr>
                    ))}
                  </Table>
                )}
              </section>
            </div>
            <section className={styles.panel} aria-labelledby="recent-conv">
              <h2 id="recent-conv">Recent conversions</h2>
              <ConversionTable rows={d.recent.conversions} fallbackCurrency={d.partner.currency} caption="Your latest conversions" />
              <p>
                <Link href={`${base}/conversions`}>All conversions</Link>
              </p>
            </section>
          </>
        )}
      </LoadGate>
      {grow}
    </div>
  );
}

function Kpis({ d }: { d: AffiliateOverview }) {
  return (
    <ul className={styles.kpis} aria-label="Key figures">
      <Kpi icon="network" label="Referral clicks" value={d.kpis.clicks} note="Times a link was followed" />
      <Kpi icon="users" label="Unique visitors" value={d.kpis.unique_visitors} note="Different browsers that followed a link" />
      <Kpi icon="users" label="Registrations" value={d.kpis.signups} note="Created a Workeasy360 account" />
      <Kpi icon="bolt" label="Conversions" value={d.kpis.conversions} note="Followed your link, now a customer" />
      <Kpi
        icon="chart"
        label="Conversion rate"
        value={percent(d.kpis.conversion_rate_bp)}
        note={d.kpis.conversion_rate_bp === null ? "No clicks in this period" : "Conversions ÷ clicks"}
      />
      <Kpi
        icon="receipt"
        label="Sales attributed"
        value={<Amounts amounts={d.money?.sales} />}
        note="Paid sales credited to your referrals"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi
        icon="history"
        label="Pending commission"
        value={<Amounts amounts={d.money?.pending} />}
        note="Still in the holding period"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi
        icon="currency"
        label="Approved commission"
        value={<Amounts amounts={d.money?.available} />}
        note="Cleared and ready for a payout"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi icon="handshake" label="Paid commission" value={<Amounts amounts={d.money?.paid} />} note="Paid out to you" unavailable={d.money ? undefined : noMoney} />
    </ul>
  );
}

function ConversionTable({
  rows,
  fallbackCurrency,
  caption,
}: {
  rows: AffiliateOverview["recent"]["conversions"];
  fallbackCurrency: string;
  caption: string;
}) {
  return (
    <Table caption={caption} head={["Became a customer", "Campaign", "Bought", "Sale", "Commission"]} isEmpty={rows.length === 0} empty="No conversions yet.">
      {rows.map((c) => (
        <tr key={c.id}>
          <th scope="row">{when(c.converted_at)}</th>
          <td>{c.code_label || "General"}</td>
          <td>{c.products && c.products.length ? c.products.map((p) => `${p.product_code}${p.plan_code ? ` (${p.plan_code})` : ""}`).join(", ") : "Not yet"}</td>
          <td className={styles.num}>
            {c.commission_minor === null || !c.sales_minor ? "—" : money(c.sales_minor, c.currency || fallbackCurrency)}
          </td>
          <td className={styles.num}>
            {c.commission_minor === null ? "—" : money(c.commission_minor, c.currency || fallbackCurrency)}
          </td>
        </tr>
      ))}
    </Table>
  );
}

// --- referral links ------------------------------------------------------------------------------------------------------

function CreateLinkForm({ d, onCreated }: { d: AffiliateOverview; onCreated: () => void }) {
  const { id } = usePanel();
  const { act, busy, feedback } = useAction(onCreated);
  const formId = useId();
  const [product, setProduct] = useState("");
  const [plan, setPlan] = useState("");
  const [campaign, setCampaign] = useState("");
  const [code, setCode] = useState("");
  const products = d.products ?? [];
  const chosen = products.find((p) => p.code === product);
  const campaigns = (d.campaign_list ?? []).filter((c) => c.status === "active");
  return (
    <form
      className={styles.panel}
      onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          await panelApi.createTrackingLink(id, {
            product_code: product || undefined,
            plan_code: plan || undefined,
            campaign_id: campaign || undefined,
            code: code.trim() || undefined,
          });
          setCode("");
        }, "Referral link created.");
      }}
    >
      <h2>Create a referral link</h2>
      <p className={styles.muted}>
        Choose the product the link opens. A campaign groups your links for tracking; it never changes who earns
        commission or how much.
      </p>
      {feedback}
      <div className={styles.formGrid}>
        <div>
          <label className="w360-label" htmlFor={`${formId}-product`}>
            Product
          </label>
          <select
            id={`${formId}-product`}
            className="w360-input"
            value={product}
            onChange={(e) => {
              setProduct(e.target.value);
              setPlan("");
            }}
          >
            <option value="">Any product (opens sign-up)</option>
            {products.map((p) => (
              <option key={p.code} value={p.code}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${formId}-plan`}>
            Plan (optional)
          </label>
          <select id={`${formId}-plan`} className="w360-input" value={plan} disabled={!chosen} onChange={(e) => setPlan(e.target.value)}>
            <option value="">Any plan</option>
            {(chosen?.plans ?? []).map((p) => (
              <option key={p.plan_code} value={p.plan_code}>
                {p.plan_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${formId}-campaign`}>
            Campaign (optional)
          </label>
          <select id={`${formId}-campaign`} className="w360-input" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
            <option value="">No campaign</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
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
            aria-describedby={`${formId}-code-help`}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
          <span id={`${formId}-code-help`} className={styles.muted}>
            4 to 16 letters and digits, e.g. AMAN360. Leave empty for a generated code.
          </span>
        </div>
      </div>
      <div>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
          Generate link
        </button>
      </div>
    </form>
  );
}

export function ReferralLinks() {
  const { can, active, partner } = usePanel();
  const { id, load, reload, controls } = useOverview();
  const { act, busy, feedback } = useAction(reload);
  const create = can("referral.create") && active;
  return (
    <div className={styles.page}>
      <PageHeader
        title="Referral Links"
        lead="Each link is yours alone. Point it at a product, share it where you post, and see how each one did."
        actions={controls.node}
      />
      {feedback}
      <LoadGate load={load} what="your links">
        {(d) => (
          <>
            {create && <CreateLinkForm d={d} onCreated={reload} />}
            <Table
              caption="Your referral links"
              head={["Link", "Opens", "Campaign", "Clicks", "Visitors", "Sign-ups", "Conversions", "Sales", "Commission", "Status", "Actions"]}
              isEmpty={d.links.length === 0}
              empty="No links yet."
            >
              {d.links.map((l) => (
                <tr key={l.id}>
                  <th scope="row" className={styles.mono}>
                    {l.url ?? `${l.code} (switched off)`}
                  </th>
                  <td>{productLabel(l)}</td>
                  <td>{l.campaign}</td>
                  <td className={styles.num}>{l.clicks}</td>
                  <td className={styles.num}>{l.visitors}</td>
                  <td className={styles.num}>{l.registrations}</td>
                  <td className={styles.num}>{l.conversions}</td>
                  <EarningCells earnings={l.earnings} />
                  <td>
                    <Status value={l.status} />
                  </td>
                  <td>
                    <div className={styles.actions}>
                      {l.url && <CopyButton text={l.url} label={`link ${l.code}`} />}
                      {l.url && create && (
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          disabled={busy}
                          onClick={() => void act(() => panelApi.disableTrackingLink(id, l.id), "Link switched off. Earlier referrals still count.")}
                        >
                          Switch off<span className="w360-visually-hidden"> link {l.code}</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>
              Clicks, visitors, sign-ups and conversions count visits in the chosen period; sales and commission come from
              the ledger. {partner ? `${partner.display_name} is paid in its payout currency.` : ""}
            </p>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- referral codes ------------------------------------------------------------------------------------------------------------

export function ReferralCodes() {
  const { can, active } = usePanel();
  const { id, load, reload } = useOverview();
  const { act, busy, feedback } = useAction(reload);
  const codeId = useId();
  const [code, setCode] = useState("");
  const create = can("referral.create") && active;
  return (
    <div className={styles.page}>
      <PageHeader
        title="Referral Codes"
        lead="The short code that identifies you. A visitor who uses your link or your code is credited to you."
      />
      {feedback}
      {create && (
        <form
          className={styles.panel}
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await panelApi.createTrackingLink(id, { code: code.trim() });
              setCode("");
            }, "Referral code created.");
          }}
        >
          <h2>Create your own code</h2>
          <label className="w360-label" htmlFor={codeId}>
            Code
          </label>
          <div className={styles.linkRow}>
            <input
              id={codeId}
              className="w360-input"
              required
              minLength={4}
              maxLength={16}
              pattern="[A-Za-z0-9]{4,16}"
              aria-describedby={`${codeId}-help`}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
            />
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || code.trim().length < 4}>
              Create code
            </button>
          </div>
          <p id={`${codeId}-help`} className={styles.muted}>
            4 to 16 letters and digits with at least one letter, e.g. AMAN360. Codes are unique across Workeasy360.
          </p>
        </form>
      )}
      <LoadGate load={load} what="your codes">
        {(d) => (
          <>
            <Table
              caption="Your referral codes"
              head={["Code", "Opens", "Campaign", "Status", "Created", "Clicks", "Conversions", "Copy"]}
              isEmpty={d.links.length === 0}
              empty="No codes yet."
            >
              {d.links.map((l) => (
                <tr key={l.id}>
                  <th scope="row" className={styles.mono}>
                    {l.code}
                  </th>
                  <td>{productLabel(l)}</td>
                  <td>{l.campaign}</td>
                  <td>
                    <Status value={l.status} />
                  </td>
                  <td>{when(l.created_at)}</td>
                  <td className={styles.num}>{l.clicks}</td>
                  <td className={styles.num}>{l.conversions}</td>
                  <td>{l.status === "active" && <CopyButton text={l.code} label={`code ${l.code}`} />}</td>
                </tr>
              ))}
            </Table>
            <NotAvailable title="Discount or promo codes" reasons={[d.not_implemented.promo_discount_codes]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- campaigns --------------------------------------------------------------------------------------------------------------------

export function Campaigns() {
  const { base, can, active } = usePanel();
  const { id, load, reload, controls } = useOverview();
  const { act, busy, feedback } = useAction(reload);
  const nameId = useId();
  const [name, setName] = useState("");
  const manage = can("referral.create") && active;
  return (
    <div className={styles.page}>
      <PageHeader
        title="Campaigns"
        lead="Group your links by where you share them and compare how each campaign does."
        actions={controls.node}
      />
      {feedback}
      {manage && (
        <form
          className={styles.panel}
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              await panelApi.createCampaign(id, name.trim());
              setName("");
            }, `Campaign "${name.trim()}" created. Choose it when you create a link.`);
          }}
        >
          <h2>Start a campaign</h2>
          <label className="w360-label" htmlFor={nameId}>
            Campaign name
          </label>
          <div className={styles.linkRow}>
            <input
              id={nameId}
              className="w360-input"
              required
              maxLength={64}
              placeholder="CRM October Campaign"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !name.trim()}>
              Create campaign
            </button>
          </div>
        </form>
      )}
      <LoadGate load={load} what="your campaigns">
        {(d) => {
          const stats = new Map(d.campaigns.map((c) => [c.campaign_id ?? `label:${c.campaign}`, c]));
          const labelled = d.campaigns.filter((c) => !c.campaign_id);
          const listed = d.campaign_list ?? [];
          return (
            <>
              <Table
                caption="Campaign results"
                head={["Campaign", "Status", "Links", "Clicks", "Sign-ups", "Conversions", "Conversion rate", "Sales", "Commission", "Actions"]}
                isEmpty={listed.length === 0 && labelled.length === 0}
                empty="No campaigns yet."
              >
                {listed.map((c) => {
                  const s = stats.get(c.id);
                  return (
                    <tr key={c.id}>
                      <th scope="row">{c.name}</th>
                      <td>
                        <Status value={c.status === "active" ? "active" : "archived"} />
                      </td>
                      <td className={styles.num}>{c.links}</td>
                      <td className={styles.num}>{s?.clicks ?? 0}</td>
                      <td className={styles.num}>{s?.registrations ?? 0}</td>
                      <td className={styles.num}>{s?.conversions ?? 0}</td>
                      <td className={styles.num}>{percent(s?.conversion_rate_bp)}</td>
                      <EarningCells earnings={s ? s.earnings : d.money ? [] : null} />
                      <td>
                        {manage && (
                          <button
                            type="button"
                            className="w360-btn w360-btn-secondary"
                            disabled={busy}
                            onClick={() =>
                              void act(
                                () => panelApi.setCampaignActive(id, c.id, c.status !== "active"),
                                c.status === "active" ? "Campaign archived. Its links keep working." : "Campaign restored.",
                              )
                            }
                          >
                            {c.status === "active" ? "Archive" : "Restore"}
                            <span className="w360-visually-hidden"> {c.name}</span>
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {labelled.map((c) => (
                  <tr key={`label:${c.campaign}`}>
                    <th scope="row">{c.campaign}</th>
                    <td className={styles.muted}>label</td>
                    <td className={styles.num}>{c.links}</td>
                    <td className={styles.num}>{c.clicks}</td>
                    <td className={styles.num}>{c.registrations}</td>
                    <td className={styles.num}>{c.conversions}</td>
                    <td className={styles.num}>{percent(c.conversion_rate_bp)}</td>
                    <EarningCells earnings={c.earnings} />
                    <td />
                  </tr>
                ))}
              </Table>
              <p className={styles.muted}>
                Add a link to a campaign on <Link href={`${base}/referral-links`}>Referral Links</Link>. Links made before
                campaigns existed are grouped by their label.
              </p>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

// --- marketing materials --------------------------------------------------------------------------------------------------------------

function ProductMaterials({ product, canLink }: { product: PromotableProduct; canLink: boolean }) {
  const { id } = usePanel();
  const [made, setMade] = useState<string | null>(null);
  const { act, busy, feedback } = useAction();
  const images = product.media;
  return (
    <li className={styles.card}>
      <div className={styles.cardHead}>
        <h3 className={styles.cardTitle}>{product.name}</h3>
      </div>
      {product.tagline && <p className={styles.muted}>{product.tagline}</p>}
      {images.length > 0 ? (
        <ul className={styles.mediaRow} aria-label={`${product.name} images`}>
          {images.map((m) => (
            <li key={m.id}>
              {/* eslint-disable-next-line @next/next/no-img-element -- same-origin listing image, sized by CSS */}
              <img className={styles.mediaThumb} src={m.url} alt={m.caption || `${product.name} ${m.kind}`} loading="lazy" />
              <a href={`${m.url}?download=1`} download>
                Download {m.kind}
                <span className="w360-visually-hidden"> for {product.name}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.muted}>No images published for this product yet.</p>
      )}
      <div>
        <h4>Approved description</h4>
        <p>{product.summary || product.description || "—"}</p>
        {(product.summary || product.description) && (
          <CopyButton text={product.summary || product.description} label={`${product.name} description`} />
        )}
      </div>
      {canLink && (
        <div className={styles.actions}>
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const created = await panelApi.createTrackingLink(id, { product_code: product.code });
                setMade(created.url);
              }, `Referral link for ${product.name} created.`)
            }
          >
            Create a link for {product.name}
          </button>
        </div>
      )}
      {feedback}
      {made && (
        <p className={styles.linkRow}>
          <span className={styles.mono}>{made}</span>
          <CopyButton text={made} label={`${product.name} link`} />
        </p>
      )}
    </li>
  );
}

export function MarketingMaterials() {
  const { can, active } = usePanel();
  const { load } = useOverview();
  return (
    <div className={styles.page}>
      <PageHeader
        title="Marketing Materials"
        lead="Logos, banners and approved descriptions published by Workeasy360 for the products you can promote."
      />
      <LoadGate load={load} what="marketing materials">
        {(d) =>
          (d.products ?? []).length === 0 ? (
            <p>No products are open for promotion right now.</p>
          ) : (
            <ul className={styles.cards} aria-label="Products you can promote">
              {(d.products ?? []).map((p) => (
                <ProductMaterials key={p.code} product={p} canLink={can("referral.create") && active} />
              ))}
            </ul>
          )
        }
      </LoadGate>
    </div>
  );
}

// --- leads and referrals ------------------------------------------------------------------------------------------------------------------

export function Leads() {
  const { id } = usePanel();
  const [load] = useLoad(() => partnerApi.referrals(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Leads & Referrals"
        lead="Everyone who followed one of your links. For their privacy, no name, email or address is kept or shown."
      />
      <LoadGate load={load} what="your referrals">
        {(d) => (
          <>
            <ul className={styles.kpis} aria-label="Referral totals">
              <Kpi icon="network" label="Followed your link" value={d.total} />
              <Kpi icon="users" label="Waiting" value={d.counts.captured} note="Followed a link, not yet a customer" />
              <Kpi icon="bolt" label="Became customers" value={d.counts.converted} />
              <Kpi icon="history" label="Expired" value={d.counts.expired} note="Never became a customer in time" />
            </ul>
            <Table
              caption="Your referrals, newest first"
              head={["Followed your link", "Campaign", "Signed up", "Status", "Became a customer"]}
              isEmpty={d.referrals.results.length === 0}
              empty="No one has followed your link yet."
            >
              {d.referrals.results.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{when(r.captured_at)}</th>
                  <td>{r.label || "General"}</td>
                  <td>{r.registered_at ? when(r.registered_at) : r.converted_at ? "Yes" : "—"}</td>
                  <td>
                    <Status value={r.status} />
                  </td>
                  <td>{r.converted_at ? when(r.converted_at) : "—"}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </LoadGate>
    </div>
  );
}
