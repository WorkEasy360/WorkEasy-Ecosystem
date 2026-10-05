"use client";

import { useEffect, useRef, useState } from "react";
import {
  adminConsoleApi,
  INTERVAL_LABELS,
  type BundleRow,
  type CatalogOverview,
  type CatalogPrice,
  type PlanRow,
} from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, Table, money, useAction, useLoad } from "./ui";
import {
  DEFAULT_INTERVALS,
  NOUN,
  STATUS_BADGE,
  STATUS_LABEL,
  statusPath,
  uiStatusOf,
  type BuilderKind,
  type UiStatus,
} from "./catalogUi";
import { PlanBuilder, type BuilderOutcome } from "./PlanBuilder";
import { DescriptionField, ErrorSummary, ProgressList, Segmented, Tabs, TextField, useStepRunner, type StepDef } from "./widgets";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * Super Admin → Plans & add-ons (Phase 8 extension). Three views over the
 * catalog, in the agency-panel vocabulary:
 *
 * - **Plans**: bundles - the suites a customer buys, made of product packages;
 * - **Product packages**: a product's standard plans;
 * - **Add-ons**: plans bought on top of a subscription.
 *
 * Every row is versioned: commercial terms (seats, features, trial, prices)
 * live on immutable published versions, so an edit is either presentation
 * ("Edit details") or a new version ("Publish new version").
 */

type Row = PlanRow | BundleRow;
type Panel = { mode: "new" } | { mode: "edit"; id: string } | { mode: "version"; id: string };

const TAB_TITLE: Record<BuilderKind, string> = {
  bundle: "Plans",
  package: "Product packages",
  addon: "Add-ons",
};

const TAB_LEAD: Record<BuilderKind, string> = {
  bundle: "A plan combines product packages into one subscription with its own price per billing cycle.",
  package: "A product package is one product's offer: included seats, features, trial and price per billing cycle.",
  addon: "An add-on is bought on top of an existing subscription to extend another product.",
};

function isPlanRow(row: Row): row is PlanRow {
  return "product" in row;
}

function rowsOf(overview: CatalogOverview, kind: BuilderKind): Row[] {
  if (kind === "bundle") return overview.bundles;
  return overview.plans.filter((p) => p.kind === (kind === "addon" ? "add_on" : "standard"));
}

export function PlansScreen() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_catalog.manage");
  const [load, reload] = useLoad(() => adminConsoleApi.catalogOverview(), []);
  const [tab, setTab] = useState<BuilderKind>("bundle");
  const [panel, setPanel] = useState<Panel | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const noticeRef = useRef<HTMLParagraphElement | null>(null);

  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);

  function close(outcome: BuilderOutcome) {
    setPanel(null);
    setNotice(outcome.message ?? null);
    if (outcome.saved || outcome.changed) reload();
  }

  return (
    <div className={cp.stack}>
      <div>
        <h1>Plans &amp; add-ons</h1>
        <p className={cp.lead}>
          Build what customers can buy. Terms are versioned: publishing a new version changes new sales only, and
          existing subscriptions keep the version they bought.
        </p>
      </div>
      {notice && (
        <p role="status" tabIndex={-1} ref={noticeRef}>
          {notice}
        </p>
      )}
      <LoadGate load={load} what="the catalog">
        {(overview) => (
          <div>
            <Tabs
              label="Catalog offers"
              items={(["bundle", "package", "addon"] as const).map((key) => ({ key, label: TAB_TITLE[key] }))}
              selected={tab}
              onSelect={(key) => {
                if (key === tab) return;
                if (panel) reload();
                setTab(key);
                setPanel(null);
              }}
            >
              <OfferTab
                kind={tab}
                overview={overview}
                manage={manage}
                panel={panel}
                open={(p) => {
                  setNotice(null);
                  setPanel(p);
                }}
                onClose={close}
              />
            </Tabs>
          </div>
        )}
      </LoadGate>
    </div>
  );
}

