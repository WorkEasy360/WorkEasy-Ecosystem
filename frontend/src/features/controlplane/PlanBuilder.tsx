"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import {
  adminConsoleApi,
  INTERVAL_LABELS,
  type BundleRow,
  type CatalogOverview,
  type FeatureRow,
  type PlanRow,
} from "@/lib/api/adminConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, money, useAction, useLoad, type Load } from "./ui";
import { toMajor, toMinor } from "./amounts";
import {
  CODE_PATTERN,
  NOUN,
  builderIntervals,
  defaultBook,
  directBooks,
  packageVersions,
  statusPath,
  type BuilderKind,
  type UiStatus,
} from "./catalogUi";
import {
  DescriptionField,
  ErrorSummary,
  ProgressList,
  Segmented,
  SelectField,
  Tabs,
  TextField,
  useStepRunner,
  type StepDef,
} from "./widgets";
import cp from "./ControlPlane.module.css";
import styles from "./AdminConsole.module.css";

/**
 * The plan builder (Phase 8 extension), in the shape of an agency panel's
 * "New plan" form. It has no write path of its own: Save calls the catalog
 * and pricing endpoints every other caller uses, in order -
 *
 *   create the plan (or bundle) → draft its first version (published unless
 *   the status is Draft) → publish one price per chosen billing cycle →
 *   move it to the chosen status.
 *
 * The whole sequence runs inside one `useAction`. It is resumable: ids created on the way are kept
 * and finished steps are skipped, so a retry never creates a second plan.
 *
 * "Publish new version" reuses the same form for an existing row: it drafts
 * and publishes the next version and its prices, and changes nothing else.
 */

type BillingType = "paid" | "free_trial";
type FeatureValue = boolean | string;

interface Inclusion {
  versionId: string;
  quantity: string;
}

interface Form {
  name: string;
  caption: string;
  description: string;
  billingType: BillingType;
  status: UiStatus;
  code: string;
  productId: string;
  addonForId: string;
  includedSeats: string;
  hardLimit: boolean;
  maxSeats: string;
  trialDays: string;
  cycles: Record<string, boolean>;
  prices: Record<string, string>;
  bookId: string;
  features: Record<string, FeatureValue>;
  inclusions: Record<string, Inclusion>;
}

export interface BuilderOutcome {
  saved: boolean;
  /** Something was created even though the whole sequence did not finish. */
  changed: boolean;
  message?: string;
}

function isPlanRow(row: PlanRow | BundleRow): row is PlanRow {
  return "product" in row;
}

function initialForm(overview: CatalogOverview, existing?: PlanRow | BundleRow): Form {
  const book = defaultBook(overview);
  const form: Form = {
    name: existing?.name ?? "",
    caption: existing?.caption ?? "",
    description: existing?.description ?? "",
    billingType: existing?.billing_type ?? "paid",
    status: "draft",
    code: existing?.code ?? "",
    productId: "",
    addonForId: "",
    includedSeats: "1",
    hardLimit: false,
    maxSeats: "",
    trialDays: "",
    cycles: {},
    prices: {},
    bookId: book?.id ?? "",
    features: {},
    inclusions: {},
  };
  if (!existing) return form;

  const bookIds = new Set(directBooks(overview).map((b) => b.id));
  const adopt = (
    latest: { trial_days: number } | undefined,
    prices: { price_book_id: string; interval: string; amount_minor: number | null; currency: string }[],
  ) => {
    if (latest?.trial_days) form.trialDays = String(latest.trial_days);
    // Start from the prices in force for the latest published version, in
    // one direct book (the default one when it has any).
    const priced = prices.filter((p) => bookIds.has(p.price_book_id));
    const chosen = priced.some((p) => p.price_book_id === form.bookId) ? form.bookId : (priced[0]?.price_book_id ?? form.bookId);
    form.bookId = chosen;
    for (const p of priced) {
      if (p.price_book_id !== chosen) continue;
      form.cycles[p.interval] = true;
      if (p.amount_minor !== null) form.prices[p.interval] = toMajor(p.amount_minor, p.currency);
    }
  };

  if (isPlanRow(existing)) {
    const latest = existing.versions[0];
    adopt(latest, existing.versions.find((v) => v.published_at)?.prices ?? []);
    form.productId = existing.product.id;
    form.addonForId = existing.addon_for?.id ?? "";
    if (latest) {
      form.includedSeats = String(latest.included_seats);
      form.hardLimit = latest.max_seats !== null && latest.max_seats === latest.included_seats;
      form.maxSeats = latest.max_seats !== null && !form.hardLimit ? String(latest.max_seats) : "";
      for (const [key, value] of Object.entries(latest.features)) {
        form.features[key] = typeof value === "boolean" ? value : String(value);
      }
    }
  } else {
    const latest = existing.versions[0];
    adopt(latest, existing.versions.find((v) => v.published_at)?.prices ?? []);
    for (const item of latest?.items ?? []) {
      const product = overview.products.find((p) => p.code === item.product_code);
      if (product) form.inclusions[product.id] = { versionId: item.plan_version_id, quantity: String(item.quantity) };
    }
  }
  return form;
}

