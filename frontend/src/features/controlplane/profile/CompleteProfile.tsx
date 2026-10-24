"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi, type CompletionSection } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "../ControlShell";
import { TypeBadges } from "../labels";
import { CompletionMeter, SetupStatusBadge } from "../PartnerConsole";
import { LoadGate, useLoad } from "../ui";
import { IdentityStep } from "../whitelabel/BrandSteps";
import { DomainStep } from "../whitelabel/DomainStep";
import { CommunicationStep } from "../whitelabel/PortalSteps";
import type { Nav } from "../whitelabel/shared";
import { useWhiteLabelContext } from "../whitelabel/useWhiteLabelContext";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import css from "../WhiteLabelSetup.module.css";
import mine from "./CompleteProfile.module.css";
import {
  BusinessStep,
  CommissionStep,
  MarketingStep,
  PayoutStep,
  PortalStep,
  PricingStep,
  ProductsStep,
  ReferralStep,
  ReviewStep,
  SalesStep,
  SubResellerStep,
  TeamStep,
  type StepProps,
} from "./ProfileSteps";
import { STEP_SECTION, WHITE_LABEL_STEPS, stepLabel, stepsFor, type ProfileStepKey } from "./steps";

/**
 * A partner's **Complete Your Profile** (ADR-048): one step-based wizard whose
 * steps follow what the partner can do. Its labels - Reseller, White-label,
 * Affiliate, derived by the server from the capability grants - pick the steps,
 * so a combined partner gets the union, and a change of capabilities changes the
 * steps on the next read.
 *
 * Each step is the partner page's own panel for that thing (business details,
 * product grants, prices, commission, capabilities, referral codes, brand,
 * domain, team), saving through the same commands; the wizard only puts them in
 * order and shows, beside them, the completion the server computed. Nothing here
 * grants anything because a step is done: a complete profile is guidance, and
 * activation is a separate act by staff (Review step).
 */
export function CompleteProfile({ partnerId }: { partnerId: string }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? `/admin/partners/${partnerId}/profile`;
  const [rev, setRev] = useState(0);
  const [load, reload] = useLoad(() => adminApi.partner(partnerId), [partnerId]);
  const [summary, reloadSummary] = useLoad(() => partnerConsoleApi.summary(partnerId), [partnerId]);
  const [chosen, setChosen] = useState<string | null>(params.get("step"));
  const [moved, setMoved] = useState(false);
  const [notice, setNotice] = useState("");

  const bump = useCallback(() => {
    reload();
    reloadSummary();
    setRev((n) => n + 1);
  }, [reload, reloadSummary]);

  const go = useCallback(
    (next: string) => {
      setMoved(true);
      setNotice("");
      setChosen(next);
      router.replace(`${pathname}?step=${encodeURIComponent(next)}`, { scroll: false });
    },
    [pathname, router],
  );

  if (!manage) {
    return (
      <div className={styles.stack}>
        <h1>Complete Your Profile</h1>
        <p role="alert">You need the permission to manage partners to set a partner up.</p>
      </div>
    );
  }

  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        <Link href={`/admin/partners/${partnerId}`}>Back to the partner</Link>
      </p>
      <LoadGate load={load} what="this partner">
        {(partner) => (
          <LoadGate load={summary} what="the partner's profile">
            {(d) => {
              const labels = d.experience.map((e) => e.key);
              const held = d.capabilities.map((c) => c.key);
              const steps = stepsFor(labels, held);
              const step = (steps.includes(chosen as ProfileStepKey) ? chosen : steps[0]) as ProfileStepKey;
              const nav: Nav = {
                step,
                go,
                announce: setNotice,
                focusHeading: moved,
                order: steps,
                labelOf: (key) => stepLabel(key, labels),
              };
              const props: StepProps = { partnerId, partner, summary, rev, bump, nav, summaryData: d };
              return (
                <>
                  <div className={local.titleRow}>
                    <h1>Complete Your Profile: {partner.display_name}</h1>
                    <SetupStatusBadge value={d.profile_completion.setup_status} />
                  </div>
                  <TypeBadges labels={d.experience} />
                  <div className={mine.meterRow}>
                    <CompletionMeter value={d.profile_completion} />
                    {!d.profile_completion.complete && (
                      <span className={styles.muted}>Still needed: {d.profile_completion.missing.map((m) => m.label).join(", ")}.</span>
                    )}
                  </div>
                  <div className={mine.layout}>
                    <StepList steps={steps} current={step} labels={labels} sections={d.profile_completion.sections} onGo={go} />
                    <div className={mine.body}>
                      <p role="status" aria-live="polite" className={css.notice}>
                        {notice}
                      </p>
                      <StepBody step={step} props={props} />
                    </div>
                  </div>
                </>
              );
            }}
          </LoadGate>
        )}
      </LoadGate>
    </div>
  );
}

