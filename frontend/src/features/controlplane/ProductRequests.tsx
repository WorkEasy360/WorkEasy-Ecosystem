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
import type { RequestKind } from "@/lib/api/partnerPanel";
import { can, useAdmin } from "./ControlShell";
import { Section } from "./PartnerSection";
import { FormPopup } from "./Modal";
import { LoadGate, Status, Table, useAction, useLoad, when } from "./ui";
import {
  Button,
  DataTable,
  EmptyState,
  FilterBar,
  IconButton,
  Page,
  PageHeader,
  SearchInput,
  Select,
  StatusChips,
  formatDate,
  formatNumber,
  kit,
  type Column,
} from "./kit";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partners → Product Requests (ADR-055): affiliates asking to promote a Store
 * product and resellers asking to sell one, and the decision.
 *
 * Approving grants the partner that one product's promotion permission (the
 * grant "Products this partner may promote" makes by hand) or its sale
 * permission (the product grant "Products this partner may sell" makes) - and
 * nothing else. Rejecting needs a reason the partner reads. Revoking stops new links at
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

const KINDS: { key: "" | RequestKind; label: string }[] = [
  { key: "", label: "All types" },
  { key: "promote", label: "Affiliate - promote" },
  { key: "sell", label: "Reseller - sell" },
];

/** The words that differ by the kind of request. */
const KIND_WORDS: Record<RequestKind, { type: string; verb: string; who: string; capability: string; channel: string }> = {
  promote: { type: "Promote", verb: "promote", who: "affiliate", capability: "Referral capability", channel: "affiliates" },
  sell: { type: "Sell", verb: "sell", who: "reseller", capability: "Selling capability", channel: "this partner's channel" },
};

const CHANNEL_NAMES: Record<string, string> = { affiliate: "affiliates", reseller: "resellers", white_label: "white-label partners" };

const PAGE = 50;

export function ProductRequestsConsole() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/admin/partners/product-requests";
  const status = (params?.get("status") as PromotionRequestStatus | "all" | null) ?? "pending";
  const q = params?.get("q") ?? "";
  const kind = (params?.get("kind") as RequestKind | null) ?? "";
  const offset = Number(params?.get("offset") ?? 0) || 0;
  const open = params?.get("request") ?? null;
  const [load, reload] = useLoad(() => adminProgrammeApi.promotionRequests({ status, kind: kind || undefined, q: q || undefined, offset }),
    [status, kind, q, offset],
  );

  const go = (next: Record<string, string | null>) => {
    const query = new URLSearchParams(params?.toString() ?? "");
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === "") query.delete(k);
      else query.set(k, v);
    }
    const text = query.toString();
    router.push(text ? `${pathname}?${text}` : pathname);
  };

  const counts = load.status === "ready" ? load.data.counts : null;
  const all = counts ? counts.pending + counts.approved + counts.rejected + counts.revoked : null;
  const requestHref = (id: string) => {
    const query = new URLSearchParams(params?.toString() ?? "");
    query.set("request", id);
    return `${pathname}?${query.toString()}`;
  };

  return (
    <Page>
      <PageHeader
        title="Product requests"
        subtitle="Affiliates asking to promote a Store product, and resellers asking to sell one."
        info="Approving lets that partner create links, codes and campaigns for that one product, or sell it to customers; it gives no other access."
      />
      <StatusChips
        label="Request status"
        value={status}
        onChange={(next) => go({ status: next, offset: null, request: null })}
        chips={TABS.map((t) => ({ value: t.key, label: t.label, count: t.key === "all" ? all : (counts?.[t.key] ?? null) }))}
      />
      <FilterBar label="Filter requests" onClear={q || kind ? () => go({ q: null, kind: null, offset: null }) : undefined}>
        <SearchInput label="Search by partner or product" value={q} onSearch={(text) => go({ q: text.slice(0, 80) || null, offset: null })} />
        <Select label="Type" value={kind} options={KINDS.map((k) => ({ value: k.key, label: k.label }))} onChange={(v) => go({ kind: v || null, offset: null })} />
      </FilterBar>
      {open && <RequestReview requestId={open} onClose={() => go({ request: null })} onDecided={reload} />}
      <LoadGate load={load} what="product requests">
        {(d) => (
          <Queue data={d} status={status} rowHref={requestHref} onOpen={(id) => go({ request: id })} onPage={(o) => go({ offset: String(o) })} />
        )}
      </LoadGate>
    </Page>
  );
}

type PromotionRequestRow = PromotionRequestQueue["items"][number];