function defaultFeatureValue(feature: FeatureRow): FeatureValue {
  if (feature.kind === "boolean") return Boolean(feature.default_value);
  if (feature.kind === "quota") return typeof feature.default_value === "number" ? String(feature.default_value) : "0";
  return typeof feature.default_value === "string" ? feature.default_value : (feature.enum_ordering?.[0] ?? "");
}

function featureValue(form: Form, feature: FeatureRow): FeatureValue {
  return feature.key in form.features ? form.features[feature.key] : defaultFeatureValue(feature);
}

const wholeNumber = (text: string): number | null => (/^\d{1,9}$/.test(text.trim()) ? Number(text.trim()) : null);

interface Checks {
  kind: BuilderKind;
  mode: "create" | "version";
  currency: string | null;
  features: FeatureRow[];
  canPrice: boolean;
  intervals: string[];
}

function checkedCycles(form: Form, intervals: string[]): string[] {
  return intervals.filter((i) => form.cycles[i]);
}

/** Everything that can be checked before any call is made. */
function validate(form: Form, x: Checks): Record<string, string> {
  const e: Record<string, string> = {};
  const publishes = x.mode === "version" || form.status !== "draft";

  if (x.mode === "create") {
    if (!form.name.trim()) e.name = "Enter a title.";
    if (!CODE_PATTERN.test(form.code.trim())) {
      e.code = "Enter a code of 2–63 lowercase letters, digits, “-” or “_”, starting with a letter or digit.";
    }
    if (x.kind !== "bundle" && !form.productId) e.productId = "Choose the product this package belongs to.";
    if (x.kind === "addon") {
      if (!form.addonForId) e.addonForId = "Choose the product this add-on extends.";
      else if (form.addonForId === form.productId) e.addonForId = "An add-on extends a different product from its own.";
    }
  }

  const trial = form.trialDays.trim();
  if (form.billingType === "free_trial") {
    const days = wholeNumber(trial);
    if (days === null || days < 1 || days > 365) e.trialDays = "A free trial needs 1 to 365 trial days.";
  } else if (x.kind !== "bundle" && trial) {
    const days = wholeNumber(trial);
    if (days === null || days > 365) e.trialDays = "Trial days must be a whole number from 0 to 365.";
  }

  if (x.kind === "bundle") {
    const chosen = Object.values(form.inclusions).filter((i) => i.versionId);
    if (chosen.length === 0) e.inclusions = "Include at least one product package.";
    else if (chosen.some((i) => (wholeNumber(i.quantity) ?? 0) < 1)) {
      e.inclusions = "Each included package needs a quantity of at least 1.";
    }
  } else {
    const seats = wholeNumber(form.includedSeats);
    if (seats === null || seats < 1) e.includedSeats = "Included seats must be a whole number of at least 1.";
    else if (!form.hardLimit && form.maxSeats.trim()) {
      const max = wholeNumber(form.maxSeats);
      if (max === null || max < seats) e.maxSeats = "Maximum seats must be a whole number no lower than the included seats.";
    }
    for (const feature of x.features) {
      if (feature.kind !== "quota") continue;
      const value = String(featureValue(form, feature)).trim();
      if (!/^-?\d{1,9}$/.test(value) || Number(value) < -1) {
        e[`feature:${feature.key}`] = `${feature.name}: enter a whole number, or -1 for unlimited.`;
      }
    }
  }

  if (publishes) {
    const cycles = checkedCycles(form, x.intervals);
    if (cycles.length === 0) {
      e.cycles = x.mode === "create" ? "Choose at least one billing cycle, or save it as a Draft." : "Choose at least one billing cycle.";
    }
    if (!x.currency) e.bookId = "Choose a price book to publish the prices in.";
    else if (form.billingType === "paid") {
      for (const cycle of cycles) {
        if (toMinor(form.prices[cycle] ?? "", x.currency) === null) {
          e[`price:${cycle}`] = `${INTERVAL_LABELS[cycle] ?? cycle}: enter a price like ${x.currency === "JPY" ? "1500" : "499.99"}.`;
        }
      }
    }
    if (!x.canPrice && cycles.length > 0) {
      e.canPrice =
        "Publishing prices needs the platform.staff_pricing.manage permission. Save it as a Draft, or ask a security administrator for that permission.";
    }
  }
  return e;
}

