"use client";

import Link from "next/link";
import { partnerConsoleApi } from "@/lib/api/partnerConsole";
import { ExperienceLabels } from "../labels";
import { ReasonAction, Status, useAction } from "../ui";
import { ALL_GROUPS, capabilityWords, validateGroups } from "./model";
import { AuthenticationBox, StepFrame, yesNo, type Ctx } from "./shared";
import css from "../WhiteLabelSetup.module.css";

/** The three labels a partner can wear. Each one is *derived* by the server from
 * the capabilities the partner holds; this list only names the places to show it. */
const KINDS = [
  { key: "reseller", label: "Reseller" },
  { key: "white_label", label: "White-label" },
  { key: "affiliate", label: "Affiliate" },
];

// --- step 9: the reseller connection ----------------------------------------------------------------

export function ConnectionStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const { setup, partnerId } = ctx;
  const worn = (key: string) => setup.experience.find((e) => e.key === key);
  const others = setup.experience.filter((e) => !KINDS.some((k) => k.key === e.key));

  return (
    <StepFrame
      step="connection"
      nav={ctx.nav}
      action={action}
      intro={
        <p className={css.lead}>
          What this partner can do today. These labels are worked out from the capabilities the partner holds: no kind of partner is stored, and
          nothing here changes the account.
        </p>
      }
    >
      <ul className={css.badgeList} aria-label="What the partner can do">
        {KINDS.map(({ key, label }) => {
          // White-label needs both of its capabilities; the server says whether it has them.
          const enabled = key === "white_label" ? setup.enabled : worn(key) !== undefined;
          const missing = key === "white_label" && !enabled && worn(key) ? setup.missing_capabilities : [];
          return (
            <li key={key} className={css.badgeItem}>
              <strong>{worn(key)?.label ?? label}:</strong>
              <span className={enabled ? "w360-badge w360-badge-success" : "w360-badge w360-badge-neutral"}>{enabled ? "Enabled" : "Not enabled"}</span>
              {missing.length > 0 && <span className={css.reason}>Still needs: {missing.map(capabilityWords).join(", ")}.</span>}
            </li>
          );
        })}
        {others.map((e) => (
          <li key={e.key} className={css.badgeItem}>
            <strong>{e.label}:</strong>
            <span className="w360-badge w360-badge-success">Enabled</span>
          </li>
        ))}
      </ul>
      <p>
        <strong>Customers:</strong> {setup.customers}
      </p>
      <p>
        <Link href={`/admin/partners/${partnerId}`}>Change this partner&apos;s capabilities on its page</Link>
      </p>
    </StepFrame>
  );
}

// --- step 10: review -----------------------------------------------------------------------------------------

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={css.factRow}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

const notSet = <span className={css.reason}>Not set</span>;
const text = (value: string) => (value.trim() ? value.trim() : notSet);

