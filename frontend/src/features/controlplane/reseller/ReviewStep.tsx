"use client";

import { useId } from "react";
import type { CapabilityCatalog, ConsoleProduct, ResellerReview, ReviewPricingRow, ReviewTrial } from "@/lib/api/partnerConsole";
import { formatBasisPoints } from "../amounts";
import { countryName } from "../countries";
import { ExperienceLabels } from "../labels";
import type { ContactDraft } from "../profileDraft";
import { SECONDARY_KINDS } from "../profileDraft";
import { money, when } from "../ui";
import styles from "../ControlPlane.module.css";
import local from "../PartnerConsole.module.css";
import cls from "../AddReseller.module.css";
import { billingLabel } from "./PricingStep";
import { STEP_LABELS, stepOfField, type Draft, type ReviewState, type StepKey } from "./draft";

/** Which step fixes each thing the activation rule lists as missing. */
const READINESS_STEP: Record<string, StepKey> = {
  primary_contact: "contacts",
  business_email: "business",
  capabilities: "capabilities",
  products: "products",
  administrator: "administrator",
};

function Section({
  title,
  step,
  onEdit,
  children,
}: {
  title: string;
  step?: StepKey;
  onEdit: (step: StepKey) => void;
  children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section className={cls.section} aria-labelledby={id}>
      <div className={cls.sectionHead}>
        <h3 id={id} className={cls.sectionTitle}>
          {title}
        </h3>
        {step && (
          <button type="button" className="w360-btn w360-btn-secondary" aria-label={`Edit ${title}`} onClick={() => onEdit(step)}>
            Edit
          </button>
        )}
      </div>
      {children}
    </section>
  );
}

/** What nobody has to provide but a complete partner usually has, highlighted so it is noticed. */
function NotSet({ items }: { items: { key: string; label: string }[] }) {
  const id = useId();
  return (
    <section className={cls.notSet} aria-labelledby={id}>
      <h3 id={id} className={cls.sectionTitle}>
        Not set (optional)
      </h3>
      <p className={styles.muted}>
        Nobody has to provide these, but a complete partner usually has them. Add them later from the partner&apos;s page.
      </p>
      <ul className={cls.plain}>
        {items.map((item) => (
          <li key={item.key}>{item.label}</li>
        ))}
      </ul>
    </section>
  );
}

