"use client";

import { useEffect, useId, useState } from "react";
import { adminApi, type ConsoleProduct, type ProductChanges, type RolePolicy, type TrialConfig } from "@/lib/api/controlPlane";
import { dialog, FormPopup } from "../Modal";
import { ReasonAction } from "../ui";
import { Segmented } from "../widgets";
import { STATUS_BADGE, STATUS_LABEL, VISIBILITY_BADGE, VISIBILITY_LABEL, visibilityOf } from "./model";
import cp from "../ControlPlane.module.css";
import styles from "./ProductManager.module.css";

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;
type Allowed = "allowed" | "not_allowed";

const ALLOWED: { value: Allowed; label: string }[] = [
  { value: "allowed", label: "Allowed" },
  { value: "not_allowed", label: "Not allowed" },
];

const CHANNELS: { key: keyof Pick<ProductChanges, "customer_store_enabled" | "reseller_enabled" | "affiliate_enabled" | "white_label_enabled">; label: string }[] = [
  { key: "customer_store_enabled", label: "Customer Store" },
  { key: "reseller_enabled", label: "Reseller" },
  { key: "affiliate_enabled", label: "Affiliate" },
  { key: "white_label_enabled", label: "White-label" },
];

const flagsOf = (p: ConsoleProduct): Record<string, Allowed> =>
  Object.fromEntries(CHANNELS.map((c) => [c.key, p[c.key] ? "allowed" : "not_allowed"]));

/** Where the product may be offered: the current eligibility, read-only, and
 * an Edit popup that saves only the flags that changed. */