const SUBMIT_LABEL: Record<BuilderKind, string> = {
  bundle: "Create plan",
  package: "Create product package",
  addon: "Create add-on",
};

const STATUS_HINT: Record<UiStatus, string> = {
  draft: "Draft: saved but not on sale, and no prices are published yet.",
  private: "Private: on sale, but not listed in the public catalog (sold by staff or partners).",
  public: "Public: on sale and listed in the catalog.",
  deactivated: "Deactivated: no new sales; existing customers keep what they bought.",
};

export function PlanBuilder({
  kind,
  overview,
  existing,
  onClose,
}: {
  kind: BuilderKind;
  overview: CatalogOverview;
  /** Set for "Publish new version" of an existing row. */
  existing?: PlanRow | BundleRow;
  onClose: (outcome: BuilderOutcome) => void;
}) {
  const { permissions } = useAdmin();
  const canPrice = can(permissions, "platform.staff_pricing.manage");
  const mode: "create" | "version" = existing ? "version" : "create";
  const noun = NOUN[kind];
  const [form, setForm] = useState<Form>(() => initialForm(overview, existing));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  // What the save sequence has created so far: kept across retries so no
  // step runs twice.
  const created = useRef<{ id?: string; versionId?: string }>({ id: existing?.id });
  const runner = useStepRunner();
  const { act, busy, feedback } = useAction();
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);
  useEffect(() => {
    if (Object.keys(errors).length > 0) summary.current?.focus();
  }, [errors]);

  const productId = kind === "bundle" ? "" : form.productId;
  const [featuresLoad] = useLoad(
    () => (productId ? adminConsoleApi.productFeatures(productId) : Promise.resolve({ results: [] as FeatureRow[] })),
    [productId],
  );
  const features = featuresLoad.status === "ready" ? featuresLoad.data.results : [];
  const intervals = builderIntervals(overview);
  const books = directBooks(overview);
  const book = books.find((b) => b.id === form.bookId);
  const currency = book?.currency ?? null;
  const publishes = mode === "version" || form.status !== "draft";
  const freeTrial = form.billingType === "free_trial";
  const title = existing?.name ?? form.name.trim();

  function steps(): StepDef[] {
    // A snapshot: a retry after verification runs exactly what was submitted.
    const f = form;
    const cycles = checkedCycles(f, intervals);
    const trialDays = wholeNumber(f.trialDays) ?? 0;
    const defs: StepDef[] = [];

    if (mode === "create") {
      defs.push({
        key: "create",
        label: `Create the ${noun} “${f.name.trim()}”`,
        run: async () => {
          const common = {
            code: f.code.trim(),
            name: f.name.trim(),
            caption: f.caption.trim(),
            description: f.description,
            is_public: f.status === "public",
            billing_type: f.billingType,
          };
          const row =
            kind === "bundle"
              ? await adminConsoleApi.createBundle(common)
              : await adminConsoleApi.createPlan({
                  ...common,
                  product_id: f.productId,
                  kind: kind === "addon" ? "add_on" : "standard",
                  addon_for_product_id: kind === "addon" ? f.addonForId : undefined,
                });
          created.current.id = row.id;
        },
      });
    }

    defs.push({
      key: "version",
      label: publishes
        ? mode === "create"
          ? "Draft and publish version 1"
          : "Draft and publish the next version"
        : "Save version 1 as an unpublished draft",
      run: async () => {
        const id = created.current.id as string;
        if (kind === "bundle") {
          const items = Object.values(f.inclusions)
            .filter((i) => i.versionId)
            .map((i) => ({ plan_version_id: i.versionId, quantity: wholeNumber(i.quantity) ?? 1 }));
          const version = await adminConsoleApi.draftBundleVersion({
            bundle_id: id,
            items,
            trial_days: freeTrialOf(f) ? trialDays : 0,
            publish: publishes,
          });
          created.current.versionId = version.id;
        } else {
          const included = wholeNumber(f.includedSeats) ?? 1;
          const version = await adminConsoleApi.draftPlanVersion({
            plan_id: id,
            included_seats: included,
            max_seats: f.hardLimit ? included : f.maxSeats.trim() ? wholeNumber(f.maxSeats) : null,
            trial_days: trialDays,
            features: Object.fromEntries(
              features.map((feature) => {
                const value = featureValue(f, feature);
                return [
                  feature.key,
                  feature.kind === "boolean" ? Boolean(value) : feature.kind === "quota" ? Number(value) : String(value),
                ];
              }),
            ),
            publish: publishes,
          });
          created.current.versionId = version.id;
        }
      },
    });

    if (publishes && currency) {
      for (const interval of cycles) {
        const amount = freeTrialOf(f) ? 0 : (toMinor(f.prices[interval] ?? "", currency) ?? 0);
        defs.push({
          key: `price:${interval}`,
          label: `Publish the ${INTERVAL_LABELS[interval] ?? interval} price: ${money(amount, currency)}`,
          run: async () => {
            const versionId = created.current.versionId as string;
            await adminConsoleApi.publishPrice({
              price_book_id: f.bookId,
              ...(kind === "bundle" ? { bundle_version_id: versionId } : { plan_version_id: versionId }),
              interval,
              amount_minor: amount,
            });
          },
        });
      }
    }

    if (mode === "create") {
      for (const status of statusPath("draft", f.status)) {
        defs.push({
          key: `status:${status}`,
          label:
            status === "grandfathered"
              ? "Deactivate it (no new sales)"
              : f.status === "public"
                ? "Put it on sale, listed publicly"
                : f.status === "private"
                  ? "Put it on sale, unlisted (private)"
                  : "Make it available (required before deactivating)",
          run: async () => {
            const id = created.current.id as string;
            if (kind === "bundle") await adminConsoleApi.setBundleStatus(id, status);
            else await adminConsoleApi.setPlanStatus(id, status);
          },
        });
      }
    }
    return defs;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || runner.running) return;
    const problems = validate(form, { kind, mode, currency, features, canPrice, intervals });
    setErrors(problems);
    if (Object.keys(problems).length > 0) return;
    const defs = steps();
    const message =
      mode === "create"
        ? `${noun[0].toUpperCase()}${noun.slice(1)} “${form.name.trim()}” created${form.status === "draft" ? " as a draft" : ""}.`
        : `A new version of “${title}” is published.`;
    runner.prepare(defs);
    await act(async () => {
      await runner.runAll(defs);
      onClose({ saved: true, changed: true, message });
    }, message);
  }

  const typeOptions = [
    {
      value: "free_trial" as const,
      label: "Free Trial",
      disabled: kind === "addon",
    },
    { value: "paid" as const, label: "Paid" },
  ];

  return (
    <section className={`w360-card ${cp.card}`} aria-labelledby={headingId}>
      <form className={styles.builder} onSubmit={submit} noValidate>
        <h3 id={headingId} ref={heading} tabIndex={-1} className={styles.panelTitle}>
          {mode === "create" ? `New ${noun}` : `Publish a new version of “${title}”`}
        </h3>
        {mode === "version" && (
          <p className={cp.muted}>
            The new version applies to new sales. Existing subscriptions keep the version they bought.{" "}
            {freeTrial ? "This is a free trial, so its prices are published at zero." : ""}
          </p>
        )}
        <ErrorSummary errors={Object.values(errors)} focusRef={summary} />

        {mode === "create" && (
          <>
            <TextField label="Title" value={form.name} onChange={(v) => set("name", v)} maxLength={128} error={errors.name} />
            <TextField
              label="Caption (one line)"
              value={form.caption}
              onChange={(v) => set("caption", v.replace(/[\r\n]+/g, " "))}
              maxLength={160}
              hint="A short line shown under the title."
            />
            <DescriptionField value={form.description} onChange={(v) => set("description", v)} />
            <Segmented
              legend="Type"
              options={typeOptions}
              value={form.billingType}
              onChange={(v) => set("billingType", v)}
              hint={kind === "addon" ? "An add-on extends a subscription that already exists, so it can't be a free trial." : undefined}
            />
            <Segmented
              legend="Status"
              options={[
                { value: "draft", label: "Draft" },
                { value: "private", label: "Private" },
                { value: "public", label: "Public" },
                { value: "deactivated", label: "Deactivated" },
              ]}
              value={form.status}
              onChange={(v) => set("status", v)}
              hint={STATUS_HINT[form.status]}
            />
            <TextField
              label="Code"
              value={form.code}
              onChange={(v) => set("code", v.toLowerCase())}
              maxLength={63}
              autoComplete="off"
              spellCheck={false}
              hint="A permanent identifier: lowercase letters, digits, “-” or “_”. It can't be changed later."
              error={errors.code}
            />
          </>
        )}

        {kind !== "bundle" && mode === "create" && (
          <div className={styles.twoCol}>
            <SelectField
              label="Product"
              value={form.productId}
              onChange={(v) => setForm((f) => ({ ...f, productId: v, features: {} }))}
              options={[{ value: "", label: "Choose a product" }, ...overview.products.map((p) => ({ value: p.id, label: p.name }))]}
              error={errors.productId}
            />
            {kind === "addon" && (
              <SelectField
                label="Add-on for"
                value={form.addonForId}
                onChange={(v) => set("addonForId", v)}
                options={[
                  { value: "", label: "Choose the product it extends" },
                  ...overview.products.filter((p) => p.id !== form.productId).map((p) => ({ value: p.id, label: p.name })),
                ]}
                error={errors.addonForId}
              />
            )}
          </div>
        )}

        {mode === "version" && <p>Type: {freeTrial ? "Free Trial" : "Paid"}</p>}

        {kind !== "bundle" ? (
          <>
            <div className={styles.twoCol}>
              <TextField
                label="Included seats"
                value={form.includedSeats}
                onChange={(v) => set("includedSeats", v)}
                inputMode="numeric"
                error={errors.includedSeats}
              />
              <TextField
                label={freeTrial ? "Trial days (required for a free trial)" : "Trial days (optional)"}
                value={form.trialDays}
                onChange={(v) => set("trialDays", v)}
                inputMode="numeric"
                error={errors.trialDays}
              />
            </div>
            <label className={styles.check}>
              <input type="checkbox" checked={form.hardLimit} onChange={(e) => set("hardLimit", e.target.checked)} />
              <span>Hard limit on inclusions (customers can&apos;t go beyond the included seats)</span>
            </label>
            {!form.hardLimit && (
              <TextField
                label="Maximum seats (leave empty for unlimited)"
                value={form.maxSeats}
                onChange={(v) => set("maxSeats", v)}
                inputMode="numeric"
                error={errors.maxSeats}
              />
            )}
            <FeaturesEditor
              productChosen={Boolean(productId)}
              load={featuresLoad}
              form={form}
              errors={errors}
              onChange={(key, value) => setForm((f) => ({ ...f, features: { ...f.features, [key]: value } }))}
            />
          </>
        ) : (
          <>
            {freeTrial && (
              <TextField
                label="Trial days (required for a free trial)"
                value={form.trialDays}
                onChange={(v) => set("trialDays", v)}
                inputMode="numeric"
                error={errors.trialDays}
              />
            )}
            <Inclusions
              overview={overview}
              cycles={checkedCycles(form, intervals)}
              value={form.inclusions}
              onChange={(v) => set("inclusions", v)}
              error={errors.inclusions}
            />
          </>
        )}

        <fieldset className={styles.fieldset}>
          <legend className="w360-label">Billing cycles</legend>
          <div className={styles.cycles}>
            {intervals.map((interval) => (
              <CycleRow
                key={interval}
                interval={interval}
                checked={Boolean(form.cycles[interval])}
                onCheck={(on) => setForm((f) => ({ ...f, cycles: { ...f.cycles, [interval]: on } }))}
                price={form.prices[interval] ?? ""}
                onPrice={(v) => setForm((f) => ({ ...f, prices: { ...f.prices, [interval]: v } }))}
                currency={currency}
                freeTrial={freeTrial}
                error={errors[`price:${interval}`]}
              />
            ))}
          </div>
          {!publishes && <p className="w360-hint">A draft publishes no prices; add them when you publish a version.</p>}
          {errors.cycles && <p className="w360-error-text">{errors.cycles}</p>}
        </fieldset>

        {books.length === 0 ? (
          <p>
            There is no direct price book yet, so no price can be published.{" "}
            <Link href="/admin/pricing">Create one in Currency &amp; pricing</Link>.
          </p>
        ) : (
          <SelectField
            label="Price book"
            value={form.bookId}
            onChange={(v) => set("bookId", v)}
            options={[
              ...(book ? [] : [{ value: "", label: "Choose a price book" }]),
              ...books.map((b) => ({
                value: b.id,
                label: `${b.name} (${b.currency}${b.region ? `, ${b.region}` : ""})${b.is_active ? "" : " - inactive"}`,
              })),
            ]}
            hint="Prices are published in this book's currency."
            error={errors.bookId}
          />
        )}
        {errors.canPrice && <p className="w360-error-text">{errors.canPrice}</p>}

        <div className={styles.actions}>
          <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || runner.running}>
            {runner.started ? "Resume saving" : mode === "create" ? SUBMIT_LABEL[kind] : "Publish version"}
          </button>
          <button
            type="button"
            className="w360-btn w360-btn-secondary"
            onClick={() => onClose({ saved: false, changed: runner.started })}
          >
            Cancel
          </button>
        </div>
        {runner.started && (
          <p className="w360-hint">
            Steps already done are not repeated. Cancel keeps what was already created; you can finish it from the list.
          </p>
        )}
      </form>
      <ProgressList steps={runner.steps} title="Saving" />
      {feedback}
    </section>
  );
}

