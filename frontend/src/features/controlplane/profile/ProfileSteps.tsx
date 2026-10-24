"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { adminApi } from "@/lib/api/controlPlane";
import { partnerConsoleApi, type PartnerSummaryView } from "@/lib/api/partnerConsole";
import { PortalPreview } from "../BrandPreview";
import { can, useAdmin } from "../ControlShell";
import { MembersSection } from "../PartnerMembers";
import { BusinessDetailsTab, ContactsTab, PayoutPreferencesTab } from "../PartnerProfileTabs";
import { Section } from "../PartnerSection";
import { CommissionTab, CompletionList, PricingTab, ProductsTab } from "../ResellerTabs";
import type { TabProps } from "../partnerTabs";
import { LoadGate, ReasonAction, Status, Table, useAction, useLoad, when } from "../ui";
import { TextAreaField, TextField } from "../widgets";
import { previewData } from "../whitelabel/BrandSteps";
import { validateGroup } from "../whitelabel/model";
import { StepFrame, brandFirst, type Ctx, type Nav } from "../whitelabel/shared";
import styles from "../ControlPlane.module.css";
import css from "../WhiteLabelSetup.module.css";

/**
 * The step bodies of Complete Your Profile that are not white-label steps. Each
 * is a frame (heading "Step n of m", Back, Continue) around the panels the
 * partner's own page already has - the same commands, the same checks - so the
 * wizard adds no second way to change anything. A panel saves with its own
 * button; Continue only moves on.
 */

export interface StepProps extends TabProps {
  nav: Nav;
  summaryData: PartnerSummaryView;
}

function Frame({ step, nav, intro, children, last = false }: { step: string; nav: Nav; intro?: React.ReactNode; children: React.ReactNode; last?: boolean }) {
  const action = useAction();
  return (
    <StepFrame step={step} nav={nav} action={action} intro={intro} last={last}>
      {children}
    </StepFrame>
  );
}

export function BusinessStep(props: StepProps) {
  return (
    <Frame
      step="business"
      nav={props.nav}
      intro={
        <p className={css.lead}>
          Who the partner is and how to reach it. Required: the primary contact&apos;s name and email, the business email and the country.
          Everything marked optional can be left empty.
        </p>
      }
    >
      <BusinessDetailsTab {...props} />
      <ContactsTab {...props} />
    </Frame>
  );
}

export function ProductsStep(props: StepProps) {
  return (
    <Frame step="products" nav={props.nav}>
      <ProductsTab {...props} />
    </Frame>
  );
}

export function PricingStep(props: StepProps) {
  return (
    <Frame step="pricing" nav={props.nav}>
      <PricingTab {...props} />
    </Frame>
  );
}

export function CommissionStep(props: StepProps) {
  return (
    <Frame
      step="commission"
      nav={props.nav}
      intro={
        <p className={css.lead}>
          The rate this partner earns. A new rate takes effect from the moment you choose - never earlier - and sales already made keep
          the rate they were worked out under. The server calculates every amount.
        </p>
      }
    >
      <CommissionTab {...props} ledger={false} />
    </Frame>
  );
}

// --- capabilities as switches ----------------------------------------------------------------------

const REASON = "Set in the partner's Complete Your Profile";

interface Switch {
  key: string;
  label: string;
  hint: string;
}

/** Capabilities shown as on/off switches. Each change is the ordinary grant or
 * revoke command (audited, second factor), never a separate setting: what the
 * partner may do is exactly the capabilities it holds. */
function CapabilitySwitches({ switches, props, legend }: { switches: Switch[]; props: StepProps; legend: string }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage") && props.partner.status !== "terminated";
  const { act, busy, feedback } = useAction(props.bump);
  const held = new Set(props.summaryData.capabilities.map((c) => c.key));
  return (
    <fieldset className={css.group} aria-busy={busy}>
      <legend>{legend}</legend>
      {feedback}
      {switches.map((s) => {
        const on = held.has(s.key);
        const hint = `${s.key}-hint`;
        return (
          <div key={s.key}>
            <label className={css.check}>
              <input
                type="checkbox"
                checked={on}
                disabled={!manage || busy}
                aria-describedby={hint}
                onChange={() =>
                  act(
                    () => (on ? adminApi.revokeCapability(props.partner.id, s.key, REASON) : adminApi.grantCapability(props.partner.id, s.key, REASON)),
                    on ? `${s.label}: turned off.` : `${s.label}: turned on.`,
                  )
                }
              />
              <span>{s.label}</span>
            </label>
            <p id={hint} className="w360-hint">
              {s.hint}
            </p>
          </div>
        );
      })}
    </fieldset>
  );
}

