"use client";

import Link from "next/link";
import { useState } from "react";
import { when } from "@/features/controlplane/ui";
import { StoreBrowser, StoreCard, planTypeLabels } from "@/features/storefront/StoreBrowser";
import { panelApi, type PartnerStoreCard, type PartnerStorePage, type RequestKind } from "@/lib/api/partnerPanel";
import { usePanel } from "./PanelKit";
import { AccessBadge, RequestForm, earningText } from "./StoreConsole";
import styles from "./PartnerPanel.module.css";

/**
 * A partner's Store: the Store Console's Explore Products as a marketplace
 * page - the shared Store layout over `GET partner/<id>/<kind>/marketplace`.
 * An affiliate sees what it may ask to promote (`promote`); a reseller or a
 * white-label partner what it may ask to sell (`sell`, the server picks the
 * channel). Each card shows this partner's access, what a sale earns (only to
 * a member allowed to see it) and the one thing to do next: ask for access,
 * or open the approved product.
 *
 * Nothing here is a gate: a request grants nothing until staff approve it, and
 * the server refuses a link or a sale without approval whatever this page shows.
 */

const LEADS: Record<RequestKind, string> = {
  promote: "Every Workeasy360 product you can promote. Ask for access to the ones you want; approved products get their links under Approved / Promoting.",
  sell: "Every Workeasy360 product you can sell. Ask for access to the ones you want; approved products are yours to sell to customers.",
};

export function PartnerStore({ kind, mine }: { kind: RequestKind; mine: { path: string; label: string } }) {
  const { id, base } = usePanel();
  const [asking, setAsking] = useState<PartnerStoreCard | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // A sent request changes the card's status: fetch the page again.
  const [round, setRound] = useState(0);

  return (
    <StoreBrowser<PartnerStoreCard, PartnerStorePage>
      title="Store"
      lead={LEADS[kind]}
      load={(query) => panelApi.marketplace(id, kind, { ...query })}
      loadKey={`${id}#${kind}#${round}`}
      statusTitle="Access"
      forbidden="You don't have access to the Store."
      nothingYet={{
        title: kind === "sell" ? "No products are open to sellers yet." : "No products are open to affiliates yet.",
      }}
      above={(page) => (
        <>
          {!page.can_request && page.cannot_request_reason && (
            <p className={styles.muted} role="note">
              {page.cannot_request_reason}
            </p>
          )}
          {done && (
            <p role="status" className={styles.notice}>
              {done}
            </p>
          )}
          {asking && (
            <RequestForm
              product={asking}
              kind={kind}
              data={page}
              onCancel={() => setAsking(null)}
              onSent={(name) => {
                setAsking(null);
                setDone(`Request sent for ${name}. It shows as Pending approval until Workeasy360 decides.`);
                setRound((n) => n + 1);
              }}
            />
          )}
        </>
      )}
      renderCard={(card, page, onCategory) => {
        const earning = earningText(card);
        const again = card.status === "rejected" || card.status === "revoked";
        return (
          <StoreCard
            key={card.code}
            code={card.code}
            title={card.title}
            icon={card.icon}
            logoSrc={card.logo_url}
            bannerSrc={card.banner_url ?? null}
            ribbon={card.ribbon && { label: card.ribbon.label, tone: card.ribbon.kind === "new" ? "new" : "trial" }}
            category={card.category}
            onCategory={onCategory}
            summary={card.summary}
            badges={card.badges}
            chips={planTypeLabels(card.plan_types, page.facets.plan_types).map((label, i) => ({
              label,
              trial: card.plan_types[i] === "free_trial",
            }))}
            facts={
              <>
                <AccessBadge status={card.status} />
                {earning && <p>{earning}</p>}
                {card.status === "pending" && card.request && <p>Requested on {when(card.request.requested_at)}.</p>}
                {card.status === "rejected" && card.request?.decision_reason && <p>Reason: {card.request.decision_reason}</p>}
                {card.status === "revoked" && card.request?.revoke_reason && <p>Reason: {card.request.revoke_reason}</p>}
              </>
            }
            price={card.price}
            deal={card.yearly_deal}
            noPrice="No price yet"
            actions={
              card.status === "approved" ? (
                <Link className="w360-btn w360-btn-secondary" href={`${base}/${mine.path}`} aria-label={`Open ${card.name} in ${mine.label}`}>
                  Open in {mine.label}
                </Link>
              ) : card.status !== "pending" && page.can_request ? (
                <button
                  type="button"
                  className="w360-btn w360-btn-primary"
                  onClick={() => {
                    setDone(null);
                    setAsking(card);
                  }}
                  aria-label={`${again ? "Request again" : "Request access"} to ${card.name}`}
                >
                  {again ? "Request again" : "Request access"}
                </button>
              ) : null
            }
          />
        );
      }}
    />
  );
}
