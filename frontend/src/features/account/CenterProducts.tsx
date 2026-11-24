"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { accountCenterApi, type CenterOrganization, type CenterProducts as CenterProductsData } from "@/lib/api/console";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { Badge, formatDate } from "@/features/controlplane/kit";
import { Panel } from "@/features/console/consoleUi";
import { productRows, type ProductRow } from "@/features/console/MyProductsScreen";
import { SETUP_STATUS_WORDS } from "@/features/console/CustomerDesk";
import { launchDenialMessage } from "@/features/launcher/launchCopy";
import type { CenterExplore } from "@/lib/api/console";
import { CenterShell } from "./CenterShell";
import { assignmentLabel } from "./centerCopy";
import styles from "./Account.module.css";

type Row = ProductRow<CenterExplore, NonNullable<CenterOrganization["setup"]>[number]>;

/** What one organization holds, classified by My Products' own `productRows` from the
 * same directory, Explore and setup facts User Admin reads. */
export function organizationRows(org: CenterOrganization): Row[] {
  return productRows(org.apps, org.explore ?? null, org.setup ?? null);
}

export type CenterFilter = "" | "purchased" | "trial" | "assigned" | "expired" | "setup";

const FILTERS: { value: CenterFilter; label: string }[] = [
  { value: "", label: "All" },
  { value: "purchased", label: "Purchased" },
  { value: "trial", label: "Trial" },
  { value: "assigned", label: "Assigned to me" },
  { value: "expired", label: "Expired" },
  { value: "setup", label: "Pending setup" },
];

export function rowMatches(row: Row, filter: CenterFilter): boolean {
  switch (filter) {
    case "purchased":
      return row.held && !row.trial;
    case "trial":
      return row.trial;
    case "assigned":
      return row.app.state === "active";
    case "expired":
      return row.expired;
    case "setup":
      return row.pendingSetup;
    default:
      return true;
  }
}

/** Opens a product in one organization: the launcher's own decision and the
 * server-built launch URL (with `organization=`) - fetched on click, never rendered. */
export function OpenProductButton({
  organization,
  productCode,
  productName,
}: {
  organization: Pick<CenterOrganization, "organization_id" | "name">;
  productCode: string;
  productName: string;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className={styles.open}>
      <button
        type="button"
        className="w360-btn w360-btn-primary"
        disabled={pending}
        aria-label={`Open ${productName} in ${organization.name}`}
        onClick={() => {
          setPending(true);
          setError(null);
          accountCenterApi
            .launch(organization.organization_id, productCode)
            .then(({ redirect_url }) => window.location.assign(redirect_url))
            .catch((err: unknown) => {
              const body =
                err instanceof ApiError && typeof err.body === "object" && err.body !== null
                  ? (err.body as { state?: string; code?: string })
                  : undefined;
              setError(launchDenialMessage(body?.state ?? body?.code));
              setPending(false);
            });
        }}
      >
        {pending ? "Opening…" : "Open"}
      </button>
      {error && <span role="alert">{error}</span>}
    </span>
  );
}

function RowChips({ row }: { row: Row }) {
  const assigned = assignmentLabel(row.app);
  return (
    <span className={styles.chips}>
      {row.held && !row.trial && <Badge tone="success">Purchased</Badge>}
      {row.trial && (
        <Badge tone="info">{row.app.trial ? `Trial - ends ${formatDate(row.app.trial.ends_at)}` : "Trial"}</Badge>
      )}
      {row.expired && <Badge tone="danger">Expired</Badge>}
      {row.held && <Badge tone={assigned.tone}>{assigned.label}</Badge>}
      {row.setup && row.pendingSetup && <Badge tone="warning">{`Setup: ${SETUP_STATUS_WORDS[row.setup.status]}`}</Badge>}
    </span>
  );
}

function OrganizationProducts({
  org,
  filter,
  data,
}: {
  org: CenterOrganization;
  filter: CenterFilter;
  data: CenterProductsData;
}) {
  const rows = organizationRows(org).filter((r) => rowMatches(r, filter));
  const store = data.store_origin ? `${data.store_origin}/store` : `${data.app_origin}/o/${encodeURIComponent(org.slug)}/store`;
  return (
    <Panel
      title={org.name}
      actions={
        <span className={styles.links}>
          <a href={store}>Explore products in the Store</a>
          {org.may_manage_billing && (
            <a href={`${data.app_origin}/o/${encodeURIComponent(org.slug)}/settings/billing`}>Billing in User Admin</a>
          )}
        </span>
      }
    >
      {org.restricted ? (
        <p role="note">Your organization does not allow this session right now, so its products cannot be shown or opened here.</p>
      ) : rows.length === 0 ? (
        <p>{filter ? "Nothing here matches this filter." : "This organization has no products yet."}</p>
      ) : (
        <ul className={styles.productList}>
          {rows.map((row) => (
            <li key={row.app.product_code}>
              <span className={styles.productName}>
                <strong>{row.app.display_name}</strong>
                <RowChips row={row} />
              </span>
              {row.app.can_launch ? (
                <OpenProductButton organization={org} productCode={row.app.product_code} productName={row.app.display_name} />
              ) : row.app.state === "no_seat" ? (
                <span className={styles.muted}>Ask an administrator of {org.name} for a seat.</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** My Products: every product of every organization you belong to. */
export function CenterProducts() {
  const [products] = useLoad(() => accountCenterApi.products(), []);
  const [filter, setFilter] = useState<CenterFilter>("");
  return (
    <CenterShell
      current="products"
      title="My Products"
      lead="Every product your organizations hold, bought, on trial, expired or still being set up - and whether it is assigned to you. Open one to sign in with this account."
    >
      <div role="group" aria-label="Show" className={styles.filters}>
        {FILTERS.map((f) => (
          <button
            key={f.value || "all"}
            type="button"
            className="w360-btn w360-btn-secondary"
            aria-pressed={filter === f.value}
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>
      <LoadGate load={products} what="your products">
        {(d) =>
          d.organizations.length === 0 ? (
            <p>You are not a member of any organization yet, so there are no products to show.</p>
          ) : (
            <>
              {d.organizations.map((o) => (
                <OrganizationProducts key={o.organization_id} org={o} filter={filter} data={d} />
              ))}
            </>
          )
        }
      </LoadGate>
    </CenterShell>
  );
}