function OfferTab({
  kind,
  overview,
  manage,
  panel,
  open,
  onClose,
}: {
  kind: BuilderKind;
  overview: CatalogOverview;
  manage: boolean;
  panel: Panel | null;
  open: (panel: Panel) => void;
  onClose: (outcome: BuilderOutcome) => void;
}) {
  const rows = rowsOf(overview, kind);
  const noun = NOUN[kind];
  const row = panel && panel.mode !== "new" ? rows.find((r) => r.id === panel.id) : undefined;
  const productColumn = kind === "bundle" ? [] : [kind === "addon" ? "Product / add-on for" : "Product"];

  return (
    <>
      <div className={styles.header}>
        <div>
          <h2 className={styles.sectionTitle}>{TAB_TITLE[kind]}</h2>
          <p className={cp.muted}>{TAB_LEAD[kind]}</p>
        </div>
        {manage && (
          <button
            type="button"
            className="w360-btn w360-btn-primary"
            disabled={panel?.mode === "new"}
            onClick={() => open({ mode: "new" })}
          >
            New {noun}
          </button>
        )}
      </div>

      {panel?.mode === "new" && <PlanBuilder key={`new-${kind}`} kind={kind} overview={overview} onClose={onClose} />}
      {panel?.mode === "version" && row && (
        <PlanBuilder key={`version-${row.id}`} kind={kind} overview={overview} existing={row} onClose={onClose} />
      )}
      {panel?.mode === "edit" && row && <DetailsEditor key={`edit-${row.id}`} kind={kind} row={row} onClose={onClose} />}

      <Table
        caption={TAB_TITLE[kind]}
        head={["Name", ...productColumn, "Type", "Status", "Billing cycles (latest published version)", "Version", "Actions"]}
        isEmpty={rows.length === 0}
        empty={`No ${noun}s yet.`}
      >
        {rows.map((r) => (
          <OfferRow key={r.id} row={r} kind={kind} manage={manage} open={open} />
        ))}
      </Table>
    </>
  );
}

