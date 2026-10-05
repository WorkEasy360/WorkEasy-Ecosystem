"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { adminApi, type CapabilityInfo } from "@/lib/api/controlPlane";
import {
  partnerConsoleApi,
  type CapabilityCatalog,
  type ConsoleProduct,
  type ResellerDraftBody,
  type ResellerReview,
  type ReviewPricingRow,
} from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { ExperienceLabels } from "./PartnerConsole";
import { LoadGate, money, useAction, useLoad } from "./ui";
import { ErrorSummary, ProgressList, SelectField, Segmented, TextField, useStepRunner, type StepDef } from "./widgets";
import { SUPPORTED_CURRENCIES, formatBasisPoints, toBasisPoints, toMinor } from "./amounts";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partners → Add reseller (Phase 10b).
 *
 * Seven steps over the *existing* commands, run one at a time by the shared
 * resumable step runner: a step that finished is never repeated after a
 * verification prompt or a fix. Three rules shape everything here:
 *
 * - **The server is the authority.** The review step is a server-side dry run:
 *   it validates with the commands' own rules and returns every price, discount
 *   and commission it would create. This screen converts what was *typed* into
 *   integers (an amount into minor units, a percentage into basis points) and
 *   displays what comes back. It computes no price, margin, discount or
 *   commission.
 * - **A preset is only a shortcut.** "Standard Reseller" ticks existing
 *   capability keys the server listed; nothing is created dynamically.
 * - **Reseller is a label, not a kind of account.** The wizard creates an
 *   ordinary partner and grants capabilities; "Reseller" is what those
 *   capabilities are called.
 */

const STEPS = ["details", "capabilities", "products", "pricing", "commission", "administrator", "review"] as const;
type StepKey = (typeof STEPS)[number];

const STEP_LABELS: Record<StepKey, string> = {
  details: "Partner details",
  capabilities: "Capabilities",
  products: "Products",
  pricing: "Pricing",
  commission: "Commission",
  administrator: "Administrator",
  review: "Review",
};

const SETUP_REASON = "Set up through the Add Reseller wizard";
const MONTHLY = "monthly";

interface Draft {
  displayName: string;
  code: string;
  codeEdited: boolean;
  legalName: string;
  contactName: string;
  contactEmail: string;
  currency: string;
  /** ``null`` until the person chooses: the Standard Reseller preset applies. */
  capabilities: string[] | null;
  products: string[];
  /** Typed partner prices, keyed ``planVersionId|interval``. Empty = list price. */
  prices: Record<string, string>;
  commissionMode: "default" | "custom";
  ratePercent: string;
  adminEmail: string;
  adminRole: "admin" | "viewer";
  activate: boolean;
}

const EMPTY: Draft = {
  displayName: "",
  code: "",
  codeEdited: false,
  legalName: "",
  contactName: "",
  contactEmail: "",
  currency: "GBP",
  capabilities: null,
  products: [],
  prices: {},
  commissionMode: "default",
  ratePercent: "",
  adminEmail: "",
  adminRole: "admin",
  activate: true,
};

/** A suggested code from the display name: input convenience only - the server
 * validates the code and refuses one that is taken. */
export function suggestCode(name: string): string {
  const slug = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return /^[a-z]/.test(slug) ? slug : slug ? `p-${slug}`.slice(0, 48) : "";
}

const CODE_PATTERN = /^[a-z][a-z0-9-]{2,47}$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function presetKeys(catalog: CapabilityCatalog | null): string[] {
  return catalog?.presets.find((p) => p.key === "standard_reseller")?.capabilities ?? [];
}

function priceKey(planVersionId: string, interval: string): string {
  return `${planVersionId}|${interval}`;
}

/** The request the review takes, from the draft. Typed prices that do not parse
 * are left out (the pricing step flags them). */
