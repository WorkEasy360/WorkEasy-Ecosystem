"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LoadGate, useAction, useLoad } from "@/features/controlplane/ui";
import { panelApi, type OwnProfile, type UpgradeRequest, type UpgradeState, type UpgradeTarget } from "@/lib/api/partnerPanel";
import { workspaceHome } from "./experiences";
import { PageHeader, usePanel } from "./PanelKit";
import {
  BrandStep,
  BusinessStep,
  CommercialStep,
  DomainStep,
  PricingStep,
  ProductsStep,
  type BrandValue,
  type BusinessValue,
} from "./UpgradeSteps";
import { STATUS_BADGE, STATUS_LABEL, TARGETS, TARGET_LABEL, TARGET_LEAD, stepsFor, upgradeHref } from "./upgrade";
import styles from "./PartnerPanel.module.css";

/**
 * The upgrade page and its step wizard (ADR-049).
 *
 * An upgrade is a **request on the partner's own account**: the page never asks
 * for a sign-in or a password, never creates a user or a partner, and nothing on it
 * grants a power - submitting sends the request to Workeasy360, whose staff approve
 * it on the server, which is the only thing that changes what the partner may do.
 * Each step is saved as it is completed (so leaving and coming back loses nothing),
 * and the server validates every save and the submission again.
 */

export function UpgradePage({ target }: { target?: UpgradeTarget }) {
  const { id } = usePanel();
  const [load, reload] = useLoad(() => panelApi.upgrade(id), [id]);
  return (
    <div className={styles.page}>
      <LoadGate load={load} what="your upgrade">
        {(state) => <UpgradeBody state={state} target={target} reload={reload} />}
      </LoadGate>
    </div>
  );
}