const freeTrialOf = (f: Form) => f.billingType === "free_trial";

function CycleRow({
  interval,
  checked,
  onCheck,
  price,
  onPrice,
  currency,
  freeTrial,
  error,
}: {
  interval: string;
  checked: boolean;
  onCheck: (on: boolean) => void;
  price: string;
  onPrice: (value: string) => void;
  currency: string | null;
  freeTrial: boolean;
  error?: string;
}) {
  const label = INTERVAL_LABELS[interval] ?? interval;
  return (
    <div className={styles.cycle}>
      <label className={styles.check}>
        <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} />
        <span>{label}</span>
      </label>
      {checked &&
        (freeTrial ? (
          <span className={cp.muted}>Free trial: {currency ? money(0, currency) : "0"}</span>
        ) : (
          <TextField
            label={`${label} price${currency ? ` (${currency})` : ""}`}
            value={price}
            onChange={onPrice}
            inputMode={currency === "JPY" ? "numeric" : "decimal"}
            autoComplete="off"
            error={error}
          />
        ))}
    </div>
  );
}

function FeaturesEditor({
  productChosen,
  load,
  form,
  errors,
  onChange,
}: {
  productChosen: boolean;
  load: Load<{ results: FeatureRow[] }>;
  form: Form;
  errors: Record<string, string>;
  onChange: (key: string, value: FeatureValue) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className="w360-label">Features</legend>
      {!productChosen ? (
        <p className={cp.muted}>Choose a product to set its features.</p>
      ) : (
        <LoadGate load={load} what="this product's features">
          {({ results }) =>
            results.length === 0 ? (
              <p className={cp.muted}>This product declares no features.</p>
            ) : (
              <div className={styles.twoCol}>
                {results.map((feature) => (
                  <FeatureInput
                    key={feature.id}
                    feature={feature}
                    value={featureValue(form, feature)}
                    error={errors[`feature:${feature.key}`]}
                    onChange={(v) => onChange(feature.key, v)}
                  />
                ))}
              </div>
            )
          }
        </LoadGate>
      )}
    </fieldset>
  );
}