function OfferRow({
  row,
  kind,
  manage,
  open,
}: {
  row: Row;
  kind: BuilderKind;
  manage: boolean;
  open: (panel: Panel) => void;
}) {
  const status = uiStatusOf(row);
  const versions: { version: number; published_at: string | null; prices: CatalogPrice[] }[] = row.versions;
  const published = versions.find((v) => v.published_at);
  const latest = versions[0];
  return (
    <tr>
      <th scope="row">
        {row.name}
        {row.caption && <div className={cp.muted}>{row.caption}</div>}
        <div className={`${cp.mono} ${cp.muted}`}>{row.code}</div>
      </th>
      {kind !== "bundle" && isPlanRow(row) && (
        <td>
          {row.product.name}
          {row.addon_for && <div className={cp.muted}>extends {row.addon_for.code}</div>}
        </td>
      )}
      <td>
        <span className="w360-badge w360-badge-neutral">{row.billing_type === "free_trial" ? "Free trial" : "Paid"}</span>
      </td>
      <td>
        <span className={STATUS_BADGE[status]}>{STATUS_LABEL[status]}</span>
      </td>
      <td>
        <CyclePrices prices={published?.prices} published={Boolean(published)} />
      </td>
      <td className={styles.nowrap}>
        {published ? `v${published.version}` : "—"}
        {latest && !latest.published_at && <div className={cp.muted}>draft v{latest.version}</div>}
      </td>
      <td>
        {manage && (
          <div className={styles.actions}>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              aria-label={`Edit details of ${row.name}`}
              onClick={() => open({ mode: "edit", id: row.id })}
            >
              Edit details
            </button>
            {status !== "retired" && (
              <button
                type="button"
                className="w360-btn w360-btn-secondary"
                aria-label={`Publish new version of ${row.name}`}
                onClick={() => open({ mode: "version", id: row.id })}
              >
                Publish new version
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function intervalRank(interval: string): number {
  const at = DEFAULT_INTERVALS.indexOf(interval);
  return at === -1 ? DEFAULT_INTERVALS.length : at;
}

export function CyclePrices({ prices, published }: { prices?: CatalogPrice[]; published: boolean }) {
  if (!published) return <span className={cp.muted}>Not published</span>;
  if (!prices || prices.length === 0) return <span className={cp.muted}>No prices</span>;
  const books = new Set(prices.map((p) => p.price_book_code)).size;
  const sorted = [...prices].sort((a, b) => intervalRank(a.interval) - intervalRank(b.interval));
  return (
    <ul className={styles.plainList}>
      {sorted.map((p) => (
        <li key={p.price_id}>
          {INTERVAL_LABELS[p.interval] ?? p.interval}: {p.amount_minor === null ? "tiered" : money(p.amount_minor, p.currency)}
          {books > 1 && <span className={cp.muted}> ({p.price_book_code})</span>}
        </li>
      ))}
    </ul>
  );
}

// --- edit details ------------------------------------------------------------------

/** Presentation only - title, caption, description, visibility and status -
 * plus the type while the row is still a draft. Terms need a new version. */
function DetailsEditor({ kind, row, onClose }: { kind: BuilderKind; row: Row; onClose: (outcome: BuilderOutcome) => void }) {
  const current = uiStatusOf(row);
  const [name, setName] = useState(row.name);
  const [caption, setCaption] = useState(row.caption);
  const [description, setDescription] = useState(row.description);
  const [billingType, setBillingType] = useState<"paid" | "free_trial">(row.billing_type);
  const [status, setStatus] = useState<UiStatus>(current === "retired" ? "deactivated" : current);
  const [errors, setErrors] = useState<string[]>([]);
  const runner = useStepRunner();
  const { act, busy, feedback } = useAction();
  const heading = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  const isDraft = row.status === "draft";
  const retired = current === "retired";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || runner.running) return;
    if (!name.trim()) {
      setErrors(["Enter a title."]);
      return;
    }
    setErrors([]);
    const isPublic = status === "public" ? true : status === "private" ? false : undefined;
    const typeChange = isDraft && billingType !== row.billing_type ? billingType : undefined;
    const body = { name: name.trim(), caption: caption.trim(), description, is_public: isPublic, billing_type: typeChange };
    const defs: StepDef[] = [
      {
        key: "details",
        label: "Save the title, caption, description and visibility",
        run: async () => {
          if (kind === "bundle") await adminConsoleApi.updateBundle(row.id, body);
          else await adminConsoleApi.updatePlan(row.id, body);
        },
      },
    ];
    if (!retired) {
      for (const target of statusPath(row.status, status)) {
        defs.push({
          key: `status:${target}`,
          label: target === "grandfathered" ? "Deactivate it (no new sales)" : "Put it on sale",
          run: async () => {
            if (kind === "bundle") await adminConsoleApi.setBundleStatus(row.id, target);
            else await adminConsoleApi.setPlanStatus(row.id, target);
          },
        });
      }
    }
    const message = `“${name.trim()}” updated.`;
    runner.prepare(defs);
    await act(async () => {
      await runner.runAll(defs);
      onClose({ saved: true, changed: true, message });
    }, message);
  }

  return (
    <section className={`w360-card ${cp.card}`} aria-labelledby={`edit-${row.id}`}>
      <form className={styles.builder} onSubmit={submit} noValidate>
        <h3 id={`edit-${row.id}`} ref={heading} tabIndex={-1} className={styles.panelTitle}>
          Edit details of “{row.name}”
        </h3>
        <ErrorSummary errors={errors} />
        <TextField label="Title" value={name} onChange={setName} maxLength={128} />
        <TextField
          label="Caption (one line)"
          value={caption}
          onChange={(v) => setCaption(v.replace(/[\r\n]+/g, " "))}
          maxLength={160}
        />
        <DescriptionField value={description} onChange={setDescription} />
        {isDraft ? (
          <Segmented
            legend="Type"
            options={[
              { value: "free_trial", label: "Free Trial", disabled: kind === "addon" },
              { value: "paid", label: "Paid" },
            ]}
            value={billingType}
            onChange={setBillingType}
            hint="The type can change only while it is a draft."
          />
        ) : (
          <p>Type: {row.billing_type === "free_trial" ? "Free Trial" : "Paid"} (fixed once it has been on sale)</p>
        )}
        {retired ? (
          <p>Status: Retired. A retired offer can&apos;t be put back on sale.</p>
        ) : (
          <Segmented
            legend="Status"
            options={[
              { value: "draft", label: "Draft", disabled: !isDraft },
              { value: "private", label: "Private" },
              { value: "public", label: "Public" },
              { value: "deactivated", label: "Deactivated" },
            ]}
            value={status}
            onChange={setStatus}
            hint={
              kind === "bundle"
                ? "A plan can go on sale only once it has a published version."
                : "Deactivated stops new sales; existing customers keep what they bought."
            }
          />
        )}
        <div className={styles.actions}>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || runner.running}>
            {runner.started ? "Resume saving" : "Save details"}
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => onClose({ saved: false, changed: runner.started })}
          >
            Cancel
          </button>
        </div>
      </form>
      <ProgressList steps={runner.steps} title="Saving" />
      {feedback}
    </section>
  );
}