function ChannelsSection({ product: p, manage, busy, reload }: { product: ConsoleProduct; manage: boolean; busy: boolean; reload: () => void }) {
  const [flags, setFlags] = useState<Record<string, Allowed>>(() => flagsOf(p));
  const changed = CHANNELS.filter((c) => (flags[c.key] === "allowed") !== p[c.key]);

  return (
    <section className={styles.card} aria-labelledby="channels-title">
      <div className={styles.sectionHead}>
        <h2 id="channels-title" className={styles.cardTitle}>
          Where it may be offered
        </h2>
        {manage && (
          <div className={styles.toolbar}>
            <FormPopup
              label="Edit availability"
              title="Where it may be offered"
              intro="Eligibility only: partners still need the product granted, and customers still need an entitlement."
              submitLabel="Save availability"
              buttonClassName="w360-btn w360-btn-secondary"
              disabled={busy}
              canSubmit={changed.length > 0}
              onOpen={() => setFlags(flagsOf(p))}
              onSubmit={async () => {
                const changes: ProductChanges = Object.fromEntries(changed.map((c) => [c.key, flags[c.key] === "allowed"]));
                await adminApi.updateProduct(p.id, changes);
                return "Availability saved.";
              }}
              onDone={reload}
            >
              {CHANNELS.map((c) => (
                <Segmented key={c.key} legend={c.label} options={ALLOWED} value={flags[c.key]} onChange={(v) => setFlags((cur) => ({ ...cur, [c.key]: v }))} />
              ))}
            </FormPopup>
          </div>
        )}
      </div>
      <p className={styles.cardLead}>Eligibility only: partners still need the product granted, and customers still need an entitlement.</p>
      <dl className={styles.facts}>
        {CHANNELS.map((c) => (
          <div key={c.key}>
            <dt>{c.label}</dt>
            <dd>
              <strong>{p[c.key] ? "Allowed" : "Not allowed"}</strong>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** How a product may be trialled: on or off, its length, and the plans a trial
 * may start on (moved from the Phase 11 console; a trial may now be switched
 * on before the product has a plan - nobody can start it until one is chosen).
 * The current set-up reads on the page; Edit opens it in a popup. */
function TrialSection({ product, manage, busy, reload }: { product: ConsoleProduct; manage: boolean; busy: boolean; reload: () => void }) {
  const current = product.channels.trial;
  const [enabled, setEnabled] = useState(current?.enabled ?? false);
  const [days, setDays] = useState(String(current?.duration_days ?? 14));
  const [seatLimit, setSeatLimit] = useState(current?.seat_limit != null ? String(current.seat_limit) : "");
  const [plans, setPlans] = useState<string[]>(current?.eligible_plan_codes ?? []);
  const [repeat, setRepeat] = useState<NonNullable<TrialConfig["repeat_policy"]>>(current?.repeat_policy ?? "never");
  const [cooldown, setCooldown] = useState(String(current?.repeat_after_days ?? 90));
  const [eligibility, setEligibility] = useState<NonNullable<TrialConfig["eligibility"]>>(current?.eligibility ?? "all_organizations");
  const [perPerson, setPerPerson] = useState(current?.one_trial_per_person ?? false);
  const standardPlans = product.plans.filter((p) => p.kind === "standard");
  const ids = { days: useId(), seats: useId(), cooldown: useId() };
  const badDays = !/^\d+$/.test(days.trim()) || Number(days) < 1 || Number(days) > 365;
  const badCooldown = repeat === "after_cooldown" && (!/^\d+$/.test(cooldown.trim()) || Number(cooldown) < 1 || Number(cooldown) > 730);
  const names = Object.fromEntries(product.plans.map((p) => [p.code, p.name]));

  function prefill() {
    setEnabled(current?.enabled ?? false);
    setDays(String(current?.duration_days ?? 14));
    setSeatLimit(current?.seat_limit != null ? String(current.seat_limit) : "");
    setPlans(current?.eligible_plan_codes ?? []);
    setRepeat(current?.repeat_policy ?? "never");
    setCooldown(String(current?.repeat_after_days ?? 90));
    setEligibility(current?.eligibility ?? "all_organizations");
    setPerPerson(current?.one_trial_per_person ?? false);
  }

  return (
    <section className={styles.card} aria-labelledby="trial-title">
      <div className={styles.sectionHead}>
        <h2 id="trial-title" className={styles.cardTitle}>
          Trial
        </h2>
        {manage && (
          <div className={styles.toolbar}>
            <FormPopup
              label="Edit trial"
              title="Trial"
              submitLabel="Save trial configuration"
              buttonClassName="w360-btn w360-btn-secondary"
              disabled={busy}
              canSubmit={!badDays && !badCooldown}
              onOpen={prefill}
              onSubmit={async () => {
                await adminApi.setTrialConfig(product.id, {
                  enabled,
                  duration_days: Number(days),
                  seat_limit: seatLimit.trim() ? Number(seatLimit) : null,
                  payment_method_required: false,
                  eligible_plan_codes: plans,
                  repeat_policy: repeat,
                  repeat_after_days: repeat === "after_cooldown" ? Number(cooldown) : null,
                  eligibility,
                  one_trial_per_person: perPerson,
                });
                return `Trial configuration for ${product.name} saved.`;
              }}
              onDone={reload}
            >
              <div className={dialog.span}>
                <Segmented
                  legend="Free trial"
                  options={[
                    { value: "enabled", label: "Enabled" },
                    { value: "disabled", label: "Disabled" },
                  ]}
                  value={enabled ? "enabled" : "disabled"}
                  onChange={(v) => setEnabled(v === "enabled")}
                />
              </div>
              <div className={cp.field}>
                <label className="w360-label" htmlFor={ids.days}>
                  Trial duration (days)
                </label>
                <input id={ids.days} className="w360-input" type="number" min={1} max={365} required value={days} onChange={(e) => setDays(e.target.value)} />
                {badDays && <p className="w360-error-text">Enter a trial length from 1 to 365 days.</p>}
              </div>
              <div className={cp.field}>
                <label className="w360-label" htmlFor={ids.seats}>
                  Seat limit (empty = the plan&apos;s own included seats)
                </label>
                <input id={ids.seats} className="w360-input" type="number" min={1} value={seatLimit} onChange={(e) => setSeatLimit(e.target.value)} />
              </div>
              <fieldset className={`${cp.field} ${dialog.span}`}>
                <legend className="w360-label">Eligible plans</legend>
                {standardPlans.length === 0 && <p className={styles.muted}>This product has no standard plan yet. Nobody can start a trial until one is chosen here.</p>}
                {standardPlans.map((plan) => (
                  <label key={plan.code} className="w360-label">
                    <input
                      type="checkbox"
                      checked={plans.includes(plan.code)}
                      onChange={(e) => setPlans((cur) => (e.target.checked ? [...cur, plan.code] : cur.filter((c) => c !== plan.code)))}
                    />{" "}
                    {plan.name}
                    {(!plan.is_public || plan.status !== "available" || !plan.priced) && <span className={styles.muted}> (not yet on sale)</span>}
                  </label>
                ))}
              </fieldset>
              <div className={dialog.span}>
                <Segmented
                  legend="Repeat trials"
                  options={[
                    { value: "never", label: "Once per organization" },
                    { value: "after_cooldown", label: "Again after a waiting period" },
                  ]}
                  value={repeat}
                  onChange={(v) => setRepeat(v as typeof repeat)}
                />
              </div>
              {repeat === "after_cooldown" && (
                <div className={cp.field}>
                  <label className="w360-label" htmlFor={ids.cooldown}>
                    Days after a trial ends before another may start
                  </label>
                  <input id={ids.cooldown} className="w360-input" type="number" min={1} max={730} value={cooldown} onChange={(e) => setCooldown(e.target.value)} />
                  {badCooldown && <p className="w360-error-text">Enter 1 to 730 days.</p>}
                </div>
              )}
              <div className={dialog.span}>
                <Segmented
                  legend="Who can start a trial"
                  options={[
                    { value: "all_organizations", label: "Any organization" },
                    { value: "new_customers", label: "Only organizations that never bought it" },
                  ]}
                  value={eligibility}
                  onChange={(v) => setEligibility(v as typeof eligibility)}
                />
              </div>
              <label className={`w360-label ${dialog.span}`}>
                <input type="checkbox" checked={perPerson} onChange={(e) => setPerPerson(e.target.checked)} /> One trial per person across all their
                organizations (a new workspace is not a new trial)
              </label>
            </FormPopup>
          </div>
        )}
      </div>
      <dl className={styles.facts}>
        <div>
          <dt>Free trial</dt>
          <dd>{current?.enabled ? "On" : "Off"}</dd>
        </div>
        {current && (
          <>
            <div>
              <dt>Trial duration</dt>
              <dd>
                {current.duration_days} day{current.duration_days === 1 ? "" : "s"}
              </dd>
            </div>
            <div>
              <dt>Seat limit</dt>
              <dd>{current.seat_limit != null ? current.seat_limit : "The plan's own included seats"}</dd>
            </div>
            <div>
              <dt>Eligible plans</dt>
              <dd>{current.eligible_plan_codes.length ? current.eligible_plan_codes.map((c) => names[c] ?? c).join(", ") : "None chosen yet"}</dd>
            </div>
            <div>
              <dt>Repeat trials</dt>
              <dd>
                {current.repeat_policy === "after_cooldown"
                  ? `Again ${current.repeat_after_days} day${current.repeat_after_days === 1 ? "" : "s"} after the previous one ends`
                  : "Once per organization"}
              </dd>
            </div>
            <div>
              <dt>Who can start one</dt>
              <dd>{current.eligibility === "new_customers" ? "Only organizations that never bought it" : "Any organization"}</dd>
            </div>
            <div>
              <dt>One trial per person</dt>
              <dd>{current.one_trial_per_person ? "Yes" : "No"}</dd>
            </div>
          </>
        )}
      </dl>
    </section>
  );
}

const ORG_ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Administrator",
  billing_manager: "Billing Manager",
  member: "Member",
  readonly: "Read-only",
};
const PRODUCT_ROLE = /^[a-z][a-z0-9_]{1,31}$/;
const FORBIDDEN_ROLE = /super|platform|staff|root|system|w360|workeasy/;

/** ADR-078: the product role each organization role gets when a member signs
 * in to the product (sent in the sign-in token; each product maps it to its own
 * roles). Product-level only - never a platform or staff role. */
function RolePolicySection({ product, manage, busy }: { product: ConsoleProduct; manage: boolean; busy: boolean }) {
  const [policy, setPolicy] = useState<RolePolicy | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [tick, setTick] = useState(0);
  const baseId = useId();

  useEffect(() => {
    let live = true;
    adminApi.rolePolicy(product.id).then(
      (p) => live && setPolicy(p),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [product.id, tick]);

  const roles = policy?.organization_roles ?? [];
  const problems = roles.filter((r) => draft[r] !== undefined && (!PRODUCT_ROLE.test(draft[r]) || FORBIDDEN_ROLE.test(draft[r])));

  return (
    <section className={styles.card} aria-labelledby="roles-title">
      <div className={styles.sectionHead}>
        <h2 id="roles-title" className={styles.cardTitle}>
          Default product roles
        </h2>
        {manage && policy && (
          <div className={styles.toolbar}>
            <FormPopup
              label="Edit roles"
              title="Default product roles"
              submitLabel="Save roles"
              buttonClassName="w360-btn w360-btn-secondary"
              disabled={busy}
              canSubmit={problems.length === 0}
              onOpen={() => setDraft({ ...policy.effective })}
              onSubmit={async () => {
                await adminApi.setRolePolicy(product.id, draft);
                return `Default roles for ${product.name} saved.`;
              }}
              onDone={() => setTick((n) => n + 1)}
            >
              <p className={`${styles.muted} ${dialog.span}`}>
                The role each member gets in {product.name} when they sign in. Use the role names the product knows (for example admin, member, viewer). A
                platform or staff role can never be set here.
              </p>
              {roles.map((role) => (
                <div key={role} className={cp.field}>
                  <label className="w360-label" htmlFor={`${baseId}-${role}`}>
                    {ORG_ROLE_LABEL[role] ?? role}
                  </label>
                  <input
                    id={`${baseId}-${role}`}
                    className="w360-input"
                    value={draft[role] ?? ""}
                    maxLength={32}
                    onChange={(e) => setDraft((d) => ({ ...d, [role]: e.target.value.trim().toLowerCase() }))}
                  />
                  {problems.includes(role) && <p className="w360-error-text">2 to 32 lower-case letters, digits or _, and not a platform or staff role.</p>}
                </div>
              ))}
            </FormPopup>
          </div>
        )}
      </div>
      {failed ? (
        <p className={styles.muted}>The default roles could not be loaded.</p>
      ) : !policy ? (
        <p className={styles.muted} aria-live="polite">
          Loading…
        </p>
      ) : (
        <dl className={styles.facts}>
          {roles.map((role) => (
            <div key={role}>
              <dt>{ORG_ROLE_LABEL[role] ?? role}</dt>
              <dd>
                {policy.effective[role]}
                {policy.custom[role] === undefined && <span className={styles.muted}> (platform default)</span>}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

export function AvailabilityTab({
  product: p,
  manage,
  busy,
  act,
  reload,
}: {
  product: ConsoleProduct;
  manage: boolean;
  busy: boolean;
  act: Act;
  reload: () => void;
}) {
  const visibility = visibilityOf(p.listing);

  return (
    <div className={styles.page}>
      <section className={styles.card} aria-labelledby="lifecycle-title">
        <h2 id="lifecycle-title" className={styles.cardTitle}>
          Status and store visibility
        </h2>
        <dl className={styles.facts}>
          <div>
            <dt>Product status</dt>
            <dd>
              <span className={STATUS_BADGE[p.manager_status]}>{STATUS_LABEL[p.manager_status]}</span>
            </dd>
          </div>
          <div>
            <dt>Store visibility</dt>
            <dd>
              <span className={VISIBILITY_BADGE[visibility]}>{VISIBILITY_LABEL[visibility]}</span>
            </dd>
          </div>
        </dl>
        <p className={styles.muted}>Published means visible in the store. It never gives anyone access: that needs a trial or a purchase.</p>
        {manage && (
          <div className={styles.toolbar}>
            {p.manager_status !== "archived" && visibility !== "published" && (
              <button type="button" className="w360-btn w360-btn-primary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "publish"), `${p.name} is published to the store.`)}>
                Publish
              </button>
            )}
            {visibility === "published" && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "unpublish"), `${p.name} is hidden from the store.`)}>
                Unpublish
              </button>
            )}
            {p.manager_status === "archived" && p.status === "deprecated" && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => void act(() => adminApi.productLifecycle(p.id, "restore"), `${p.name} is active again (hidden).`)}>
                Restore
              </button>
            )}
            {p.manager_status !== "archived" && (
              <ReasonAction
                label="Archive"
                danger
                busy={busy}
                onConfirm={() => act(() => adminApi.productLifecycle(p.id, "archive"), `${p.name} is archived. Its history is kept.`)}
              />
            )}
          </div>
        )}
      </section>

      <ChannelsSection product={p} manage={manage} busy={busy} reload={reload} />
      <TrialSection product={p} manage={manage} busy={busy} reload={reload} />
      <RolePolicySection product={p} manage={manage} busy={busy} />
    </div>
  );
}
