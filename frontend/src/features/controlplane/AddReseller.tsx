"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { partnerConsoleApi, type ResellerReview } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { LoadGate, useAction, useLoad } from "./ui";
import { ErrorSummary, ProgressList, useStepRunner } from "./widgets";
import type { FieldErrors, ProfileDraft } from "./profileDraft";
import { AdministratorStep } from "./reseller/AdministratorStep";
import { BusinessStep, ContactsStep } from "./reseller/BusinessStep";
import { CapabilitiesStep } from "./reseller/CapabilitiesStep";
import { CommissionStep } from "./reseller/CommissionStep";
import { PricingStep } from "./reseller/PricingStep";
import { ProductsStep } from "./reseller/ProductsStep";
import { ReviewStep } from "./reseller/ReviewStep";
import { StepIndicator } from "./reseller/StepIndicator";
import { WhiteLabelStep } from "./reseller/WhiteLabelStep";
import { buildCommands, type Created } from "./reseller/commands";
import {
  PURPOSES,
  STEPS,
  STEP_LABELS,
  adaptServerErrors,
  capitalize,
  emptyDraft,
  identityValid,
  messagesOf,
  stepOfField,
  suggestCode,
  toBody,
  validateAll,
  validateStep,
  type CheckMode,
  type Draft,
  type Purpose,
  type ReviewState,
  type SetDraft,
  type StepKey,
} from "./reseller/draft";
import styles from "./ControlPlane.module.css";
import cls from "./AddReseller.module.css";

export { PURPOSES, isPurpose, suggestCode, toBody } from "./reseller/draft";
export type { Purpose } from "./reseller/draft";

/**
 * Partners → Add reseller: a nine-step wizard over the *existing* commands, run
 * one at a time by the shared resumable step runner (a step that finished is
 * never repeated after a verification prompt or a fix).
 *
 * The rules that shape it:
 *
 * - **The server is the authority.** The review is a server-side dry run: it
 *   validates with the commands' own rules and returns every price, discount,
 *   commission and worked example. This screen converts what was *typed* into
 *   integers (an amount into minor units, a percentage into basis points) and
 *   displays what comes back. It computes no price, margin, discount or
 *   commission.
 * - **Problems are not readiness.** A problem is something wrong and blocks
 *   everything. Readiness is what is missing before *activation*: a draft may be
 *   saved without it, so Save draft is offered from every step once the name,
 *   code and currency are valid, and Activate waits for readiness.
 * - **A preset is only a shortcut.** "Standard Reseller" ticks existing
 *   capability keys the server listed; nothing is created dynamically.
 * - **Reseller is a label, not a kind of account.** The wizard creates one
 *   ordinary partner and grants capabilities; "Reseller" is what they are called.
 */

interface Finished {
  id: string;
  name: string;
  kind: "draft" | "activate";
  administrator: string;
  whiteLabel: boolean;
}