export function ReviewStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const { setup, draft, partnerId } = ctx;
  const unsaved = ALL_GROUPS.some((g) => ctx.dirty(g)) || ctx.logo !== null;
  const brandSaved = setup.brand !== null && setup.brand.brand_name.trim() !== "";
  const visible = setup.products.filter((p) => p.visible_in_portal).map((p) => p.product_name);
  const logo = ctx.logo ? "Chosen, not saved yet" : setup.brand?.logo_file_id ? "Saved" : "None";
  const colour = draft.accent.trim();

  return (
    <StepFrame
      step="review"
      nav={ctx.nav}
      action={action}
      last
      intro={<p className={css.lead}>Check everything, then save it as a draft or enable white-label.</p>}
      validate={() => validateGroups(ctx.draft, ALL_GROUPS)}
      onErrors={ctx.setErrors}
      save={() => ctx.saveBrand(ALL_GROUPS)}
      dirty={unsaved}
      extraActions={<Link href={`/admin/partners/${partnerId}`}>Back to the partner</Link>}
    >
      {unsaved && (
        <p className={css.problem} role="status">
          Some of what you see here is not saved yet. Save draft stores it.
        </p>
      )}

      <section className={css.reviewSection} aria-labelledby="wl-review-partner">
        <h3 id="wl-review-partner">Partner</h3>
        <dl className={css.facts}>
          <Row label="Name">{setup.partner.display_name}</Row>
          <Row label="Status">
            <Status value={setup.partner.status} />
          </Row>
          <Row label="Derived labels">
            <ExperienceLabels labels={setup.experience} />
          </Row>
        </dl>
      </section>

      <section className={css.reviewSection} aria-labelledby="wl-review-brand">
        <h3 id="wl-review-brand">Brand</h3>
        <dl className={css.facts}>
          <Row label="Brand name">{text(draft.brandName)}</Row>
          <Row label="Short name">{text(draft.shortName)}</Row>
          <Row label="Logo">{logo}</Row>
          <Row label="Primary colour">{colour ? colour : <span className={css.reason}>Default Workeasy360 colour</span>}</Row>
        </dl>
      </section>

      <section className={css.reviewSection} aria-labelledby="wl-review-support">
        <h3 id="wl-review-support">Support</h3>
        <dl className={css.facts}>
          <Row label="Support contact">{text(draft.supportName)}</Row>
          <Row label="Support email">{text(draft.supportEmail)}</Row>
          <Row label="Support phone">{text(draft.supportPhone)}</Row>
          <Row label="Support page">{text(draft.supportUrl)}</Row>
          <Row label="Company website">{text(draft.websiteUrl)}</Row>
        </dl>
      </section>

      <section className={css.reviewSection} aria-labelledby="wl-review-portal">
        <h3 id="wl-review-portal">Customer portal</h3>
        <dl className={css.facts}>
          <Row label="Welcome message">{text(draft.welcome)}</Row>
          <Row label="Footer text">{text(draft.footer)}</Row>
          <Row label="Products shown">{visible.length > 0 ? visible.join(", ") : <span className={css.reason}>None</span>}</Row>
        </dl>
      </section>

      <section className={css.reviewSection} aria-labelledby="wl-review-comm">
        <h3 id="wl-review-comm">Communication</h3>
        <dl className={css.facts}>
          <Row label="Sender display name">{text(draft.senderName)}</Row>
          <Row label="Reply-to address">{text(draft.replyTo)}</Row>
          <Row label="Brand on invitation emails">{yesNo(draft.useForInvitations)}</Row>
        </dl>
      </section>

      <section className={css.reviewSection} aria-labelledby="wl-review-login">
        <h3 id="wl-review-login">Sign-in page and domain</h3>
        <dl className={css.facts}>
          <Row label="Welcome message on sign-in">{yesNo(draft.loginShowWelcome)}</Row>
          <Row label="Support details on sign-in">{yesNo(draft.loginShowSupport)}</Row>
          <Row label="Custom domain">
            {setup.domain ? (
              <>
                {setup.domain.hostname} <Status value={setup.domain.status} />
              </>
            ) : (
              <span className={css.reason}>None (optional)</span>
            )}
          </Row>
        </dl>
      </section>

      <AuthenticationBox setup={setup} />

      <section className={css.statusBox} aria-labelledby="wl-review-enable">
        <h3 id="wl-review-enable">Enable white-label</h3>
        {setup.enabled ? (
          <p role="status">White-label is enabled for this partner. Saving the brand now updates what its customers see.</p>
        ) : (
          <>
            <p>
              Save draft stores the brand and portal settings and serves nothing. Enabling white-label gives the partner the two capabilities
              that let its brand be served. The minimum is a saved brand name; a custom domain is optional.
            </p>
            {!brandSaved && <p className={css.problem}>Save a brand name first (step 2).</p>}
            {brandSaved && unsaved && <p className={css.problem}>Save draft first, so what you enable is what you reviewed.</p>}
            {brandSaved && !unsaved ? (
              <ReasonAction
                label="Enable white-label"
                confirmLabel="Confirm: enable white-label"
                busy={action.busy}
                onConfirm={(reason) =>
                  void action.act(async () => {
                    await partnerConsoleApi.enableWhiteLabel(partnerId, reason);
                    await ctx.refresh();
                  }, "White-label is enabled.")
                }
              />
            ) : (
              <button type="button" className="w360-btn w360-btn-secondary" disabled>
                Enable white-label
              </button>
            )}
          </>
        )}
      </section>
    </StepFrame>
  );
}