function UpgradeBody({ state, target, reload }: { state: UpgradeState; target?: UpgradeTarget; reload: () => void }) {
  const { base } = usePanel();
  const open = state.open;

  if (open) {
    return open.editable ? (
      <Wizard state={state} request={open} onChanged={reload} />
    ) : (
      <Submitted state={state} request={open} onChanged={reload} />
    );
  }

  const latest = state.recent[0];
  if (latest?.status === "approved" && state.current === latest.to_experience && (!target || target === latest.to_experience)) {
    const label = TARGET_LABEL[latest.to_experience];
    return (
      <>
        <PageHeader title={`Your ${label} upgrade is approved`} lead="Same sign-in, same account." />
        <section className={styles.grow} aria-live="polite">
          <p>
            Everything you earned as {latest.from_experience === "affiliate" ? "an affiliate" : "a partner"} is still here. Your {label} workspace
            is now where you start when you sign in.
          </p>
          <p>
            <Link className="w360-btn w360-btn-primary" href={workspaceHome(latest.to_experience)}>
              Open your {label} workspace
            </Link>
          </p>
        </section>
      </>
    );
  }

  if (target && state.eligible.includes(target)) {
    return <Intro state={state} target={target} onStarted={reload} />;
  }
  return (
    <>
      <PageHeader title="Upgrade your partnership" lead="Choose the next step for your account." />
      {state.eligible.length === 0 ? (
        <section className={styles.grow}>
          <p>There is no upgrade available for your account right now.</p>
          <p>
            <Link href={`${base}/dashboard`}>Back to the dashboard</Link>
          </p>
        </section>
      ) : (
        <ul className={styles.cards}>
          {TARGETS.filter((t) => state.eligible.includes(t)).map((t) => (
            <li key={t} className={styles.card}>
              <h2 className={styles.cardTitle}>{TARGET_LABEL[t]}</h2>
              <p>{TARGET_LEAD[t]}</p>
              <p>
                <Link className="w360-btn w360-btn-primary" href={upgradeHref(base, t)}>
                  Upgrade to {TARGET_LABEL[t]}
                </Link>
              </p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// --- before a request exists -----------------------------------------------------------------------

function Intro({ state, target, onStarted }: { state: UpgradeState; target: UpgradeTarget; onStarted: () => void }) {
  const { id, base } = usePanel();
  const { act, busy, feedback } = useAction(onStarted);
  const label = TARGET_LABEL[target];
  const steps = stepsFor(state, target);
  return (
    <>
      <PageHeader title={`Upgrade to ${label}`} lead={TARGET_LEAD[target]} />
      <section className={styles.grow}>
        <h2>What happens</h2>
        <ul>
          <li>You keep the same sign-in and the same partner account. You will not register again.</li>
          <li>Your referral links, commissions, payouts and history stay exactly as they are.</li>
          <li>You answer a few questions ({steps.map((s) => s.label.toLowerCase()).join(", ")}). Each step is saved as you go.</li>
          <li>Workeasy360 reviews the request. Nothing about your account changes until it is approved.</li>
        </ul>
        {feedback}
        {state.can_request ? (
          <div className={styles.wizardActions}>
            <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void act(() => panelApi.startUpgrade(id, target), "Started.")}>
              Start your {label} request
            </button>
            <Link className="w360-btn w360-btn-secondary" href={`${base}/dashboard`}>
              Not now
            </Link>
          </div>
        ) : (
          <p role="status">Only a partner administrator can request an upgrade. Ask an administrator of your partner account.</p>
        )}
      </section>
    </>
  );
}

// --- while it is being filled in ---------------------------------------------------------------------------

function Wizard({ state, request, onChanged }: { state: UpgradeState; request: UpgradeRequest; onChanged: () => void }) {
  const { id, base } = usePanel();
  const target = request.to_experience;
  const steps = stepsFor(state, target);
  const [index, setIndex] = useState(0);
  // The newest copy the server sent back from a save. The next save must carry its
  // version, and the page's own load may not have caught up yet.
  const [latest, setLatest] = useState<UpgradeRequest | null>(null);
  const current = latest && latest.id === request.id && latest.version >= request.version ? latest : request;
  const [profileLoad] = useLoad(() => panelApi.profile(id), [id]);
  const [savedProfile, setSavedProfile] = useState<OwnProfile | null>(null);
  const profile = savedProfile ?? (profileLoad.status === "ready" ? profileLoad.data : null);
  const { act, busy, feedback } = useAction();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const moved = useRef(false);

  useEffect(() => {
    // Move keyboard and screen-reader focus to the new step's heading; not on first paint.
    if (moved.current) heading.current?.focus();
    moved.current = true;
  }, [index]);

  const step = steps[index];
  const back = () => setIndex((i) => Math.max(0, i - 1));
  const advance = (save: () => Promise<unknown>) =>
    void act(async () => {
      await save();
      setIndex((i) => Math.min(i + 1, steps.length - 1));
    }, "Saved.");

  const saveSection = async (section: Parameters<typeof panelApi.saveUpgradeSection>[2], data: unknown) => {
    setLatest(await panelApi.saveUpgradeSection(id, current.id, section, data, current.version));
  };
  const saveProfile = async (body: Record<string, unknown>) => {
    if (!profile) throw new Error("Your details have not loaded yet.");
    setSavedProfile(await panelApi.saveProfile(id, profile.version, body));
  };

  const common = { busy, onBack: back };
  let form: React.ReactNode = null;
  if (!step) {
    form = <p role="alert">This upgrade has no steps.</p>;
  } else if (step.key === "business") {
    form = profile ? (
      <BusinessStep
        profile={profile}
        {...common}
        backLabel="Cancel"
        onBack={() => window.history.back()}
        onNext={(v) => advance(() => saveProfile(v as BusinessValue))}
      />
    ) : (
      <LoadGate load={profileLoad} what="your business details">
        {() => null}
      </LoadGate>
    );
  } else if (step.key === "brand" && profile) {
    form = (
      <BrandStep
        request={current}
        profile={profile}
        {...common}
        onNext={(v) =>
          advance(async () => {
            const { brand, supportPhone } = v as BrandValue;
            await saveSection("brand", brand);
            await saveProfile({ support: { phone: supportPhone } });
          })
        }
      />
    );
  } else if (step.key === "products") {
    form = <ProductsStep request={current} products={state.products} {...common} onNext={(v) => advance(() => saveSection("products", { products: v }))} />;
  } else if (step.key === "pricing") {
    form = <PricingStep request={current} {...common} onNext={(v) => advance(() => saveSection("pricing", v))} />;
  } else if (step.key === "commercial") {
    form = <CommercialStep request={current} {...common} onNext={(v) => advance(() => saveSection("commercial", v))} />;
  } else if (step.key === "domain") {
    form = <DomainStep request={current} {...common} onNext={(v) => advance(() => saveSection("domain", v))} />;
  } else if (step.key === "review") {
    form = (
      <Review
        state={state}
        request={current}
        busy={busy}
        onBack={back}
        onSubmit={() =>
          void act(async () => {
            setLatest(await panelApi.submitUpgrade(id, current.id, current.version));
            onChanged();
          }, "Sent for review.")
        }
        onRefresh={onChanged}
      />
    );
  }

  return (
    <>
      <PageHeader title={`Upgrade to ${TARGET_LABEL[target]}`} lead={TARGET_LEAD[target]} />
      {current.status === "profile_required" && current.info_request_note && (
        <p role="note" className={styles.notice}>
          Workeasy360 asks for more information: {current.info_request_note}
        </p>
      )}
      <ol className={styles.steps} aria-label="Upgrade steps">
        {steps.map((s, i) => (
          <li key={s.key} className={styles.step} aria-current={i === index ? "step" : undefined}>
            <span className={styles.stepNumber}>{i + 1}.</span> {s.label}
          </li>
        ))}
      </ol>
      {feedback}
      {step && (
        <h2 ref={heading} tabIndex={-1}>
          Step {index + 1} of {steps.length}: {step.label}
        </h2>
      )}
      {form}
      <div className={styles.wizardActions}>
        <Withdraw request={current} onChanged={onChanged} />
        <Link href={`${base}/dashboard`}>Leave and continue later</Link>
      </div>
    </>
  );
}

// --- review and read-only views ----------------------------------------------------------------------------------

function Review({
  state,
  request,
  busy,
  onBack,
  onSubmit,
  onRefresh,
}: {
  state: UpgradeState;
  request: UpgradeRequest;
  busy: boolean;
  onBack: () => void;
  onSubmit: () => void;
  onRefresh: () => void;
}) {
  // The checklist is the server's answer for the stored facts; ask again on arrival
  // so what was just saved is counted.
  useEffect(() => {
    onRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const missing = state.checklist.filter((c) => c.required && !c.done);
  return (
    <div className={styles.page}>
      <Summary state={state} request={request} />
      <section className={styles.panel} aria-labelledby="upgrade-checklist">
        <h3 id="upgrade-checklist">Before you send it</h3>
        <ul className={styles.checklist}>
          {state.checklist.map((c) => (
            <li key={c.key}>
              {c.done ? "Done" : c.required ? "Still needed" : "Optional"}: {c.label}
              {c.detail ? ` - ${c.detail}` : ""}
            </li>
          ))}
        </ul>
      </section>
      <p className={styles.muted}>
        Workeasy360 reviews every request. Nothing about your account changes until it is approved, and you keep using your current workspace in the
        meantime.
      </p>
      <div className={styles.wizardActions}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onBack}>
          Back
        </button>
        <button type="button" className="w360-btn w360-btn-primary" disabled={busy || missing.length > 0} onClick={onSubmit}>
          Send for review
        </button>
        {missing.length > 0 && <span role="status">Complete the items marked &ldquo;Still needed&rdquo; first.</span>}
      </div>
    </div>
  );
}

function Summary({ state, request }: { state: UpgradeState; request: UpgradeRequest }) {
  const p = request.proposal;
  const names = new Map(state.products.map((x) => [x.code, x.name]));
  const rows: [string, string][] = [];
  if (p.brand) {
    rows.push(["Brand name", p.brand.brand_name]);
    if (p.brand.primary_color) rows.push(["Primary colour", p.brand.primary_color]);
    if (p.brand.secondary_color) rows.push(["Secondary colour", p.brand.secondary_color]);
    if (p.brand.support_email) rows.push(["Support email", p.brand.support_email]);
  }
  rows.push(["Products", (p.products ?? []).map((c) => names.get(c) ?? c).join(", ") || "None chosen"]);
  rows.push(["Pricing", p.pricing ? (p.pricing.preference === "negotiated" ? "I would like to discuss my pricing" : "Standard prices") : "Not set"]);
  if (p.pricing?.notes) rows.push(["Pricing notes", p.pricing.notes]);
  if (p.commercial) {
    if (p.commercial.expected_customers != null) rows.push(["Expected customers", String(p.commercial.expected_customers)]);
    if (p.commercial.team_size != null) rows.push(["Team size", String(p.commercial.team_size)]);
    if (p.commercial.customer_management) rows.push(["Customer management", p.commercial.customer_management]);
    if (p.commercial.notes) rows.push(["Notes", p.commercial.notes]);
  }
  if (request.to_experience === "white_label") rows.push(["Custom domain", p.domain?.hostname || "None"]);
  return (
    <section className={styles.panel} aria-labelledby="upgrade-summary">
      <h3 id="upgrade-summary">What you are asking for</h3>
      <dl className={styles.summary}>
        {rows.map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Withdraw({ request, onChanged }: { request: UpgradeRequest; onChanged: () => void }) {
  const { id } = usePanel();
  const { act, busy, feedback } = useAction(onChanged);
  return (
    <>
      <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => panelApi.cancelUpgrade(id, request.id), "Request withdrawn.")}>
        Withdraw request
      </button>
      {feedback}
    </>
  );
}

function Submitted({ state, request, onChanged }: { state: UpgradeState; request: UpgradeRequest; onChanged: () => void }) {
  const { base } = usePanel();
  return (
    <>
      <PageHeader title={`${TARGET_LABEL[request.to_experience]} upgrade`} lead="Your request is with Workeasy360." />
      <section className={styles.grow} aria-live="polite">
        <p>
          <span className={STATUS_BADGE[request.status]}>{STATUS_LABEL[request.status]}</span>
        </p>
        <p>
          Nothing about your account has changed. You will keep signing in the same way, and your {TARGET_LABEL[request.to_experience]} workspace opens
          once the request is approved.
        </p>
        <div className={styles.wizardActions}>
          {state.can_request && <Withdraw request={request} onChanged={onChanged} />}
          <Link href={`${base}/dashboard`}>Back to the dashboard</Link>
        </div>
      </section>
      <Summary state={state} request={request} />
    </>
  );
}