const SALES_SWITCHES: Switch[] = [
  { key: "customer.provision", label: "May create customers", hint: "Create a customer organization and invite its owner." },
  { key: "customer.manage", label: "May manage customers", hint: "Manage the commercial setup of its own customers." },
  { key: "subscription.sell", label: "May sell products", hint: "Sell the products allowed on the Products step to its customers." },
  {
    key: "subscription.manage",
    label: "May manage subscriptions and renewals",
    hint: "Change, cancel and reactivate its customers' subscriptions. Renewals follow the subscription; there is no separate renewal switch.",
  },
  { key: "pricing.view", label: "May view pricing", hint: "See the prices approved for this partner." },
];

export function SalesStep(props: StepProps) {
  return (
    <Frame
      step="sales"
      nav={props.nav}
      intro={
        <p className={css.lead}>
          What this partner may do for its customers. Each switch is a capability: turning one off takes the power away on the partner&apos;s
          next request.
        </p>
      }
    >
      <CapabilitySwitches props={props} legend="Customer and sales permissions" switches={SALES_SWITCHES} />
    </Frame>
  );
}

export function SubResellerStep(props: StepProps) {
  return (
    <Frame step="subresellers" nav={props.nav}>
      <CapabilitySwitches
        props={props}
        legend="Sub-resellers"
        switches={[
          {
            key: "subpartner.manage",
            label: "May create sub-resellers",
            hint: "Create and manage sub-partners under this partner.",
          },
        ]}
      />
      <div className={css.callout}>
        <p>
          A reseller can give a sub-reseller only capabilities it holds itself and only products it is allowed to sell. It can never give
          commission or payouts (Workeasy360 decides who earns), custom domains, or the right to create sub-resellers: the hierarchy is one
          level deep.
        </p>
      </div>
    </Frame>
  );
}

// --- the referral program ----------------------------------------------------------------------------

export function ReferralStep(props: StepProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage") && props.partner.status !== "terminated";
  const [load, reload] = useLoad(() => partnerConsoleApi.referrals(props.partnerId), [props.partnerId, props.rev]);
  const [label, setLabel] = useState("");
  const [copied, setCopied] = useState("");
  const { act, busy, feedback } = useAction(() => {
    reload();
    props.bump();
    setLabel("");
  });
  return (
    <Frame
      step="referral"
      nav={props.nav}
      intro={
        <p className={css.lead}>
          An affiliate shares a referral link; a visitor who signs up through it is attributed to the affiliate. Give it at least one code -
          one per campaign if it runs several. The partner can add its own codes after signing in.
        </p>
      }
    >
      {feedback}
      <LoadGate load={load} what="the referral program">
        {(d) => (
          <div className={styles.stack}>
            <p>
              <strong>Attribution window:</strong> {d.attribution_window_days} days. A referred visit counts if the customer signs up within that
              time; the first referral link used is the one that counts. The window is the platform&apos;s, the same for every affiliate.
            </p>
            <Table caption="Referral codes" head={["Code", "Campaign", "Referral link", "Status", "Created"]} isEmpty={d.codes.length === 0} empty="No referral code yet.">
              {d.codes.map((c) => (
                <tr key={c.id}>
                  <th scope="row" className={styles.mono}>
                    {c.code}
                  </th>
                  <td>{c.label || "—"}</td>
                  <td>
                    <span className={styles.mono}>{c.url}</span>{" "}
                    <button
                      type="button"
                      className="w360-btn w360-btn-secondary"
                      aria-label={`Copy link for ${c.code}`}
                      onClick={() => {
                        void navigator.clipboard?.writeText(c.url).then(
                          () => setCopied(c.code),
                          () => setCopied(""),
                        );
                      }}
                    >
                      Copy link
                    </button>
                  </td>
                  <td>
                    <Status value={c.status} />
                  </td>
                  <td>{when(c.created_at)}</td>
                </tr>
              ))}
            </Table>
            <p role="status" aria-live="polite" className={styles.muted}>
              {copied ? `Link for ${copied} copied.` : ""}
            </p>
            {manage && d.can_create ? (
              <form
                className={styles.form}
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(() => partnerConsoleApi.createReferralCode(props.partnerId, label.trim()), "Referral code created.");
                }}
              >
                <TextField
                  label="Campaign name (optional)"
                  value={label}
                  onChange={setLabel}
                  maxLength={64}
                  autoComplete="off"
                  hint="Shown beside the code, to tell campaigns apart. Plain text."
                />
                <div>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy}>
                    Create referral code
                  </button>
                </div>
              </form>
            ) : (
              !d.can_create && <p className={styles.muted}>This partner does not hold the referral capability, so it cannot have a code.</p>
            )}
          </div>
        )}
      </LoadGate>
    </Frame>
  );
}

export function PayoutStep(props: StepProps) {
  return (
    <Frame step="payout" nav={props.nav}>
      <PayoutPreferencesTab {...props} />
    </Frame>
  );
}

