"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { partnerConsoleApi, type WhiteLabelRow } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels } from "./PartnerConsole";
import { MoreButton, Status, Table, useLoad, usePaged, when } from "./ui";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partners → White-label (Phase 10d): a filtered view of partner accounts,
 * not a kind of account. A partner is listed while it holds branding or domain
 * capability; the brand and the domain shown are what those capabilities were
 * used to set up.
 */

const DOMAIN_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "Any" },
  { value: "awaiting", label: "Awaiting verification" },
  { value: "requested", label: "Requested" },
  { value: "verification_pending", label: "Verification pending" },
  { value: "verified", label: "Verified (not active)" },
  { value: "active", label: "Active" },
  { value: "failed", label: "Failed" },
  { value: "disabled", label: "Disabled" },
  { value: "none", label: "No domain" },
];

export function WhiteLabelList() {
  const { permissions } = useAdmin();
  const params = useSearchParams();
  const searchId = useId();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [query, setQuery] = useState(params.get("q") ?? "");
  const initial = params.get("domain_status") ?? "";
  const [domainStatus, setDomainStatus] = useState(DOMAIN_FILTERS.some((f) => f.value === initial) ? initial : "");
  const [brand, setBrand] = useState(params.get("brand") === "configured" || params.get("brand") === "none" ? (params.get("brand") as string) : "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const paged = usePaged(
    (cursor) =>
      partnerConsoleApi.whiteLabel({
        q: query || undefined,
        status: status || undefined,
        domain_status: domainStatus || undefined,
        brand: brand || undefined,
        cursor,
      }),
    [query, status, domainStatus, brand],
  );
  // Partners that could be given white-label: any active partner, chosen here to open its setup.
  const [candidates] = useLoad(
    () => (manage ? partnerConsoleApi.resellers({ scope: "all", status: "active" }) : Promise.resolve(null)),
    [manage],
  );
  const [pick, setPick] = useState("");

  return (
    <div className={styles.stack}>
      <h1>White-label</h1>
      <p className={styles.lead}>
        Partners that can set a brand or a domain. White-label is a capability a partner holds, alongside any other; enabling it does not
        create another account.
      </p>
      <form
        className={local.filters}
        role="search"
        aria-label="Filter white-label partners"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <div>
          <label className="w360-label" htmlFor={searchId}>
            Search partner, brand or domain
          </label>
          <input id={searchId} className="w360-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} />
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-dom`}>
            Domain status
          </label>
          <select id={`${searchId}-dom`} className="w360-input" value={domainStatus} onChange={(e) => setDomainStatus(e.target.value)}>
            {DOMAIN_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-brand`}>
            Brand
          </label>
          <select id={`${searchId}-brand`} className="w360-input" value={brand} onChange={(e) => setBrand(e.target.value)}>
            <option value="">Any</option>
            <option value="configured">Configured</option>
            <option value="none">Not configured</option>
          </select>
        </div>
        <div>
          <label className="w360-label" htmlFor={`${searchId}-status`}>
            Partner status
          </label>
          <select id={`${searchId}-status`} className="w360-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any</option>
            <option value="draft">Draft</option>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="terminated">Terminated</option>
          </select>
        </div>
        <button type="submit" className="w360-btn w360-btn-secondary">
          Search
        </button>
      </form>

      {paged.state === "loading" && <p aria-live="polite">Loading partners…</p>}
      {paged.state === "forbidden" && <p role="alert">You don&apos;t have access to partners.</p>}
      {paged.state === "error" && <p role="alert">Couldn&apos;t load white-label partners.</p>}
      {paged.state === "ready" && (
        <>
          <Table
            caption="White-label partners"
            head={["Brand", "Partner", "Brand status", "Domain", "Domain status", "Customers", "Updated", ""]}
            isEmpty={paged.rows.length === 0}
            empty="No partner matches. Enable white-label for a partner below."
          >
            {paged.rows.map((r) => (
              <WhiteLabelTableRow key={r.id} row={r} manage={manage} />
            ))}
          </Table>
          <MoreButton hasMore={paged.hasMore} onMore={paged.more} />
        </>
      )}

      {manage && candidates.status === "ready" && candidates.data && (
        <section className={`w360-card ${styles.card}`} aria-labelledby="wl-enable">
          <h2 id="wl-enable" className={styles.cardTitle}>
            Set up white-label for a partner
          </h2>
          <div className={local.filters}>
            <div>
              <label className="w360-label" htmlFor={`${searchId}-pick`}>
                Partner
              </label>
              <select id={`${searchId}-pick`} className="w360-input" value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Choose…</option>
                {candidates.data.results.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.display_name}
                  </option>
                ))}
              </select>
            </div>
            {pick ? (
              <Link className="w360-btn w360-btn-primary" href={`/admin/partners/white-label/${pick}`}>
                Set up white-label
              </Link>
            ) : (
              <span className={styles.muted}>Choose a partner to continue.</span>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function WhiteLabelTableRow({ row: r, manage }: { row: WhiteLabelRow; manage: boolean }) {
  return (
    <tr>
      <th scope="row">
        {r.brand.configured ? r.brand.name : <span className={styles.muted}>No brand yet</span>}
        {r.brand.has_logo && <div className={styles.muted}>Logo uploaded</div>}
      </th>
      <td>
        <Link href={`/admin/partners/${r.id}`}>{r.display_name}</Link>
        <div>
          <Status value={r.status} />
        </div>
        <ExperienceLabels labels={r.experience} />
      </td>
      <td>{r.brand.configured ? "Configured" : "Not configured"}</td>
      <td className={styles.mono}>{r.domain ? r.domain.hostname : "—"}</td>
      <td>{r.domain ? <Status value={r.domain.status} /> : <span className={styles.muted}>No domain</span>}</td>
      <td className={styles.num}>{r.customers}</td>
      <td>{when(r.updated_at)}</td>
      <td>
        {manage && (
          <Link href={`/admin/partners/white-label/${r.id}`} aria-label={`Set up white-label for ${r.display_name}`}>
            Set up
          </Link>
        )}
      </td>
    </tr>
  );
}