const STATE_TEXT: Record<CompletionSection["state"], string> = {
  complete: "complete",
  incomplete: "needs attention",
  pending: "waiting",
  optional: "optional",
};

function StepList({
  steps,
  current,
  labels,
  sections,
  onGo,
}: {
  steps: ProfileStepKey[];
  current: ProfileStepKey;
  labels: string[];
  sections: CompletionSection[];
  onGo: (step: string) => void;
}) {
  return (
    <nav aria-label="Profile steps" className={mine.steps}>
      <ol>
        {steps.map((key, i) => {
          const section = STEP_SECTION[key] ? sections.find((s) => s.key === STEP_SECTION[key]) : undefined;
          const state = section?.state;
          return (
            <li key={key}>
              <button
                type="button"
                className={mine.stepButton}
                aria-current={key === current ? "step" : undefined}
                onClick={() => onGo(key)}
              >
                <span className={state === "complete" ? mine.dotDone : state === "incomplete" ? mine.dotTodo : mine.dot} aria-hidden="true">
                  {state === "complete" ? "✓" : i + 1}
                </span>
                <span>
                  {stepLabel(key, labels)}
                  {state && <span className="w360-visually-hidden"> ({STATE_TEXT[state]})</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function StepBody({ step, props }: { step: ProfileStepKey; props: StepProps }) {
  if (WHITE_LABEL_STEPS.includes(step)) return <WhiteLabelStepHost step={step} props={props} />;
  switch (step) {
    case "business":
      return <BusinessStep {...props} />;
    case "products":
      return <ProductsStep {...props} />;
    case "pricing":
      return <PricingStep {...props} />;
    case "commission":
      return <CommissionStep {...props} />;
    case "sales":
      return <SalesStep {...props} />;
    case "subresellers":
      return <SubResellerStep {...props} />;
    case "referral":
      return <ReferralStep {...props} />;
    case "payout":
      return <PayoutStep {...props} />;
    case "marketing":
      return <MarketingStep {...props} />;
    case "team":
      return <TeamStep {...props} />;
    case "review":
      return <ReviewStep {...props} />;
    default:
      return null;
  }
}

/** The brand, domain, communication and portal steps run the white-label
 * wizard's own steps, over the partner's white-label setup read. A change there
 * also refreshes the completion beside the steps. */
function WhiteLabelStepHost({ step, props }: { step: ProfileStepKey; props: StepProps }) {
  const { bump } = props;
  const nav: Nav = props.nav;
  const { load, ctx, stale } = useWhiteLabelContext(props.partnerId, nav);
  if (load.status === "loading") return <p aria-live="polite">Loading the brand…</p>;
  if (load.status === "forbidden") return <p role="alert">You don&apos;t have access to this partner&apos;s brand.</p>;
  if (load.status === "error") return <p role="alert">{load.message}</p>;
  if (ctx === null) return null;
  if (ctx.setup.partner.status === "terminated") return <p role="alert">A terminated partner cannot take a new brand or domain.</p>;
  const wrapped = {
    ...ctx,
    // A saved brand or domain changes the completion: re-read it beside the steps.
    saveBrand: async (groups: Parameters<typeof ctx.saveBrand>[0]) => {
      await ctx.saveBrand(groups);
      bump();
    },
    refresh: async () => {
      await ctx.refresh();
      bump();
    },
  };
  return (
    <>
      {stale && (
        <p role="alert" className="w360-error-text">
          The change was saved, but this page could not be refreshed. Reload the page before making more changes.
        </p>
      )}
      {step === "brand" && <IdentityStep ctx={wrapped} />}
      {step === "domain" && <DomainStep ctx={wrapped} />}
      {step === "communication" && <CommunicationStep ctx={wrapped} />}
      {step === "portal" && <PortalStep ctx={wrapped} />}
    </>
  );
}