/** Label/value pairs; a pair with no value is left out. */
function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  const shown = rows.filter(([, value]) => value !== undefined && value !== null && value !== "" && value !== false);
  if (shown.length === 0) return null;
  return (
    <dl className={cls.facts}>
      {shown.map(([label, value]) => (
        <div key={label} className={cls.fact}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const contactLine = (c: ContactDraft) =>
  [c.name.trim(), c.jobTitle.trim(), c.email.trim(), c.phone.trim()].filter(Boolean).join(" · ");

const PAYOUT_FREQUENCY: Record<string, string> = { manual: "Manual - when staff prepare one", monthly: "Monthly", quarterly: "Quarterly" };

function trialLine(t: ReviewTrial, plans: Record<string, string>): string {
  if (!t.platform_trial_enabled) return "No trial configured by the platform";
  const names = t.eligible_plan_codes.map((code) => plans[code] ?? code).join(", ");
  const seats = t.seat_limit === null ? "with the plan's included seats" : `up to ${t.seat_limit} ${t.seat_limit === 1 ? "seat" : "seats"}`;
  const terms = `${t.duration_days}-day trial on ${names}, ${seats}`;
  return t.partner_allows
    ? `Customers can start a free trial: ${terms}`
    : `Free trial turned off for this partner's customers (the platform offers a ${terms})`;
}

function priceLine(p: ReviewPricingRow): string {
  const parts = [
    `${p.product_name} — ${p.plan_name} (${billingLabel(p.interval).toLowerCase()}): ${
      p.partner_price_minor === null ? "no price" : money(p.partner_price_minor, p.currency)
    }${p.partner_price_source === "list" ? " (list price)" : ""}`,
  ];
  if (p.partner_price_source === "proposed" && p.list_price_minor !== null) parts.push(`list ${money(p.list_price_minor, p.currency)}`);
  if (p.partner_price_source === "proposed" && p.discount_minor !== null) {
    parts.push(`discount ${money(p.discount_minor, p.currency)} (${formatBasisPoints(p.discount_bp ?? 0)})`);
  }
  if (p.commission_minor !== null) parts.push(`partner margin ${money(p.commission_minor, p.currency)}`);
  if (p.platform_net_minor !== null) parts.push(`platform keeps ${money(p.platform_net_minor, p.currency)}`);
  return parts.join(" · ");
}

/** Step 9: everything the partner will be created with, read back from the
 * server's dry run, and what blocks Save draft, what blocks only Activate, and
 * what is worth a look. */
export function ReviewStep({
  review,
  draft,
  catalog,
  products,
  confirming,
  running,
  busy,
  canActivate,
  onEdit,
  onCancel,
  onConfirm,
  onRetry,
}: {
  review: ReviewState;
  draft: Draft;
  catalog: CapabilityCatalog | null;
  products: ConsoleProduct[];
  confirming: boolean;
  running: boolean;
  busy: boolean;
  canActivate: boolean;
  onEdit: (step: StepKey) => void;
  onCancel: () => void;
  onConfirm: () => void;
  onRetry: () => void;
}) {
  const r: ResellerReview | undefined = review.data;
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

  const shortLabel = (key: string, fallback?: string) => {
    const info = catalog?.results.find((c) => c.key === key);
    return info?.short_label || info?.label || fallback || key;
  };
  const planNames: Record<string, string> = {};
  for (const p of r.products) for (const plan of p.plans) planNames[plan.plan_code] = plan.plan_name;
  const productName = (code: string) => products.find((p) => p.code === code)?.name ?? r.products.find((p) => p.code === code)?.name ?? code;
  const profile = draft.profile;
  const address = [profile.address.line1, profile.address.line2, profile.address.city, profile.address.region, profile.address.postalCode]
    .map((part) => part.trim())
    .filter(Boolean)
    .join(", ");
  const c = r.commission;
  const priced = r.pricing.filter((p) => p.partner_price_source === "proposed");
  const readiness = r.readiness;
  const admin = r.administrator;
  const supportLines = [profile.supportEmail, profile.supportPhone, profile.supportUrl].some((v) => v.trim());

  return (
    <>
      {r.problems.length > 0 && (
        <div role="alert" className="w360-error-text">
          <p>
            The server found {r.problems.length === 1 ? "a problem" : `${r.problems.length} problems`} to fix before anything can be saved.
          </p>
          <ul className={cls.problems}>
            {r.problems.map((p) => {
              const step = stepOfField(p.field);
              return (
                <li key={`${p.field}:${p.message}`}>
                  {p.message}{" "}
                  {step !== "review" && (
                    <button type="button" className={cls.linkButton} onClick={() => onEdit(step)}>
                      Fix this on {STEP_LABELS[step]}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {readiness.missing.length > 0 && (
        <div className={local.warn} role="status">
          <p>
            <strong>Before this partner can be activated:</strong> the items below are missing. A draft can be saved without them.
          </p>
          <ul className={cls.problems}>
            {readiness.missing.map((item) => (
              <li key={item.key}>
                {item.label}: {item.detail}{" "}
                {READINESS_STEP[item.key] && (
                  <button type="button" className={cls.linkButton} onClick={() => onEdit(READINESS_STEP[item.key])}>
                    Go to {STEP_LABELS[READINESS_STEP[item.key]]}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.warnings.length > 0 && (
        <div className={local.warn} role="status">
          <p>Worth a look before you save:</p>
          <ul className={cls.problems}>
            {r.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <Section title="Business details" step="business" onEdit={onEdit}>
        <Facts
          rows={[
            [
              "Name",
              <>
                {r.partner.display_name} <span className={`${styles.muted} ${styles.mono}`}>{r.partner.code}</span>
                <ExperienceLabels labels={r.experience} />
              </>,
            ],
            ["Legal name", profile.legalName.trim()],
            ["Business email", profile.businessEmail.trim()],
            ["Alternative email", profile.alternativeEmail.trim()],
            ["Country", profile.country ? countryName(profile.country) : ""],
            ["Website", profile.website.trim()],
            ["Phone", profile.phone.trim()],
            ["Registration number", profile.registrationNumber.trim()],
            ["Tax number", profile.taxNumber.trim()],
            ["Address", address],
            ["Payout currency", `${draft.currency}: commission and payouts are in this currency and never converted`],
          ]}
        />
      </Section>

      <Section title="Contacts" step="contacts" onEdit={onEdit}>
        <Facts
          rows={[
            ["Primary contact", contactLine(profile.contacts.primary) || "Not set"],
            ...SECONDARY_KINDS.map((kind): [string, React.ReactNode] => [
              `${kind[0].toUpperCase()}${kind.slice(1)} contact`,
              profile.sameAsPrimary[kind] ? "Same as primary contact" : contactLine(profile.contacts[kind]) || "Not set",
            ]),
          ]}
        />
      </Section>

      <Section title="Capabilities" step="capabilities" onEdit={onEdit}>
        {r.capabilities.length === 0 ? (
          <p>None chosen.</p>
        ) : (
          <ul className={cls.plain}>
            {r.capabilities.map((cap) => (
              <li key={cap.key}>{shortLabel(cap.key, cap.label)}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Products and trials" step="products" onEdit={onEdit}>
        {r.products.length === 0 ? (
          <p>
            None allowed. With no product allowed this partner has no right to sell anything, and the server enforces that.
          </p>
        ) : (
          <ul className={cls.plain}>
            {r.products.map((p) => {
              const trial = r.trials.find((t) => t.product_code === p.code);
              return (
                <li key={p.code}>
                  <strong>{p.name}</strong>
                  {p.plans.length > 0 ? ` — plans: ${p.plans.map((pl) => pl.plan_name).join(", ")}` : " — no plans on sale yet"}
                  {trial && <div className={styles.muted}>{trialLine(trial, planNames)}</div>}
                </li>
              );
            })}
          </ul>
        )}
      </Section>

      <Section title="Pricing" step="pricing" onEdit={onEdit}>
        {r.pricing_mode === "standard" ? (
          <p>Standard partner pricing: every plan is sold at its list price and no partner price is created.</p>
        ) : priced.length === 0 ? (
          <p>Custom partner pricing, but no price was typed: every plan uses its list price.</p>
        ) : (
          <>
            <p>Custom partner pricing. Figures are calculated by the server and never change the global list price.</p>
            <ul className={cls.plain}>
              {priced.map((p) => (
                <li key={`${p.plan_version_id}|${p.interval}`}>{priceLine(p)}</li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <Section title="Commission" step="commission" onEdit={onEdit}>
        <Facts
          rows={[
            [
              "Rate",
              c.source === "custom" && c.rate_bp !== null
                ? `${formatBasisPoints(c.rate_bp)} for this partner`
                : c.source === "programme_default" && c.programme_default_bp !== null
                  ? `Programme default, currently ${formatBasisPoints(c.programme_default_bp)}`
                  : "None: there is no programme default rule, so no commission would be earned",
            ],
            ["Method", "A percentage of each sale"],
            c.source === "custom"
              ? ["Applies to", c.scope === "products" ? c.product_codes.map(productName).join(", ") : "All products"]
              : ["Applies to", ""],
            c.source === "custom" ? ["Effective from", c.effective_from ? when(c.effective_from) : "From when it is assigned"] : ["Effective from", ""],
            ["Hold period", `${c.hold_days} ${c.hold_days === 1 ? "day" : "days"} before a commission becomes payable (a platform setting)`],
            [
              "Worked example",
              c.example
                ? `On a customer purchase of ${money(c.example.purchase_minor, c.example.currency)} at ${formatBasisPoints(c.example.rate_bp)}: commission ${money(c.example.commission_minor, c.example.currency)}, platform net ${money(c.example.platform_net_minor, c.example.currency)}`
                : "",
            ],
          ]}
        />
        <p className={styles.muted}>Sales already made keep the rate they were made under: a new rule never changes an old sale.</p>
      </Section>

      <Section title="Payouts" step="commission" onEdit={onEdit}>
        <Facts
          rows={[
            ["Frequency", PAYOUT_FREQUENCY[profile.payoutFrequency] ?? profile.payoutFrequency],
            ["Payout contact email", profile.payoutContactEmail.trim()],
            ["Currency", draft.currency],
          ]}
        />
        <p className={styles.muted}>
          Payouts move Draft → Ready → Approved → Paid, by staff. Nothing transfers automatically and no bank details are collected.
        </p>
      </Section>

      <Section title="Administrator" step="administrator" onEdit={onEdit}>
        {admin === null || !admin.email ? (
          <p>None yet. A partner needs an administrator before it can be activated.</p>
        ) : (
          <Facts
            rows={[
              ["Name", [admin.first_name, admin.last_name].filter(Boolean).join(" ")],
              ["Email", admin.email],
              ["Role", admin.role === "admin" ? "Administrator" : "Viewer"],
              ["Invitation", admin.send_invitation === "now" ? "Sent now" : "Sent when the partner is activated"],
            ]}
          />
        )}
      </Section>

      <Section title="Support" step="administrator" onEdit={onEdit}>
        {supportLines || r.profile.has_internal_notes ? (
          <Facts
            rows={[
              ["Support email", profile.supportEmail.trim()],
              ["Support phone", profile.supportPhone.trim()],
              ["Support page", profile.supportUrl.trim()],
              ["Internal notes", r.profile.has_internal_notes ? "Added (staff only)" : ""],
            ]}
          />
        ) : (
          <p>No support details were given.</p>
        )}
      </Section>

      <Section title="White-label" step="whitelabel" onEdit={onEdit}>
        {r.white_label.requested ? (
          <p>
            Yes.{" "}
            {r.white_label.capabilities_added.length > 0
              ? `The same partner also gets: ${r.white_label.capabilities_added.map((k) => shortLabel(k)).join(" and ")}.`
              : "The partner already holds both white-label capabilities."}{" "}
            You can continue to the white-label setup once it is created.
          </p>
        ) : (
          <p>Not now.</p>
        )}
      </Section>

      {r.missing_optional.length > 0 && <NotSet items={r.missing_optional} />}

      {confirming && (
        <div className={`w360-card ${styles.card}`} role="group" aria-label="Confirm">
          <p>
            <strong>Activate {r.partner.display_name}?</strong> This creates the partner, saves its profile, grants the capabilities
            {r.products.length > 0 ? " and products" : ""} above
            {priced.length > 0 ? ", sets its partner prices" : ""}
            {c.source === "custom" ? ", assigns its commission rate" : ""}
            {admin && admin.email
              ? `, names ${admin.email} as ${admin.role === "admin" ? "administrator" : "viewer"} (the setup link is emailed ${
                  admin.send_invitation === "now" ? "now" : "when the partner is activated"
                })`
              : ""}{" "}
            and then activates it. Activation lets it act: it is recorded in the audit log and may ask for a second factor.
          </p>
          <div className={styles.row}>
            <button type="button" className="w360-btn w360-btn-primary" onClick={onConfirm} disabled={busy || running || !canActivate}>
              {running ? "Activating…" : "Confirm and activate"}
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