export function toBody(d: Draft, catalog: CapabilityCatalog | null): ResellerDraftBody {
  const prices: ResellerDraftBody["partner_prices"] = [];
  for (const [key, text] of Object.entries(d.prices)) {
    if (!text.trim()) continue;
    const minor = toMinor(text, d.currency);
    if (minor === null) continue;
    const [plan_version_id, interval] = key.split("|");
    prices.push({ plan_version_id, interval, amount_minor: minor });
  }
  const bp = d.commissionMode === "custom" ? toBasisPoints(d.ratePercent) : null;
  return {
    partner: {
      code: d.code.trim(),
      display_name: d.displayName.trim(),
      legal_name: d.legalName.trim(),
      contact_name: d.contactName.trim(),
      contact_email: d.contactEmail.trim(),
      payout_currency: d.currency,
    },
    capabilities: d.capabilities ?? presetKeys(catalog),
    products: d.products,
    partner_prices: prices,
    commission: bp === null ? { mode: d.commissionMode } : { mode: "custom", rate_bp: bp },
    administrator: { email: d.adminEmail.trim(), role: d.adminRole },
  };
}

export function AddReseller() {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const canPrice = can(permissions, "platform.staff_pricing.manage");
  const canCommission = can(permissions, "platform.staff_commission.manage");

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [step, setStep] = useState<StepKey>("details");
  const [problems, setProblems] = useState<string[]>([]);
  const [review, setReview] = useState<{ load: "idle" | "loading" | "ready" | "error"; data?: ResellerReview; stale: boolean }>({
    load: "idle",
    stale: false,
  });
  const [confirming, setConfirming] = useState(false);
  const [finished, setFinished] = useState<{ id: string; name: string } | null>(null);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    // Whether to activate is a choice about what happens *after* the review;
    // it changes none of the reviewed figures.
    if (key !== "activate") setReview((r) => ({ ...r, stale: true }));
  };

  const [catalogLoad] = useLoad(() => partnerConsoleApi.capabilityCatalog(), []);
  const [productsLoad] = useLoad(() => partnerConsoleApi.products(), []);
  const catalog = catalogLoad.status === "ready" ? catalogLoad.data : null;

  const created = useRef<{ id?: string; version?: number }>({});
  const runner = useStepRunner();
  const { act, busy, feedback } = useAction();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    heading.current?.focus();
  }, [step]);
  useEffect(() => {
    if (problems.length > 0) summary.current?.focus();
  }, [problems]);

  const body = useMemo(() => toBody(draft, catalog), [draft, catalog]);

  const lastAsked = useRef<string | null>(null);

  async function refresh(force = false): Promise<ResellerReview | null> {
    const asked = JSON.stringify(body);
    // Leaving a field that did not change what the server would answer is not
    // a reason to ask again.
    if (!force && asked === lastAsked.current && review.load === "ready") return review.data ?? null;
    lastAsked.current = asked;
    setReview((r) => ({ ...r, load: "loading" }));
    try {
      const data = await partnerConsoleApi.review(body);
      setReview({ load: "ready", data, stale: false });
      return data;
    } catch {
      lastAsked.current = null;
      setReview({ load: "error", stale: true });
      return null;
    }
  }

  if (!manage) {
    return (
      <div className={styles.stack}>
        <h1>Add reseller</h1>
        <p role="alert">You need the permission to create and change partners to add a reseller.</p>
      </div>
    );
  }

  if (finished) {
    return (
      <div className={styles.stack}>
        <h1>Reseller created</h1>
        <p role="status">
          {finished.name} has been set up. Its administrator has been sent a link to choose a password.
        </p>
        <div className={styles.row}>
          <Link className="w360-btn w360-btn-primary" href={`/admin/partners/${finished.id}`}>
            Open {finished.name}
          </Link>
          <Link className="w360-btn w360-btn-secondary" href="/admin/partners/resellers">
            Back to the list
          </Link>
        </div>
      </div>
    );
  }

  const index = STEPS.indexOf(step);

  /** What must be fixed before leaving this step. Shallow and local: the server
   * re-validates everything in the review. */
  function stepProblems(key: StepKey): string[] {
    const out: string[] = [];
    if (key === "details") {
      if (!draft.displayName.trim()) out.push("Enter the company or display name.");
      if (!CODE_PATTERN.test(draft.code.trim()))
        out.push("The code must be 3-48 characters: lower-case letters, digits and hyphens, starting with a letter.");
      if (draft.contactEmail.trim() && !EMAIL_PATTERN.test(draft.contactEmail.trim())) out.push("Enter a valid contact email.");
    }
    if (key === "capabilities" && (draft.capabilities ?? presetKeys(catalog)).length === 0) {
      out.push("Choose at least one capability, or start from Standard Reseller.");
    }
    if (key === "pricing") {
      const bad = Object.values(draft.prices).find((text) => text.trim() && toMinor(text, draft.currency) === null);
      if (bad !== undefined) {
        out.push(`“${bad}” is not an amount in ${draft.currency}. Use digits and at most two decimals, for example 45.00.`);
      }
    }
    if (key === "commission" && draft.commissionMode === "custom" && toBasisPoints(draft.ratePercent) === null) {
      out.push("Enter the commission as a percentage from 0 to 100, with at most two decimals, for example 15 or 12.5.");
    }
    if (key === "administrator" && !EMAIL_PATTERN.test(draft.adminEmail.trim())) {
      out.push("Enter the administrator's email address.");
    }
    return out;
  }

  async function goTo(next: StepKey) {
    setProblems([]);
    setConfirming(false);
    setStep(next);
    // Figures shown on these steps come from the server.
    if (next === "pricing" || next === "commission" || next === "review") await refresh(next === "review");
  }

  async function onNext(e: React.FormEvent) {
    e.preventDefault();
    if (step === "review") {
      setConfirming(true);
      return;
    }
    const found = stepProblems(step);
    setProblems(found);
    if (found.length > 0) return;
    await goTo(STEPS[index + 1]);
  }

  function reviewedProblems(r: ResellerReview): string[] {
    return r.problems.map((p) => p.message);
  }

  function saveSteps(): StepDef[] {
    // A snapshot: a retry after verification runs exactly what was confirmed.
    const b = body;
    const shouldActivate = draft.activate;
    const defs: StepDef[] = [
      {
        key: "partner",
        label: `Create the partner “${b.partner.display_name}” (as a draft)`,
        run: async () => {
          const row = await adminApi.createPartner({
            code: b.partner.code,
            display_name: b.partner.display_name,
            payout_currency: b.partner.payout_currency,
            legal_name: b.partner.legal_name || undefined,
            contact_name: b.partner.contact_name || undefined,
            contact_email: b.partner.contact_email || undefined,
          });
          created.current = { id: row.id, version: row.version };
        },
      },
    ];
    for (const key of b.capabilities) {
      defs.push({
        key: `capability:${key}`,
        label: `Grant “${catalog?.results.find((c: CapabilityInfo) => c.key === key)?.label ?? key}”`,
        run: async () => {
          await adminApi.grantCapability(created.current.id as string, key, SETUP_REASON);
        },
      });
    }
    for (const code of b.products) {
      defs.push({
        key: `product:${code}`,
        label: `Allow selling ${code}`,
        run: async () => {
          await adminApi.grantProduct(created.current.id as string, code);
        },
      });
    }
    for (const price of b.partner_prices) {
      defs.push({
        key: `price:${price.plan_version_id}:${price.interval}`,
        label: `Set a partner price of ${money(price.amount_minor, b.partner.payout_currency)} (${price.interval})`,
        run: async () => {
          await partnerConsoleApi.setPartnerPrice(created.current.id as string, price);
        },
      });
    }
    if (b.commission.mode === "custom" && b.commission.rate_bp !== undefined) {
      const rate = b.commission.rate_bp;
      defs.push({
        key: "commission",
        label: `Assign a commission rate of ${formatBasisPoints(rate)}`,
        run: async () => {
          await partnerConsoleApi.assignCommission(created.current.id as string, {
            rate_bp: rate,
            reason: SETUP_REASON,
            expected_version: 0,
          });
        },
      });
    }
    defs.push({
      key: "administrator",
      label: `Invite ${b.administrator.email} as ${b.administrator.role === "admin" ? "administrator" : "viewer"}`,
      run: async () => {
        await partnerConsoleApi.inviteAdministrator(created.current.id as string, b.administrator.email, b.administrator.role);
      },
    });
    if (shouldActivate) {
      defs.push({
        key: "activate",
        label: "Activate the partner",
        run: async () => {
          await adminApi.setPartnerStatus(created.current.id as string, "active", SETUP_REASON, created.current.version ?? 0);
        },
      });
    }
    return defs;
  }

  async function create() {
    if (busy || runner.running) return;
    const defs = saveSteps();
    runner.prepare(defs);
    await act(async () => {
      await runner.runAll(defs);
      setFinished({ id: created.current.id as string, name: body.partner.display_name });
    }, `${body.partner.display_name} was created.`);
  }

  const latest = review.data;
  const running = runner.started || runner.running;

  return (
    <div className={styles.stack}>
      <h1>Add reseller</h1>
      <p className={styles.lead}>
        A reseller is an ordinary partner account that can sell. Nothing is created until you confirm on the last step.
      </p>
      <ol className={local.steps} aria-label="Steps">
        {STEPS.map((key, i) => (
          <li key={key} className={local.step} aria-current={key === step ? "step" : undefined}>
            <span aria-hidden="true">{i + 1}.</span> {STEP_LABELS[key]}
          </li>
        ))}
      </ol>

      <form className={local.wizard} onSubmit={onNext} noValidate>
        <h2 ref={heading} tabIndex={-1} className={styles.cardTitle}>
          Step {index + 1} of {STEPS.length}: {STEP_LABELS[step]}
        </h2>
        <ErrorSummary errors={problems} focusRef={summary} />

        {step === "details" && (
          <DetailsStep draft={draft} set={set} onName={(v) => setDraft((d) => ({ ...d, displayName: v, code: d.codeEdited ? d.code : suggestCode(v) }))} />
        )}
        {step === "capabilities" && (
          <LoadGate load={catalogLoad} what="the capabilities">
            {(c) => <CapabilitiesStep catalog={c} draft={draft} set={set} />}
          </LoadGate>
        )}
        {step === "products" && (
          <LoadGate load={productsLoad} what="the products">
            {(p) => <ProductsStep products={p.results} draft={draft} set={set} />}
          </LoadGate>
        )}
        {step === "pricing" && (
          <PricingStep
            draft={draft}
            set={set}
            review={review}
            canPrice={canPrice}
            onRefresh={(force) => {
              const found = stepProblems("pricing");
              setProblems(found);
              if (found.length === 0) void refresh(force);
            }}
          />
        )}
        {step === "commission" && <CommissionStep draft={draft} set={set} latest={latest} canCommission={canCommission} />}
        {step === "administrator" && <AdministratorStep draft={draft} set={set} />}
        {step === "review" && (
          <ReviewStep
            review={review}
            draft={draft}
            set={set}
            confirming={confirming}
            running={running}
            busy={busy}
            onCancel={() => setConfirming(false)}
            onCreate={() => void create()}
            onRetry={() => void refresh(true)}
            blocking={latest ? reviewedProblems(latest) : []}
          />
        )}

        {!confirming && (
          <div className={styles.row}>
            <button
              type="button"
              className="w360-btn w360-btn-secondary"
              disabled={index === 0 || running}
              onClick={() => void goTo(STEPS[index - 1])}
            >
              Back
            </button>
            <button
              type="submit"
              className="w360-btn w360-btn-primary"
              disabled={running || (step === "review" && !(latest?.ok && review.load === "ready" && !review.stale))}
            >
              {step === "review" ? "Create reseller…" : "Next"}
            </button>
          </div>
        )}
      </form>
      <ProgressList steps={runner.steps} title="Creating the reseller" />
      {feedback}
    </div>
  );
}

