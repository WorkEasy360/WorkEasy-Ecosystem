"use client";

import { useId, useMemo, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { reportsApi, type ReportColumn, type ReportQuery } from "@/lib/api/adminPanel";
import { adminApi } from "@/lib/api/controlPlane";
import { can, useAdmin } from "./ControlShell";
import { DateRange, OffsetPager, dayBefore } from "./AdminListParts";
import { LoadGate, useLoad } from "./ui";
import local from "./AdminListParts.module.css";
import {
  Alert,
  DataTable,
  EmptyState,
  FilterBar,
  Page,
  PageHeader,
  SearchInput,
  Select,
  StatusBadge,
  Trunc,
  EMPTY,
  buttonClass,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  productName,
  sentence,
  type Column,
} from "./kit";

/**
 * Admin → Reports (2026-11-09): one screen for the eight standard reports. The server
 * says what each report's columns are and how to write them. Money is shown per currency
 * as the row carries it and is never added up here; AI credits and units are whole numbers.
 * Every CSV export is recorded in the audit log by the server.
 */

export type ReportKind = "sales" | "payments" | "renewals" | "wallet-movements" | "usage" | "partner-conversions" | "commissions" | "payouts";

/** What only this screen knows about a report. `product` is true where the server filters
 * by product: a report it does not apply to does not offer a filter that would do nothing. */
const REPORTS: Record<ReportKind, { label: string; search: string; product: boolean; earnings?: boolean }> = {
  sales: { label: "Sales", search: "Search by invoice number", product: true },
  payments: { label: "Payments", search: "Search by invoice number", product: true },
  renewals: { label: "Subscription Renewals", search: "Search by customer", product: true },
  "wallet-movements": { label: "Wallet Movements", search: "Search by reason or source", product: false },
  usage: { label: "Usage by Product", search: "Search by product or meter", product: true },
  "partner-conversions": { label: "Partner Conversions", search: "Search by partner", product: false },
  commissions: { label: "Commissions", search: "Search by partner or reason", product: true, earnings: true },
  payouts: { label: "Payouts", search: "Search by partner or reference", product: false, earnings: true },
};

const LIMIT = 25;
const CATALOG_VIEW = "platform.staff_catalog.view";

/** Columns holding a system key, written for a person. Names and references stay as they are. */
const KEY_COLUMNS = new Set(["provider", "billing_interval", "renewal_policy", "earning", "reason_code", "meter"]);

type Row = Record<string, unknown>;

function reportCell(column: ReportColumn, row: Row): React.ReactNode {
  const value = row[column.key];
  if (value === null || value === undefined || value === "") return EMPTY;
  const currency = typeof row.currency === "string" ? row.currency : "";
  switch (column.kind) {
    case "money":
      return formatMoney(Number(value), currency);
    case "amount":
      // Minor units of the row's currency; without one, whole credits or units.
      return currency ? formatMoney(Number(value), currency) : formatNumber(Number(value));
    case "number":
      return formatNumber(Number(value));
    case "date":
      return formatDateTime(String(value));
    case "status":
      return <StatusBadge value={String(value)} />;
    default:
      return <Trunc max="lg">{KEY_COLUMNS.has(column.key) ? sentence(String(value)) : String(value)}</Trunc>;
  }
}

/** A whole day from the server, on that day wherever the browser is. */
const dayText = (day: string) => formatDate(`${day}T12:00:00`);

export function ReportScreen({ kind }: { kind: ReportKind }) {
  const meta = REPORTS[kind];
  const { permissions } = useAdmin();
  const [from, setFrom] = useState(() => dayBefore(29));
  const [to, setTo] = useState(() => dayBefore(0));
  const [product, setProduct] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const exportWhy = useId();

  // The catalog needs its own grant. Without it the report still runs, unfiltered by product.
  const offerProducts = meta.product && can(permissions, CATALOG_VIEW);
  const [products] = useLoad(() => (offerProducts ? adminApi.products() : Promise.resolve({ results: [] })), [offerProducts]);

  const filters: ReportQuery = { from: from || undefined, to: to || undefined, product: product || undefined, q: query || undefined };
  const [load] = useLoad(() => reportsApi.run(kind, { ...filters, limit: LIMIT, offset }), [kind, from, to, product, query, offset]);

  /** A new filter starts again on the first page. */
  const first = (set: (value: string) => void) => (value: string) => {
    set(value);
    setOffset(0);
  };

  const data = load.status === "ready" ? load.data : null;
  const rows = useMemo(() => (data ? data.results.map((cells, i) => ({ key: String(data.offset + i), cells })) : []), [data]);
  const columns: Column<{ key: string; cells: Row }>[] = useMemo(
    () =>
      (data?.columns ?? []).map((column) => ({
        key: column.key,
        header: column.label,
        align: column.kind === "money" || column.kind === "amount" || column.kind === "number" ? ("right" as const) : undefined,
        render: (row: { cells: Row }) => reportCell(column, row.cells),
      })),
    [data],
  );
  const options = products.status === "ready" ? products.data.results : [];

  return (
    <Page>
      <PageHeader
        title={data?.label ?? meta.label}
        subtitle={data?.description}
        meta={data ? [`Period: ${dayText(data.from)} to ${dayText(data.to)}`, `${formatNumber(data.total)} ${data.total === 1 ? "row" : "rows"}`] : undefined}
      />
      {meta.earnings && (
        <Alert tone="info" title="Earnings, not wallet balances">
          These are records of what partners earned and were paid. They are not wallet balances and never appear in a wallet.
        </Alert>
      )}
      <FilterBar
        label="Filter the report"
        end={
          data && data.total > 0 ? (
            // A plain link: the server sends the file, and records the export in the audit log.
            <a className={buttonClass("secondary")} href={reportsApi.csvUrl(kind, filters)}>
              <Icon name="download" size={16} />
              <span>Export CSV</span>
            </a>
          ) : (
            // Always on the page (2026-11-10: an inspection of an empty period found
            // no export at all). Disabled with the reason beside it, so the words
            // are readable even though a disabled button cannot be focused.
            <span className={local.exportOff}>
              <button type="button" className={buttonClass("secondary")} disabled aria-describedby={exportWhy}>
                <Icon name="download" size={16} />
                <span>Export CSV</span>
              </button>
              <span id={exportWhy} className={local.exportWhy}>
                {load.status === "loading"
                  ? "Loading the report…"
                  : load.status === "forbidden"
                    ? "You do not have access to this report."
                    : load.status === "error"
                      ? "The report could not be loaded."
                      : "Nothing to export in this period."}
              </span>
            </span>
          )
        }
      >
        <SearchInput label={meta.search} value={query} onSearch={first(setQuery)} />
        <DateRange from={from} to={to} onFrom={first(setFrom)} onTo={first(setTo)} />
        {options.length > 0 && (
          <Select
            label="Product"
            prefix="Product:"
            value={product}
            onChange={first(setProduct)}
            options={[{ value: "", label: "All" }, ...options.map((p) => ({ value: p.code, label: productName(p.name) }))]}
          />
        )}
      </FilterBar>
      {data ? (
        <>
          <DataTable
            caption={data.label}
            columns={columns}
            rows={rows}
            rowKey={(row) => row.key}
            pageSize={0}
            hideEmptyColumns={false}
            empty={<EmptyState icon="chart" title="Nothing in this period" hint="Try a wider date range or clear the search." />}
          />
          <OffsetPager total={data.total} limit={data.limit} offset={data.offset} shown={rows.length} onOffset={setOffset} />
        </>
      ) : (
        <LoadGate load={load} what="this report">
          {() => null}
        </LoadGate>
      )}
    </Page>
  );
}