export function AddReseller({ purpose = "reseller" }: { purpose?: Purpose }) {
  const intent = PURPOSES[purpose];
  const router = useRouter();
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const canPrice = can(permissions, "platform.staff_pricing.manage");
  const canCommission = can(permissions, "platform.staff_commission.manage");

  const [draft, setDraft] = useState<Draft>(() => emptyDraft(purpose));
  const [step, setStep] = useState<StepKey>("business");
  // Which steps the person has tried to leave (or save past) with something wrong:
  // from then on their errors are live, so fixing a field clears its message.
  const [attempted, setAttempted] = useState<Partial<Record<StepKey, CheckMode>>>({});
  const [summary, setSummary] = useState<string[]>([]);
  const [review, setReview] = useState<ReviewState>({ load: "idle", stale: false });
  const [confirming, setConfirming] = useState(false);
  const [finished, setFinished] = useState<Finished | null>(null);
  // Unsaved work: the browser warns before the tab is closed or reloaded.
  const [dirty, setDirty] = useState(false);

  const [catalogLoad] = useLoad(() => partnerConsoleApi.capabilityCatalog(), []);
  const [productsLoad] = useLoad(() => partnerConsoleApi.products(), []);
  const catalog = catalogLoad.status === "ready" ? catalogLoad.data : null;
  const products = useMemo(() => (productsLoad.status === "ready" ? productsLoad.data.results : []), [productsLoad]);

  const created = useRef<Created>({});
  const runner = useStepRunner();
  const { act, busy, feedback } = useAction();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const shown = useRef({ step, locked: false });
  const lastAsked = useRef<string | null>(null);
  const lastAction = useRef<"draft" | "activate">("draft");
  const activateHelpId = useId();

  const touch = () => {
    setDirty(true);
    setReview((r) => ({ ...r, stale: true }));
  };
  const set: SetDraft = (key, value) => {
    setDraft((d) => ({ ...d, [key]: value }));
    // Which action the review highlights changes none of the reviewed figures.
    if (key === "status") setDirty(true);
    else touch();
  };
  const patchProfile = (patch: Partial<ProfileDraft>) => {
    setDraft((d) => ({ ...d, profile: { ...d.profile, ...patch } }));
    touch();
  };
  const onName = (value: string) => {
    setDraft((d) => ({ ...d, profile: { ...d.profile, displayName: value }, code: d.codeEdited ? d.code : suggestCode(value) }));
    touch();
  };

  const working = runner.running || busy;
  const waiting = runner.steps.some((s) => s.state === "waiting");
  // Once any command has run the partner exists, so the form is replaced by the
  // retry panel: what was already done is never edited under the runner's feet.
  const locked = runner.started && !finished;

  useEffect(() => {
    // Only a real change of step moves focus (not the mount, and not a Strict
    // Mode re-run of this effect).
    if (shown.current.step === step && shown.current.locked === locked) return;
    shown.current = { step, locked };
    heading.current?.focus();
  }, [step, locked]);
  useEffect(() => {
    if (summary.length > 0) summaryRef.current?.focus();
  }, [summary]);
  useEffect(() => {
    if (!dirty || finished) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, finished]);

  const body = useMemo(() => toBody(draft, catalog, intent.preset), [draft, catalog, intent.preset]);
  const checkOptions = { catalog, preset: intent.preset };

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
        <h1>{intent.title}</h1>
        <p role="alert">You need the permission to create and change partners to add a {intent.noun}.</p>
      </div>
    );
  }

  if (finished) {
    const href = `/admin/partners/${encodeURIComponent(finished.id)}`;
    return (
      <div className={styles.stack}>
        <h1>{finished.kind === "draft" ? `${capitalize(intent.noun)} saved as a draft` : `${capitalize(intent.noun)} created`}</h1>
        <p role="status">
          {finished.kind === "draft"
            ? `${finished.name} has been saved as a draft. It can do nothing until it is activated; its page lists what is still missing.`
            : `${finished.name} has been set up and activated.${
                finished.administrator ? ` ${finished.administrator} has been sent a link to choose a password.` : ""
              }`}
        </p>
        <div className={styles.row}>
          {finished.whiteLabel && (
            <Link className="w360-btn w360-btn-primary" href={`/admin/partners/white-label/${encodeURIComponent(finished.id)}`}>
              Continue to white-label setup
            </Link>
          )}
          <Link className={`w360-btn ${finished.whiteLabel ? "w360-btn-secondary" : "w360-btn-primary"}`} href={href}>
            Open {finished.name}
          </Link>
          <Link className="w360-btn w360-btn-secondary" href={intent.list}>
            Back to the list
          </Link>
        </div>
      </div>
    );
  }

  const index = STEPS.indexOf(step);
  const stepErrors: FieldErrors = {
    ...(review.data && !review.stale ? adaptServerErrors(review.data.problems) : {}),
    ...(attempted[step] ? validateStep(step, draft, attempted[step], checkOptions) : {}),
  };

  /** Show a step. The capabilities step arrives with its preset pre-ticked, which
   * is the first moment the preset counts as chosen. */
  function show(target: StepKey) {
    setConfirming(false);
    setStep(target);
    if (target === "capabilities" && !draft.capabilitiesSeen) {
      setDraft((d) => ({ ...d, capabilitiesSeen: true }));
      touch();
    }
  }

  async function goTo(next: StepKey) {
    setSummary([]);
    show(next);
    // Figures shown on these steps come from the server.
    if (next === "pricing" || next === "commission" || next === "review") await refresh(next === "review");
  }

  async function onNext(event: React.FormEvent) {
    event.preventDefault();
    if (step === "review" || working) return;
    const found = validateStep(step, draft, "continue", checkOptions);
    if (Object.keys(found).length > 0) {
      setAttempted((a) => ({ ...a, [step]: "continue" }));
      setSummary(messagesOf(found));
      return;
    }
    await goTo(STEPS[index + 1]);
  }

  /** Create the partner as a draft with whatever is filled in. Nothing is sent
   * until what is typed is right (locally, then by the server's own dry run). */
  async function saveDraft() {
    if (working) return;
    const all = validateAll(draft, checkOptions);
    const wrong = STEPS.filter((key) => all[key]);
    if (wrong.length > 0) {
      setAttempted((a) => {
        const next = { ...a };
        for (const key of wrong) next[key] = next[key] ?? "draft";
        return next;
      });
      const target = wrong.includes(step) ? step : wrong[0];
      show(target);
      setSummary(messagesOf(all[target] ?? {}));
      return;
    }
    const data = await refresh(false);
    if (!data) {
      setSummary(["The check with the server could not be completed. Try again."]);
      return;
    }
    if (data.problems.length > 0) {
      const steps = data.problems.map((p) => stepOfField(p.field));
      const target = steps.includes(step) ? step : (steps.find((s) => s !== "review") ?? step);
      show(target);
      setSummary([...new Set(data.problems.map((p) => p.message))]);
      return;
    }
    await execute("draft");
  }

  async function execute(kind: "draft" | "activate") {
    if (working) return;
    lastAction.current = kind;
    const productNames: Record<string, string> = {};
    for (const p of products) productNames[p.code] = p.name;
    const planNames: Record<string, string> = {};
    for (const row of review.data?.pricing ?? []) planNames[row.plan_version_id] = `${row.product_name} — ${row.plan_name}`;
    // A snapshot: a retry after verification runs exactly what was confirmed.
    const defs = buildCommands({ body, catalog, productNames, planNames, created, reason: intent.reason, activate: kind === "activate" });
    runner.prepare(defs);
    const name = body.partner.display_name;
    const administrator = body.administrator?.email ?? "";
    const whiteLabel = body.white_label === true;
    await act(
      async () => {
        await runner.runAll(defs);
        const id = created.current.id as string;
        setDirty(false);
        setFinished({ id, name, kind, administrator, whiteLabel });
        if (kind === "draft") router.push(`/admin/partners/${encodeURIComponent(id)}`);
      },
      kind === "draft" ? `${name} was saved as a draft.` : `${name} was created and activated.`,
    );
  }

  const data = review.data;
  const reviewReady = review.load === "ready" && !review.stale && data !== undefined;
  const canSave = reviewReady && data.ok;
  const canActivate = canSave && data.readiness.can_activate;
  const identityOk = identityValid(draft);
  const draftIsPrimary = draft.status === "draft";
  const activateWhy = !reviewReady
    ? ""
    : !data.ok
      ? "Fix the problems listed above first."
      : data.readiness.missing.map((m) => m.label).join(", ");

  const stepContent = (() => {
    const props = { draft, set, patchProfile, errors: stepErrors };
    switch (step) {
      case "business":
        return <BusinessStep {...props} onName={onName} />;
      case "contacts":
        return <ContactsStep {...props} />;
      case "capabilities":
        return (
          <LoadGate load={catalogLoad} what="the capabilities">
            {(c) => <CapabilitiesStep catalog={c} draft={draft} set={set} errors={stepErrors} preset={intent.preset} />}
          </LoadGate>
        );
      case "products":
        return (
          <LoadGate load={productsLoad} what="the products">
            {(p) => <ProductsStep products={p.results} draft={draft} set={set} errors={stepErrors} />}
          </LoadGate>
        );
      case "pricing":
        return <PricingStep draft={draft} set={set} errors={stepErrors} review={review} canPrice={canPrice} onRefresh={(force) => void refresh(force)} />;
      case "commission":
        return (
          <CommissionStep
            {...props}
            review={review}
            canCommission={canCommission}
            products={products}
            onRefresh={(force) => {
              if (Object.keys(validateStep("commission", draft, "continue", checkOptions)).length === 0) void refresh(force);
            }}
          />
        );
      case "administrator":
        return <AdministratorStep {...props} />;
      case "whitelabel":
        return <WhiteLabelStep draft={draft} set={set} errors={stepErrors} />;
      case "review":
        return (
          <ReviewStep
            review={review}
            draft={draft}
            catalog={catalog}
            products={products}
            confirming={confirming}
            running={working}
            busy={busy}
            canActivate={Boolean(canActivate)}
            onEdit={(target) => void goTo(target)}
            onCancel={() => setConfirming(false)}
            onConfirm={() => void execute("activate")}
            onRetry={() => void refresh(true)}
          />
        );
    }
  })();

  return (
    <div className={styles.stack}>
      <h1>{intent.title}</h1>
      <p className={styles.lead}>{intent.lead}</p>
      {purpose !== "reseller" && (
        <p className={styles.muted}>
          <Link href="/admin/partners/new">Choose a different purpose</Link>
        </p>
      )}
      <StepIndicator step={step} locked={locked} onJump={(target) => void goTo(target)} />

      {locked ? (
        <section className={cls.wizard} aria-labelledby="setup-state">
          <h2 id="setup-state" ref={heading} tabIndex={-1} className={styles.cardTitle}>
            {working ? "Setting up the partner" : waiting ? "Waiting for your verification" : "Setup did not finish"}
          </h2>
          {working ? (
            <p>Each step is recorded in the audit log. Steps that have finished are not repeated.</p>
          ) : waiting ? (
            <p>This change needs a recent verification. Enter your code below and the setup carries on from where it stopped.</p>
          ) : (
            <>
              <p>
                The partner has been created, but one step failed (see below). Try again to carry on from where it stopped: steps that
                finished are not repeated. You can also open the partner and finish its setup from its own page.
              </p>
              <div className={cls.actions}>
                <button type="button" className="w360-btn w360-btn-primary" onClick={() => void execute(lastAction.current)}>
                  Try again
                </button>
                {created.current.id && (
                  <Link className="w360-btn w360-btn-secondary" href={`/admin/partners/${encodeURIComponent(created.current.id)}`}>
                    Open the partner
                  </Link>
                )}
              </div>
            </>
          )}
        </section>
      ) : (
        <form className={cls.wizard} onSubmit={onNext} noValidate>
          <h2 ref={heading} tabIndex={-1} className={styles.cardTitle}>
            Step {index + 1} of {STEPS.length}: {STEP_LABELS[step]}
          </h2>
          <ErrorSummary errors={summary} focusRef={summaryRef} />

          {stepContent}

          {!confirming && (
            <>
              <div className={cls.actions}>
                <button type="button" className="w360-btn w360-btn-secondary" disabled={index === 0 || working} onClick={() => void goTo(STEPS[index - 1])}>
                  Back
                </button>
                {step === "review" ? (
                  <>
                    <button
                      type="button"
                      className={`w360-btn ${draftIsPrimary ? "w360-btn-primary" : "w360-btn-secondary"}`}
                      disabled={!canSave || working}
                      onClick={() => void saveDraft()}
                    >
                      Save draft
                    </button>
                    <button
                      type="button"
                      className={`w360-btn ${draftIsPrimary ? "w360-btn-secondary" : "w360-btn-primary"}`}
                      disabled={!canActivate || working}
                      aria-describedby={activateWhy ? activateHelpId : undefined}
                      onClick={() => setConfirming(true)}
                    >
                      Activate {intent.noun}
                    </button>
                  </>
                ) : (
                  <>
                    <button type="submit" className="w360-btn w360-btn-primary" disabled={working}>
                      Save &amp; continue
                    </button>
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      disabled={!identityOk || working || review.load === "loading"}
                      onClick={() => void saveDraft()}
                    >
                      Save draft
                    </button>
                  </>
                )}
              </div>
              {step === "review" && activateWhy && (
                <p id={activateHelpId} className={styles.muted}>
                  {data && !data.ok ? activateWhy : `Activate is not available yet. Still needed: ${activateWhy}. You can save a draft without them.`}
                </p>
              )}
              {step !== "review" && !identityOk && (
                <p className={styles.muted}>Save draft is available once the name, code and payout currency are filled in.</p>
              )}
            </>
          )}
        </form>
      )}
      <ProgressList steps={runner.steps} title={`Setting up the ${intent.noun}`} />
      {feedback}
    </div>
  );
}