// --- step 1: the partner -------------------------------------------------------------------------

function DetailsStep({
  draft,
  set,
  onName,
}: {
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  onName: (value: string) => void;
}) {
  return (
    <>
      <TextField label="Company or display name" value={draft.displayName} onChange={onName} maxLength={120} required autoComplete="off" />
      <TextField
        label="Code"
        value={draft.code}
        onChange={(v) => {
          set("code", v.toLowerCase());
          set("codeEdited", true);
        }}
        maxLength={48}
        autoComplete="off"
        spellCheck={false}
        hint="A permanent identifier: lower-case letters, digits and hyphens. Suggested from the name."
      />
      <TextField label="Legal name (optional)" value={draft.legalName} onChange={(v) => set("legalName", v)} maxLength={200} />
      <div className={local.split}>
        <TextField label="Primary contact (optional)" value={draft.contactName} onChange={(v) => set("contactName", v)} maxLength={120} autoComplete="off" />
        <TextField label="Contact email (optional)" type="email" value={draft.contactEmail} onChange={(v) => set("contactEmail", v)} maxLength={254} autoComplete="off" />
      </div>
      <SelectField
        label="Payout currency"
        value={draft.currency}
        onChange={(v) => set("currency", v)}
        options={SUPPORTED_CURRENCIES.map((c) => ({ value: c, label: c }))}
        hint="Commission is paid in this one currency and never converted."
      />
    </>
  );
}