function FeatureInput({
  feature,
  value,
  error,
  onChange,
}: {
  feature: FeatureRow;
  value: FeatureValue;
  error?: string;
  onChange: (value: FeatureValue) => void;
}) {
  if (feature.kind === "boolean") {
    return (
      <label className={styles.check}>
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <span>
          {feature.name} <span className={cp.muted}>({feature.key})</span>
        </span>
      </label>
    );
  }
  if (feature.kind === "quota") {
    return (
      <TextField
        label={`${feature.name}${feature.unit ? ` (${feature.unit})` : ""}`}
        value={String(value)}
        onChange={onChange}
        inputMode="numeric"
        hint="-1 means unlimited."
        error={error}
      />
    );
  }
  const options = feature.enum_ordering ?? [];
  return (
    <SelectField
      label={feature.name}
      value={String(value)}
      onChange={onChange}
      options={(options.includes(String(value)) ? options : [String(value), ...options]).map((o) => ({ value: o, label: o }))}
    />
  );
}

/** The products a plan (bundle) includes: a product list on the left and, for
 * the selected product, which of its published packages to include. */
function Inclusions({
  overview,
  cycles,
  value,
  onChange,
  error,
}: {
  overview: CatalogOverview;
  cycles: string[];
  value: Record<string, Inclusion>;
  onChange: (value: Record<string, Inclusion>) => void;
  error?: string;
}) {
  const [current, setCurrent] = useState(overview.products[0]?.id ?? "");
  const product = overview.products.find((p) => p.id === current) ?? overview.products[0];
  if (!product) {
    return (
      <fieldset className={styles.fieldset}>
        <legend className="w360-label">Inclusions</legend>
        <p>There are no products yet. Add one in the Product manager first.</p>
      </fieldset>
    );
  }
  const chosen = value[product.id];
  const options = packageVersions(overview, product.id, cycles, chosen?.versionId);
  const update = (next: Inclusion) => onChange({ ...value, [product.id]: next });

  return (
    <fieldset className={styles.fieldset}>
      <legend className="w360-label">Inclusions</legend>
      <div className={styles.inclusions}>
        <Tabs
          label="Products"
          vertical
          items={overview.products.map((p) => ({
            key: p.id,
            label: value[p.id]?.versionId ? `${p.name} (included)` : p.name,
          }))}
          selected={product.id}
          onSelect={setCurrent}
          listClassName={styles.productList}
          tabClassName={styles.productTab}
          panelClassName={cp.field}
        >
          {options.length === 0 ? (
            <p>No package available for the selected billing cycles.</p>
          ) : (
            <>
              <SelectField
                label={`Package for ${product.name}`}
                value={chosen?.versionId ?? ""}
                onChange={(v) => update({ versionId: v, quantity: chosen?.quantity ?? "1" })}
                options={[{ value: "", label: "Not included" }, ...options.map((o) => ({ value: o.id, label: o.label }))]}
              />
              {chosen?.versionId && (
                <TextField
                  label={`Quantity of ${product.name}`}
                  value={chosen.quantity}
                  onChange={(v) => update({ ...chosen, quantity: v })}
                  inputMode="numeric"
                />
              )}
            </>
          )}
        </Tabs>
      </div>
      {error && <p className="w360-error-text">{error}</p>}
    </fieldset>
  );
}