export function MarketingStep(props: StepProps) {
  return (
    <Frame
      step="marketing"
      nav={props.nav}
      intro={<p className={css.lead}>How the affiliate presents itself. Its website and support details are part of its profile.</p>}
    >
      <div className={css.callout}>
        <p>
          The website, phone and support details are edited on the{" "}
          <button type="button" className="w360-btn w360-btn-secondary" onClick={() => props.nav.go("business")}>
            Profile step
          </button>
          .
        </p>
        <p>
          A marketing library (banners, brand assets, promotion codes) is not part of the platform yet: the affiliate shares its referral
          links. Nothing is configured here.
        </p>
      </div>
    </Frame>
  );
}

export function TeamStep(props: StepProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const { act, busy, feedback } = useAction(props.bump);
  return (
    <Frame
      step="team"
      nav={props.nav}
      intro={
        <p className={css.lead}>
          Who signs in for this partner. The administrator created with the partner is here; add the rest of the team, or let the
          administrator invite them after signing in.
        </p>
      }
    >
      {feedback}
      <MembersSection partner={props.partner} manage={manage} act={act} busy={busy} />
    </Frame>
  );
}

export function ReviewStep(props: StepProps) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const { act, busy, feedback } = useAction(props.bump);
  const c = props.summaryData.profile_completion;
  const p = props.partner;
  return (
    <Frame step="review" nav={props.nav} last>
      {feedback}
      <Section title={c.complete ? "Profile complete" : "Profile incomplete"}>
        <p>
          <strong>{c.percent}% complete</strong>
          <span className={styles.muted}>
            {" "}
            · {c.done} of {c.required} required sections
          </span>
        </p>
        <CompletionList sections={c.sections} partnerId={p.id} onStep={{ go: props.nav.go, steps: props.nav.order ?? [] }} />
      </Section>
      {p.status === "draft" && manage && (
        <Section title="Activate">
          {c.complete ? (
            <>
              <p>
                Everything required is in place. Activating lets the partner&apos;s administrator sign in to the partner portal and use what
                its capabilities allow.
              </p>
              <ReasonAction label="Activate partner" busy={busy} onConfirm={(r) => act(() => adminApi.setPartnerStatus(p.id, "active", r, p.version), "Partner activated.")} />
            </>
          ) : (
            <p className={styles.muted}>The partner can be activated once every required section is complete.</p>
          )}
        </Section>
      )}
      {p.status !== "draft" && (
        <p>
          Status: <Status value={p.status} />
        </p>
      )}
      <p>
        <Link href={`/admin/partners/${p.id}`}>Back to the partner&apos;s page</Link>
      </p>
    </Frame>
  );
}

// --- the white-label customer portal (optional) --------------------------------------------------------

/** The words customers read in the portal and what the sign-in page shows,
 * with the preview: the brand's ``portal`` and ``login`` groups, saved together. */
export function PortalStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const id = useId();
  const { draft, patch, errors } = ctx;
  return (
    <StepFrame
      step="portal"
      nav={ctx.nav}
      action={action}
      intro={<p className={css.lead}>Optional. The words customers read in the portal, what the sign-in page shows, and a preview.</p>}
      blocked={brandFirst(ctx)}
      validate={() => validateGroup(ctx.draft, "portal")}
      onErrors={ctx.setErrors}
      save={() => ctx.saveBrand(["portal", "login"])}
      dirty={ctx.dirty("portal") || ctx.dirty("login")}
      saveMessage="Customer portal saved."
    >
      <div className={css.callout}>
        <p>Plain text only. Angle brackets (&lt; and &gt;) are refused, and each message is a single line.</p>
      </div>
      <TextAreaField
        label="Welcome message (optional)"
        value={draft.welcome}
        onChange={(v) => patch({ welcome: v })}
        maxLength={280}
        rows={3}
        error={errors.welcome}
        hint="Shown at the top of the customers' dashboard, and on the sign-in page unless turned off below."
      />
      <TextAreaField
        label="Footer text (optional)"
        value={draft.footer}
        onChange={(v) => patch({ footer: v })}
        maxLength={160}
        rows={2}
        error={errors.footer}
        hint="Shown at the bottom of the portal."
      />
      <fieldset className={css.group} aria-describedby={id}>
        <legend>On the sign-in page</legend>
        <label className={css.check}>
          <input type="checkbox" checked={draft.loginShowWelcome} onChange={(e) => patch({ loginShowWelcome: e.target.checked })} />
          <span>Show the welcome message</span>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={draft.loginShowSupport} onChange={(e) => patch({ loginShowSupport: e.target.checked })} />
          <span>Show support details</span>
        </label>
        <p id={id} className="w360-hint">
          The sign-in form itself is always Workeasy360&apos;s secure sign-in.
        </p>
      </fieldset>
      <PortalPreview data={previewData(ctx)} />
    </StepFrame>
  );
}