// --- step 2: capabilities --------------------------------------------------------------------------

function CapabilitiesStep({
  catalog,
  draft,
  set,
}: {
  catalog: CapabilityCatalog;
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const preset = catalog.presets.find((p) => p.key === "standard_reseller");
  const selected = new Set(draft.capabilities ?? presetKeys(catalog));
  const usingPreset =
    preset !== undefined && preset.capabilities.length === selected.size && preset.capabilities.every((k) => selected.has(k));

  function toggle(key: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(key);
    else next.delete(key);
    set("capabilities", [...next]);
  }

  return (
    <>
      {preset && (
        <Segmented
          legend="Start from"
          options={[
            { value: "preset", label: preset.label },
            { value: "custom", label: "Custom selection" },
          ]}
          value={usingPreset ? "preset" : "custom"}
          onChange={(v) => set("capabilities", v === "preset" ? [...preset.capabilities] : [...selected])}
          hint={preset.description}
        />
      )}
      <label className="w360-label">
        <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} /> Show technical capability names
      </label>
      {catalog.groups.map((group) => (
        <fieldset key={group.key} className={local.checkGroup}>
          <legend>{group.label}</legend>
          <ul className={local.checks}>
            {group.capabilities.map((key) => {
              const info = catalog.results.find((c) => c.key === key);
              return (
                <li key={key}>
                  <label>
                    <input type="checkbox" checked={selected.has(key)} onChange={(e) => toggle(key, e.target.checked)} /> {info?.label ?? key}
                    {advanced && <span className={`${styles.muted} ${styles.mono}`}> {key}</span>}
                  </label>
                </li>
              );
            })}
          </ul>
        </fieldset>
      ))}
    </>
  );
}

