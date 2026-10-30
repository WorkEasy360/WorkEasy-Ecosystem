"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AccessBadge } from "@/features/partnerpanel/StoreConsole";
import {
  adminProgrammeApi,
  type PromotionRequestDetail,
  type PromotionRequestQueue,
  type PromotionRequestStatus,
} from "@/lib/api/adminProgramme";
import { can, useAdmin } from "./ControlShell";
import { Section } from "./PartnerSection";
import { LoadGate, Status, Table, useAction, useLoad, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partners → Product Requests (ADR-055): affiliates asking to promote a Store
 * product, and the decision.
 *
 * Approving grants the partner that one product's promotion permission - the
 * same grant "Products this partner may promote" makes by hand - and nothing
 * else. Rejecting needs a reason the partner reads. Revoking stops new links at
 * once; referrals and commission already earned are kept. Every action is
 * checked again on the server (`platform.staff_partner.manage`), and a request
 * someone else decided a moment ago is refused, not decided twice.
 */

const TABS: { key: PromotionRequestStatus | "all"; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "revoked", label: "Revoked" },
  { key: "all", label: "All" },
];

const PAGE = 50;

export function ProductRequestsConsole() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/admin/partners/product-requests";
  const status = (params?.get("status") as PromotionRequestStatus | "all" | null) ?? "pending";
  const q = params?.get("q") ?? "";
  const offset = Number(params?.get("offset") ?? 0) || 0;
  const open = params?.get("request") ?? null;
  const searchId = useId();
  const [draft, setDraft] = useState(q);
  const [load, reload] = useLoad(() => adminProgrammeApi.promotionRequests({ status, q: q || undefined, offset }), [status, q, offset]);

  const go = (next: Record<string, string | null>) => {
    const query = new URLSearchParams(params?.toString() ?? "");
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === "") query.delete(k);
      else query.set(k, v);
    }
    const text = query.toString();
    router.push(text ? `${pathname}?${text}` : pathname);
  };

  return (
    <div className={styles.stack}>
      <div className={local.headerRow}>
        <h1>Product Requests</h1>
      </div>
      <p className={styles.muted}>
        Affiliates ask here to promote a product from the Store. Approving lets that partner create links, codes and campaigns for that one
        product; it gives no other access.
      </p>
      <nav aria-label="Request status" className={local.filters}>
        {TABS.map((t) => {
          const count = load.status === "ready" && t.key !== "all" ? load.data.counts[t.key] : null;
          return (
            <Link
              key={t.key}
              href={`${pathname}?status=${t.key}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
              aria-current={status === t.key ? "page" : undefined}
              className={status === t.key ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-secondary"}
            >
              {t.label}
              {count !== null ? ` (${count})` : ""}
            </Link>
          );
        })}
      </nav>
      <form
        className={local.filters}
        role="search"
        aria-label="Search requests"
        onSubmit={(e) => {
          e.preventDefault();
          go({ q: draft.trim().slice(0, 80), offset: null });
        }}
      >
        <div className={local.searchField}>
          <label className="w360-label" htmlFor={searchId}>
            Search by partner or product
          </label>
          <input id={searchId} type="search" className="w360-input" maxLength={80} value={draft} onChange={(e) => setDraft(e.target.value)} />
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>
      {open && (
        <RequestReview
          requestId={open}
          onClose={() => go({ request: null })}
          onDecided={reload}
        />
      )}
      <LoadGate load={load} what="product requests">
        {(d) => <Queue data={d} status={status} onOpen={(id) => go({ request: id })} onPage={(o) => go({ offset: String(o) })} />}
      </LoadGate>
    </div>
  );
}

function Queue({
  data,
  status,
  onOpen,
  onPage,
}: {
  data: PromotionRequestQueue;
  status: string;
  onOpen: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  return (
    <>
      <Table
        caption={`Product requests: ${status}`}
        head={["Partner", "Product", "Channel", "Requested", "Status", ""]}
        isEmpty={data.items.length === 0}
        empty={status === "pending" ? "No request is waiting for a decision." : "No requests."}
      >
        {data.items.map((r) => (
          <tr key={r.id}>
            <th scope="row">
              <Link href={`/admin/partners/${r.partner_id}`}>{r.partner_name}</Link>
              <span className={styles.muted}> · {r.partner_code}</span>
            </th>
            <td>{r.product_name}</td>
            <td>{r.channel_label}</td>
            <td>{when(r.requested_at)}</td>
            <td>
              <AccessBadge status={r.status} />
            </td>
            <td>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onOpen(r.id)}>
                Review<span className="w360-visually-hidden"> the request from {r.partner_name} for {r.product_name}</span>
              </button>
            </td>
          </tr>
        ))}
      </Table>
      {data.total > data.limit && (
        <div className={styles.row}>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={data.offset === 0} onClick={() => onPage(Math.max(0, data.offset - PAGE))}>
            Previous
          </button>
          <span className={styles.muted}>
            {data.offset + 1}–{Math.min(data.offset + data.items.length, data.total)} of {data.total}
          </span>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            disabled={data.offset + data.items.length >= data.total}
            onClick={() => onPage(data.offset + PAGE)}
          >
            Next
          </button>
        </div>
      )}
    </>
  );
}

function RequestReview({ requestId, onClose, onDecided }: { requestId: string; onClose: () => void; onDecided: () => void }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [load, reload] = useLoad(() => adminProgrammeApi.promotionRequest(requestId), [requestId]);
  const { act, busy, feedback } = useAction(() => {
    reload();
    onDecided();
  });
  const [mode, setMode] = useState<"reject" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const reasonId = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => heading.current?.focus(), [requestId]);

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={`${reasonId}-h`}>
      <div className={local.headerRow}>
        <h2 id={`${reasonId}-h`} ref={heading} tabIndex={-1} className={styles.cardTitle}>
          Review request
        </h2>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onClose}>
          Close<span className="w360-visually-hidden"> the review</span>
        </button>
      </div>
      {feedback}
      <LoadGate load={load} what="the request">
        {(d: PromotionRequestDetail) => {
          const r = d.request;
          return (
            <div className={styles.stack}>
              <div className={styles.grid}>
                <Section title="Affiliate">
                  <dl className={styles.factsGrid}>
                    <dt>Partner</dt>
                    <dd>
                      <Link href={`/admin/partners/${d.partner.id}`}>{d.partner.name}</Link> ({d.partner.code})
                    </dd>
                    <dt>Account</dt>
                    <dd>
                      <Status value={d.partner.status} />
                    </dd>
                    <dt>Asked by</dt>
                    <dd>{r.requested_by}</dd>
                    <dt>Profile website</dt>
                    <dd>{d.partner.website || "—"}</dd>
                    <dt>Referral capability</dt>
                    <dd>{d.partner.referral_capability ? "Held" : "Not held - approval will be refused"}</dd>
                    <dt>Already promotes</dt>
                    <dd>{d.partner.promotes.length ? d.partner.promotes.join(", ") : "Nothing yet"}</dd>
                  </dl>
                </Section>
                <Section title="Product">
                  <dl className={styles.factsGrid}>
                    <dt>Product</dt>
                    <dd>
                      {d.product.name} ({d.product.code})
                    </dd>
                    <dt>Catalog status</dt>
                    <dd>
                      <Status value={d.product.status} />
                    </dd>
                    <dt>Open to affiliates</dt>
                    <dd>{d.product.eligible ? "Yes - on the Store for affiliates" : "No - approval will be refused"}</dd>
                  </dl>
                </Section>
              </div>
              <Section title="What the affiliate wrote">
                <dl className={styles.factsGrid}>
                  <dt>Status</dt>
                  <dd>
                    <AccessBadge status={r.status} />
                  </dd>
                  <dt>Channel</dt>
                  <dd>{r.channel_label}</dd>
                  <dt>Website or profile</dt>
                  <dd className={styles.mono}>{r.website || "—"}</dd>
                  <dt>Audience</dt>
                  <dd>{r.audience}</dd>
                  <dt>How they will promote it</dt>
                  <dd className={styles.preWrap}>{r.strategy}</dd>
                  <dt>Notes</dt>
                  <dd className={styles.preWrap}>{r.notes || "—"}</dd>
                </dl>
              </Section>

              {manage && (r.status === "pending" || r.status === "approved") && (
                <div className={styles.stack}>
                  {mode === null && (
                    <div className={styles.row}>
                      {r.status === "pending" && (
                        <>
                          <button
                            type="button"
                            className="w360-btn w360-btn-primary"
                            disabled={busy}
                            onClick={() => void act(() => adminProgrammeApi.decidePromotionRequest(r.id, "approve"), `Approved: ${d.partner.name} may promote ${d.product.name}.`)}
                          >
                            Approve
                          </button>
                          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => setMode("reject")}>
                            Reject
                          </button>
                        </>
                      )}
                      {r.status === "approved" && (
                        <button type="button" className="w360-btn w360-btn-danger" disabled={busy} onClick={() => setMode("revoke")}>
                          Revoke permission
                        </button>
                      )}
                    </div>
                  )}
                  {mode !== null && (
                    <form
                      className={styles.form}
                      onSubmit={(e) => {
                        e.preventDefault();
                        const action = mode;
                        void act(async () => {
                          await adminProgrammeApi.decidePromotionRequest(r.id, action, reason.trim() || undefined);
                          setMode(null);
                          setReason("");
                        }, action === "reject" ? "Request rejected. The affiliate sees your reason." : "Permission revoked. New links stop now; earned commission is kept.");
                      }}
                    >
                      <div className={styles.field}>
                        <label className="w360-label" htmlFor={reasonId}>
                          {mode === "reject" ? "Reason (the affiliate reads it)" : "Reason (optional, the affiliate reads it)"}
                        </label>
                        <textarea
                          id={reasonId}
                          className="w360-input"
                          rows={3}
                          maxLength={500}
                          required={mode === "reject"}
                          minLength={mode === "reject" ? 3 : undefined}
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          autoFocus
                        />
                      </div>
                      <div className={styles.row}>
                        <button type="submit" className={mode === "reject" ? "w360-btn w360-btn-primary" : "w360-btn w360-btn-danger"} disabled={busy}>
                          {mode === "reject" ? "Reject request" : "Revoke permission"}
                        </button>
                        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setMode(null)}>
                          Cancel
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              )}

              <Section title="History of this partner and product">
                <Table
                  caption="Requests"
                  head={["Requested", "Status", "Decided", "By", "Reason"]}
                  isEmpty={d.history.length === 0}
                  empty="No requests."
                >
                  {d.history.map((h) => (
                    <tr key={h.id}>
                      <th scope="row">{when(h.requested_at)}</th>
                      <td>
                        <AccessBadge status={h.status} />
                      </td>
                      <td>{h.status === "revoked" ? when(h.revoked_at) : when(h.decided_at)}</td>
                      <td>{(h.status === "revoked" ? h.revoked_by : h.decided_by) ?? "—"}</td>
                      <td>{(h.status === "revoked" ? h.revoke_reason : h.decision_reason) || "—"}</td>
                    </tr>
                  ))}
                </Table>
                <Table caption="Promotion grants" head={["Granted", "By", "Status", "Revoked", "By"]} isEmpty={d.grants.length === 0} empty="No grant yet.">
                  {d.grants.map((g) => (
                    <tr key={g.id}>
                      <th scope="row">{when(g.granted_at)}</th>
                      <td>{g.granted_by}</td>
                      <td>
                        <Status value={g.status} />
                      </td>
                      <td>{when(g.revoked_at)}</td>
                      <td>{g.revoked_by ?? "—"}</td>
                    </tr>
                  ))}
                </Table>
              </Section>
            </div>
          );
        }}
      </LoadGate>
    </section>
  );
}