function Queue({
  data,
  status,
  rowHref,
  onOpen,
  onPage,
}: {
  data: PromotionRequestQueue;
  status: string;
  rowHref: (id: string) => string;
  onOpen: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  const columns: Column<PromotionRequestRow>[] = [
    {
      key: "partner",
      header: "Partner",
      value: (r) => r.partner_name,
      render: (r) => (
        <span className={kit.cellStack}>
          <Link href={`/admin/partners/${r.partner_id}`} className={kit.cellLink}>
            {r.partner_name}
          </Link>
          <span className={kit.cellSub}>{r.partner_code}</span>
        </span>
      ),
    },
    { key: "type", header: "Type", value: (r) => r.kind, render: (r) => KIND_WORDS[r.kind].type },
    { key: "product", header: "Product", value: (r) => r.product_name, render: (r) => r.product_name },
    { key: "channel", header: "Channel", value: (r) => r.channel_label, render: (r) => r.channel_label },
    { key: "requested", header: "Requested", value: (r) => r.requested_at, render: (r) => formatDate(r.requested_at) },
    { key: "status", header: "Status", value: (r) => r.status, render: (r) => <AccessBadge status={r.status} /> },
    {
      key: "review",
      header: "Action",
      align: "right",
      render: (r) => (
        <Button size="sm" onClick={() => onOpen(r.id)}>
          Review<span className="w360-visually-hidden"> the request from {r.partner_name} for {r.product_name}</span>
        </Button>
      ),
    },
  ];
  const last = data.offset + data.items.length;
  return (
    <>
      <DataTable
        caption={`Product requests: ${status}`}
        columns={columns}
        rows={data.items}
        rowKey={(r) => r.id}
        rowHref={(r) => rowHref(r.id)}
        pageSize={0}
        empty={
          <EmptyState
            icon="store"
            title={status === "pending" ? "No request is waiting for a decision." : "No requests."}
            hint="Requests partners make from the Store appear here."
          />
        }
      />
      {data.total > data.limit && (
        <div className={kit.tableFoot}>
          <span>
            Showing {formatNumber(data.offset + 1)}–{formatNumber(Math.min(last, data.total))} of {formatNumber(data.total)}
          </span>
          <span className={kit.pager}>
            <IconButton icon="chevronLeft" label="Previous" size="sm" variant="secondary" disabled={data.offset === 0} onClick={() => onPage(Math.max(0, data.offset - PAGE))} />
            <IconButton icon="chevronRight" label="Next" size="sm" variant="secondary" disabled={last >= data.total} onClick={() => onPage(data.offset + PAGE)} />
          </span>
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
  // A rejection or revocation is made in a popup; its outcome is the review's own.
  const announce = (message: string) => void act(async () => {}, message);
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => heading.current?.focus(), [requestId]);

  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={`${headingId}-h`}>
      <div className={local.headerRow}>
        <h2 id={`${headingId}-h`} ref={heading} tabIndex={-1} className={styles.cardTitle}>
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
          const words = KIND_WORDS[r.kind];
          const holding = r.kind === "sell" ? d.partner.sells : d.partner.promotes;
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
                    <dt>{words.capability}</dt>
                    <dd>
                      {(r.kind === "sell" ? d.partner.selling_capability : d.partner.referral_capability)
                        ? "Held"
                        : "Not held - approval will be refused"}
                    </dd>
                    <dt>Already {r.kind === "sell" ? "sells" : "promotes"}</dt>
                    <dd>{holding.length ? holding.join(", ") : "Nothing yet"}</dd>
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
                    <dt>Open to {words.channel}</dt>
                    <dd>{d.product.eligible ? `Yes - on the Store for ${CHANNEL_NAMES[d.partner.channel] ?? d.partner.channel}` : "No - approval will be refused"}</dd>
                  </dl>
                </Section>
              </div>
              <Section title={`What the ${words.who} wrote`}>
                <dl className={styles.factsGrid}>
                  <dt>Status</dt>
                  <dd>
                    <AccessBadge status={r.status} />
                  </dd>
                  <dt>Asks to</dt>
                  <dd>
                    {words.verb} {d.product.name}
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
                <div className={styles.row}>
                  {r.status === "pending" && (
                    <>
                      <button
                        type="button"
                        className="w360-btn w360-btn-primary"
                        disabled={busy}
                        onClick={() => void act(() => adminProgrammeApi.decidePromotionRequest(r.id, "approve"), `Approved: ${d.partner.name} may ${words.verb} ${d.product.name}.`)}
                      >
                        Approve
                      </button>
                      <ReasonPopup
                        action="reject"
                        label="Reject"
                        reasonLabel={`Reason (the ${words.who} reads it)`}
                        disabled={busy}
                        onSubmit={(reason) => adminProgrammeApi.decidePromotionRequest(r.id, "reject", reason)}
                        onDone={() => announce(`Request rejected. The ${words.who} sees your reason.`)}
                      />
                    </>
                  )}
                  {r.status === "approved" && (
                    <ReasonPopup
                      action="revoke"
                      label="Revoke permission"
                      reasonLabel={`Reason (optional, the ${words.who} reads it)`}
                      disabled={busy}
                      onSubmit={(reason) => adminProgrammeApi.decidePromotionRequest(r.id, "revoke", reason)}
                      onDone={() =>
                        announce(
                          r.kind === "sell"
                            ? "Permission revoked. New sales stop now; existing customers keep their subscriptions."
                            : "Permission revoked. New links stop now; earned commission is kept.",
                        )
                      }
                    />
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

/** Reject or revoke, with the reason the partner reads, in a small popup. A
 * refusal stays in the popup with the reason kept; a rejection needs one. */
function ReasonPopup({
  action,
  label,
  reasonLabel,
  disabled,
  onSubmit,
  onDone,
}: {
  action: "reject" | "revoke";
  label: string;
  reasonLabel: string;
  disabled: boolean;
  onSubmit: (reason: string | undefined) => Promise<unknown>;
  onDone: () => void;
}) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const reject = action === "reject";
  return (
    <FormPopup
      label={label}
      buttonClassName={reject ? "w360-btn w360-btn-secondary" : "w360-btn w360-btn-danger"}
      title={reject ? "Reject the request" : "Revoke the permission"}
      size="sm"
      submitLabel={reject ? "Reject request" : "Revoke permission"}
      danger={!reject}
      canSubmit={!reject || reason.trim().length >= 3}
      disabled={disabled}
      onOpen={() => setReason("")}
      onSubmit={async () => {
        await onSubmit(reason.trim() || undefined);
      }}
      onDone={onDone}
    >
      <div className={styles.field}>
        <label className="w360-label" htmlFor={reasonId}>
          {reasonLabel}
        </label>
        <textarea
          id={reasonId}
          className="w360-input"
          rows={3}
          maxLength={500}
          required={reject}
          minLength={reject ? 3 : undefined}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
    </FormPopup>
  );
}