// --- step 3: products --------------------------------------------------------------------------------

function ProductsStep({
  products,
  draft,
  set,
}: {
  products: ConsoleProduct[];
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
}) {
  const searchId = useId();
  const [search, setSearch] = useState("");
  const offered = products.filter((p) => p.status === "available");
  const shown = offered.filter((p) => `${p.name} ${p.code}`.toLowerCase().includes(search.trim().toLowerCase()));
  const chosen = new Set(draft.products);

  return (
    <>
      <p className={styles.muted}>
        A reseller can sell only the products you allow here. With none, it cannot sell anything - you can add products later.
      </p>
      <div className={styles.field}>
        <label className="w360-label" htmlFor={searchId}>
          Search products
        </label>
        <input id={searchId} className="w360-input" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={80} />
      </div>
      {offered.length === 0 ? (
        <p role="status">No product is available to sell yet.</p>
      ) : shown.length === 0 ? (
        <p role="status">No product matches “{search}”.</p>
      ) : (
        <fieldset className={local.checkGroup}>
          <legend>Products this reseller may sell</legend>
          <ul className={local.checks}>
            {shown.map((p) => (
              <li key={p.code}>
                <label>
                  <input
                    type="checkbox"
                    checked={chosen.has(p.code)}
                    onChange={(e) =>
                      set("products", e.target.checked ? [...draft.products, p.code] : draft.products.filter((c) => c !== p.code))
                    }
                  />{" "}
                  {p.name} <span className={styles.muted}>({p.plans.length} {p.plans.length === 1 ? "plan" : "plans"})</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      <p className={styles.muted}>{draft.products.length} selected.</p>
    </>
  );
}

// --- step 4: pricing --------------------------------------------------------------------------------------

function PricingStep({
  draft,
  set,
  review,
  canPrice,
  onRefresh,
}: {
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  review: { load: "idle" | "loading" | "ready" | "error"; data?: ResellerReview; stale: boolean };
  canPrice: boolean;
  onRefresh: (force: boolean) => void;
}) {
  if (draft.products.length === 0) {
    return <p role="status">No products were chosen, so there is nothing to price. Go on to the next step.</p>;
  }
  const rows = (review.data?.pricing ?? []).filter((r) => r.interval === MONTHLY);
  return (
    <>
      <p className={styles.muted}>
        The customer pays the partner price; the partner earns commission on it. Leave a price empty to use the list price.
        Figures are calculated by the server. Other billing cycles use the list price until you set one on the reseller&apos;s
        page.
      </p>
      {!canPrice && (
        <p className={local.warn} role="status">
          Setting partner prices needs the platform.staff_pricing.manage permission. Every plan will use the list price.
        </p>
      )}
      {review.load === "loading" && <p aria-live="polite">Calculating…</p>}
      {review.load === "error" && <p role="alert">The figures could not be calculated. Try again.</p>}
      {rows.length > 0 && (
        <PriceTable rows={rows} draft={draft} set={set} canPrice={canPrice} onBlur={() => onRefresh(false)} />
      )}
      <div>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => onRefresh(true)} disabled={review.load === "loading"}>
          Update figures
        </button>
        {review.stale && review.load === "ready" && <span className={styles.muted}> The figures are from before your last change.</span>}
      </div>
    </>
  );
}

function PriceTable({
  rows,
  draft,
  set,
  canPrice,
  onBlur,
}: {
  rows: ReviewPricingRow[];
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  canPrice: boolean;
  onBlur: () => void;
}) {
  return (
    <div className={styles.tableWrap} role="region" aria-label="Pricing" tabIndex={0}>
      <table className={styles.table}>
        <caption>Prices per month, calculated by the server</caption>
        <thead>
          <tr>
            {["Plan", "List price", "Partner price", "Discount", "Commission per sale", "Platform keeps"].map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const key = priceKey(r.plan_version_id, r.interval);
            return (
              <tr key={key}>
                <th scope="row">
                  {r.product_name} — {r.plan_name}
                </th>
                <td className={styles.num}>{r.list_price_minor === null ? "No list price" : money(r.list_price_minor, r.currency)}</td>
                <td>
                  <label className="w360-visually-hidden" htmlFor={`price-${key}`}>
                    Partner price for {r.plan_name} in {r.currency}
                  </label>
                  <input
                    id={`price-${key}`}
                    className="w360-input"
                    inputMode="decimal"
                    value={draft.prices[key] ?? ""}
                    placeholder={r.list_price_minor === null ? "" : "List price"}
                    disabled={!canPrice}
                    onChange={(e) => set("prices", { ...draft.prices, [key]: e.target.value })}
                    onBlur={onBlur}
                  />
                </td>
                <td className={styles.num}>
                  {r.discount_minor === null ? "—" : `${money(r.discount_minor, r.currency)} (${formatBasisPoints(r.discount_bp ?? 0)})`}
                </td>
                <td className={styles.num}>
                  {r.commission_minor === null ? "No commission" : `${money(r.commission_minor, r.currency)} at ${formatBasisPoints(r.commission_bp ?? 0)}`}
                </td>
                <td className={styles.num}>{r.platform_net_minor === null ? "—" : money(r.platform_net_minor, r.currency)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --- step 5: commission ------------------------------------------------------------------------------------------

function CommissionStep({
  draft,
  set,
  latest,
  canCommission,
}: {
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  latest: ResellerReview | undefined;
  canCommission: boolean;
}) {
  const programme = latest?.commission.programme_default_bp;
  return (
    <>
      <Segmented<"default" | "custom">
        legend="Commission"
        options={[
          { value: "default", label: "Programme default" },
          { value: "custom", label: "A rate for this partner", disabled: !canCommission },
        ]}
        value={draft.commissionMode}
        onChange={(v) => set("commissionMode", v)}
        hint={
          canCommission
            ? "A rate for this partner applies from the moment it is assigned. Sales already made keep the rate they were computed under."
            : "Assigning a rate needs the platform.staff_commission.manage permission."
        }
      />
      {draft.commissionMode === "default" ? (
        <p role="status">
          {programme === null || programme === undefined
            ? "There is no programme default commission rule at the moment, so this partner would earn nothing until one exists or a rate is assigned."
            : `The partner will earn the programme default, currently ${formatBasisPoints(programme)}. If the default changes, so does this partner's rate for later sales.`}
        </p>
      ) : (
        <TextField
          label="Commission rate (%)"
          value={draft.ratePercent}
          onChange={(v) => set("ratePercent", v)}
          inputMode="decimal"
          hint="A percentage from 0 to 100, for example 15 or 12.5. Applies to every product."
        />
      )}
    </>
  );
}

// --- step 6: the administrator -------------------------------------------------------------------------------------

function AdministratorStep({ draft, set }: { draft: Draft; set: <K extends keyof Draft>(key: K, value: Draft[K]) => void }) {
  return (
    <>
      <p className={styles.muted}>
        The person who will run this partner account. If the address has no Workeasy360 account, one is created and they are
        emailed a one-time link to choose a password; an existing account is simply attached. Nothing is granted until they
        sign in.
      </p>
      <TextField label="Administrator's email" type="email" value={draft.adminEmail} onChange={(v) => set("adminEmail", v)} maxLength={254} autoComplete="off" required />
      <SelectField
        label="Role"
        value={draft.adminRole}
        onChange={(v) => set("adminRole", v === "viewer" ? "viewer" : "admin")}
        options={[
          { value: "admin", label: "Administrator (can act)" },
          { value: "viewer", label: "Viewer (read only)" },
        ]}
        hint="A partner needs at least one administrator before it can be activated."
      />
    </>
  );
}

// --- step 7: review --------------------------------------------------------------------------------------------------------

function ReviewStep({
  review,
  draft,
  set,
  confirming,
  running,
  busy,
  onCancel,
  onCreate,
  onRetry,
  blocking,
}: {
  review: { load: "idle" | "loading" | "ready" | "error"; data?: ResellerReview; stale: boolean };
  draft: Draft;
  set: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  confirming: boolean;
  running: boolean;
  busy: boolean;
  onCancel: () => void;
  onCreate: () => void;
  onRetry: () => void;
  blocking: string[];
}) {
  const r = review.data;
  if (review.load === "loading" || review.load === "idle") return <p aria-live="polite">Checking everything with the server…</p>;
  if (review.load === "error" || !r) {
    return (
      <div role="alert">
        <p>The review could not be completed.</p>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <>
      {blocking.length > 0 && (
        <div role="alert" className="w360-error-text">
          <p>The server found {blocking.length === 1 ? "a problem" : `${blocking.length} problems`} to fix. Go back to the step it names.</p>
          <ul>
            {r.problems.map((p) => (
              <li key={`${p.field}:${p.message}`}>
                {p.message} <span className={styles.muted}>({p.field})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.warnings.length > 0 && (
        <div className={local.warn} role="status">
          <p>Worth a look before you create it:</p>
          <ul>
            {r.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      <dl className={local.review}>
        <div>
          <dt>Partner</dt>
          <dd>
            {r.partner.display_name} <span className={`${styles.muted} ${styles.mono}`}>{r.partner.code}</span>
            <ExperienceLabels labels={r.experience} />
            {r.partner.contact_name || r.partner.contact_email ? (
              <div className={styles.muted}>{[r.partner.contact_name, r.partner.contact_email].filter(Boolean).join(" · ")}</div>
            ) : null}
            <div className={styles.muted}>Paid in {draft.currency}</div>
          </dd>
        </div>
        <div>
          <dt>Capabilities</dt>
          <dd>{r.capabilities.length === 0 ? "None" : r.capabilities.map((c) => c.label).join("; ")}</dd>
        </div>
        <div>
          <dt>Products</dt>
          <dd>{r.products.length === 0 ? "None: it cannot sell yet" : r.products.map((p) => p.name).join(", ")}</dd>
        </div>
        <div>
          <dt>Pricing (monthly, from the server)</dt>
          <dd>
            {r.pricing.filter((p) => p.interval === MONTHLY).length === 0 ? (
              "Nothing to price"
            ) : (
              <ul>
                {r.pricing
                  .filter((p) => p.interval === MONTHLY)
                  .map((p) => (
                    <li key={`${p.plan_version_id}|${p.interval}`}>
                      {p.plan_name}:{" "}
                      {p.partner_price_minor === null ? "no price" : money(p.partner_price_minor, p.currency)}
                      {p.partner_price_source === "list" ? " (list price)" : ""}
                      {p.commission_minor !== null && ` · commission ${money(p.commission_minor, p.currency)}`}
                    </li>
                  ))}
              </ul>
            )}
          </dd>
        </div>
        <div>
          <dt>Commission</dt>
          <dd>
            {r.commission.source === "custom" && r.commission.rate_bp !== null
              ? `${formatBasisPoints(r.commission.rate_bp)} for this partner`
              : r.commission.source === "programme_default" && r.commission.programme_default_bp !== null
                ? `Programme default, currently ${formatBasisPoints(r.commission.programme_default_bp)}`
                : "None"}
          </dd>
        </div>
        <div>
          <dt>Administrator</dt>
          <dd>
            {r.administrator.email} ({r.administrator.role === "admin" ? "administrator" : "viewer"})
          </dd>
        </div>
      </dl>
      <label className="w360-label">
        <input type="checkbox" checked={draft.activate} onChange={(e) => set("activate", e.target.checked)} disabled={running} /> Activate the
        partner as soon as it is set up
      </label>
      {confirming && (
        <div className={`w360-card ${styles.card}`} role="group" aria-label="Confirm">
          <p>
            <strong>Create {r.partner.display_name}?</strong> This creates the partner, grants the capabilities and products above
            {r.pricing.some((p) => p.partner_price_source === "proposed") ? ", sets its partner prices" : ""}
            {r.commission.source === "custom" ? ", assigns its commission rate" : ""}, invites {r.administrator.email}
            {draft.activate ? " and activates it" : " and leaves it as a draft"}. Each step is recorded in the audit log and some ask for
            a second factor.
          </p>
          <div className={styles.row}>
            <button type="button" className="w360-btn w360-btn-primary" onClick={onCreate} disabled={busy || running}>
              {running ? "Creating…" : "Confirm and create"}
            </button>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={onCancel} disabled={running}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </>
  );
}
