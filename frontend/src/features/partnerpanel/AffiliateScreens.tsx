"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { LoadGate, Status, Table, useAction, useLoad, when } from "@/features/controlplane/ui";
import { partnerApi } from "@/lib/api/controlPlane";
import { panelApi, type AffiliateLink, type AffiliateOverview } from "@/lib/api/partnerPanel";
import { Amounts, Kpi, NotAvailable, PageHeader, usePanel } from "./PanelKit";
import { CopyButton, percent, useRangeControls } from "./workspaceKit";
import styles from "./PartnerPanel.module.css";

/**
 * The affiliate workspace's own screens (ADR-047): a dashboard, the tracking links
 * and the codes behind them, campaigns, materials and the leads.
 *
 * Three ideas hold across all of them:
 *
 * - **A campaign is a label.** A "tracking link" is a referral code filed under a
 *   campaign name; the name is tracking metadata and reaches no commission, no
 *   attribution and no permission. The link's address is built by the server from
 *   configuration - the browser never composes or edits one.
 * - **Nothing identifies a visitor.** The rows are dates, a campaign and a status;
 *   the platform keeps no name or address of a referred person.
 * - **Gaps are stated.** Signups, marketing assets and discount codes are not
 *   built; each screen says so, in the server's words, instead of showing a zero.
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
      <p className={styles.muted}>Campaign: {link.campaign}. Visitors who follow it and sign up are credited to you.</p>
    </section>
  );
}

export function AffiliateDashboard() {
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
                  caption="Clicks and conversions by campaign"
                  head={["Campaign", "Links", "Clicks", "Conversions"]}
                  isEmpty={d.campaigns.length === 0}
                  empty="No links yet."
                >
                  {d.campaigns.map((c) => (
                    <tr key={c.campaign}>
                      <th scope="row">{c.campaign}</th>
                      <td className={styles.num}>{c.links}</td>
                      <td className={styles.num}>{c.clicks}</td>
                      <td className={styles.num}>{c.conversions}</td>
                    </tr>
                  ))}
                </Table>
              </section>
              <section className={styles.panel} aria-labelledby="recent-conv">
                <h2 id="recent-conv">Recent conversions</h2>
                <Table
                  caption="Your latest conversions"
                  head={["Date", "Campaign", "Commission"]}
                  isEmpty={d.recent.conversions.length === 0}
                  empty="No conversions yet."
                >
                  {d.recent.conversions.map((c) => (
                    <tr key={c.id}>
                      <th scope="row">{when(c.converted_at)}</th>
                      <td>{c.code_label || "General"}</td>
                      <td className={styles.num}>
                        {c.commission_minor === null ? (
                          "—"
                        ) : (
                          <Amounts amounts={[{ currency: c.currency || d.partner.currency, amount_minor: c.commission_minor }]} />
                        )}
                      </td>
                    </tr>
                  ))}
                </Table>
                <p>
                  <Link href={`${base}/conversions`}>All conversions</Link>
                </p>
              </section>
            </div>
          </>
        )}
      </LoadGate>
    </div>
  );
}

function Kpis({ d }: { d: AffiliateOverview }) {
  const noMoney = "Needs commission access.";
  return (
    <ul className={styles.kpis} aria-label="Key figures">
      <Kpi icon="network" label="Clicks" value={d.kpis.clicks} note="Visitors who followed your link" />
      <Kpi icon="users" label="Signups" unavailable={d.not_implemented.affiliate_signups} />
      <Kpi icon="bolt" label="Conversions" value={d.kpis.conversions} note="Followed your link, now a customer" />
      <Kpi
        icon="chart"
        label="Conversion rate"
        value={percent(d.kpis.conversion_rate_bp)}
        note={d.kpis.conversion_rate_bp === null ? "No clicks in this period" : "Conversions ÷ clicks"}
      />
      <Kpi icon="currency" label="Commission earned" value={<Amounts amounts={d.money?.earned} />} unavailable={d.money ? undefined : noMoney} />
      <Kpi
        icon="history"
        label="Pending commission"
        value={<Amounts amounts={d.money?.pending} />}
        note="Still in the holding period"
        unavailable={d.money ? undefined : noMoney}
      />
      <Kpi icon="handshake" label="Paid commission" value={<Amounts amounts={d.money?.paid} />} note="Paid out to you" unavailable={d.money ? undefined : noMoney} />
    </ul>
  );
}

// --- referral links ------------------------------------------------------------------------------------------------------

export function ReferralLinks() {
  const { can, active, partner } = usePanel();
  const { id, load, reload } = useOverview();
  const { act, busy, feedback } = useAction(reload);
  const campaignId = useId();
  const [campaign, setCampaign] = useState("General");
  const [custom, setCustom] = useState("");
  const create = can("referral.create") && active;
  return (
    <div className={styles.page}>
      <PageHeader
        title="Referral Links"
        lead="Each link is yours alone. Share the one for the place you are posting, and the dashboard shows how each did."
      />
      {feedback}
      <LoadGate load={load} what="your links">
        {(d) => (
          <>
            <LinkBlock link={d.primary_link} createHref={null} />
            {create && (
              <form
                className={styles.panel}
                onSubmit={(e) => {
                  e.preventDefault();
                  const name = (campaign === "__other" ? custom : campaign).trim();
                  void act(async () => {
                    await panelApi.createTrackingLink(id, name);
                    setCustom("");
                  }, `Tracking link created for ${name}.`);
                }}
              >
                <h2>Create a tracking link</h2>
                <p className={styles.muted}>
                  A campaign name is only a label for your own tracking. It never changes who earns commission or how much.
                </p>
                <label className="w360-label" htmlFor={campaignId}>
                  Campaign
                </label>
                <select id={campaignId} className="w360-input" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
                  {d.campaign_presets.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                  <option value="__other">Another name…</option>
                </select>
                {campaign === "__other" && (
                  <>
                    <label className="w360-label" htmlFor={`${campaignId}-other`}>
                      Campaign name
                    </label>
                    <input
                      id={`${campaignId}-other`}
                      className="w360-input"
                      required
                      maxLength={64}
                      value={custom}
                      onChange={(e) => setCustom(e.target.value)}
                    />
                  </>
                )}
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || (campaign === "__other" && !custom.trim())}>
                    Create tracking link
                  </button>
                </div>
              </form>
            )}
            <Table
              caption="Your tracking links"
              head={["Campaign", "Link", "Clicks", "Conversions", "Status", "Actions"]}
              isEmpty={d.links.length === 0}
              empty="No links yet."
            >
              {d.links.map((l) => (
                <tr key={l.id}>
                  <th scope="row">{l.campaign}</th>
                  <td className={styles.mono}>{l.url ?? "Switched off"}</td>
                  <td className={styles.num}>{l.clicks}</td>
                  <td className={styles.num}>{l.conversions}</td>
                  <td>
                    <Status value={l.status} />
                  </td>
                  <td>
                    <div className={styles.actions}>
                      {l.url && <CopyButton text={l.url} label={`${l.campaign} link`} />}
                      {l.url && create && (
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          disabled={busy}
                          onClick={() => void act(() => panelApi.disableTrackingLink(id, l.id), "Link switched off. Earlier referrals still count.")}
                        >
                          Switch off<span className="w360-visually-hidden"> {l.campaign} link</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>
              Figures cover the last 30 days. {partner ? `${partner.display_name} is paid in its payout currency.` : ""}
            </p>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- referral codes ------------------------------------------------------------------------------------------------------------

export function ReferralCodes() {
  const { load } = useOverview();
  return (
    <div className={styles.page}>
      <PageHeader title="Referral Codes" lead="The short code inside each of your links. It identifies you when a visitor signs up." />
      <LoadGate load={load} what="your codes">
        {(d) => (
          <>
            <Table
              caption="Your referral codes"
              head={["Code", "Campaign", "Status", "Created", "Clicks", "Conversions", "Copy"]}
              isEmpty={d.links.length === 0}
              empty="No codes yet. Create a tracking link and its code appears here."
            >
              {d.links.map((l) => (
                <tr key={l.id}>
                  <th scope="row" className={styles.mono}>
                    {l.code}
                  </th>
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
  const { base } = usePanel();
  const { load, controls } = useOverview();
  return (
    <div className={styles.page}>
      <PageHeader
        title="Campaigns"
        lead="Group your links by where you share them. A campaign is a label for your own tracking."
        actions={controls.node}
      />
      <LoadGate load={load} what="your campaigns">
        {(d) => (
          <>
            <Table
              caption="Campaign results"
              head={["Campaign", "Links", "Clicks", "Conversions", "Conversion rate"]}
              isEmpty={d.campaigns.length === 0}
              empty="No campaigns yet. Create a tracking link and give it a campaign."
            >
              {d.campaigns.map((c) => (
                <tr key={c.campaign}>
                  <th scope="row">{c.campaign}</th>
                  <td className={styles.num}>{c.links}</td>
                  <td className={styles.num}>{c.clicks}</td>
                  <td className={styles.num}>{c.conversions}</td>
                  <td className={styles.num}>{percent(c.conversion_rate_bp)}</td>
                </tr>
              ))}
            </Table>
            <p className={styles.muted}>
              Suggested campaigns: {d.campaign_presets.join(", ")}. A campaign never changes who earns commission or how much. Add one on{" "}
              <Link href={`${base}/referral-links`}>Referral Links</Link>.
            </p>
          </>
        )}
      </LoadGate>
    </div>
  );
}

// --- marketing materials --------------------------------------------------------------------------------------------------------------

export function MarketingMaterials() {
  const { load } = useOverview();
  return (
    <div className={styles.page}>
      <PageHeader title="Marketing Materials" lead="Logos, banners and approved descriptions to share alongside your link." />
      <LoadGate load={load} what="marketing materials">
        {(d) => (
          <>
            <NotAvailable title="Not available yet" reasons={[d.not_implemented.marketing_materials]} />
            <LinkBlock link={d.primary_link} createHref={null} />
          </>
        )}
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
              head={["Followed your link", "Campaign", "Status", "Became a customer"]}
              isEmpty={d.referrals.results.length === 0}
              empty="No one has followed your link yet."
            >
              {d.referrals.results.map((r) => (
                <tr key={r.id}>
                  <th scope="row">{when(r.captured_at)}</th>
                  <td>{r.label || "General"}</td>
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
