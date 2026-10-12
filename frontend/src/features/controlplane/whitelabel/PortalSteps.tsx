"use client";

import { useId } from "react";
import { PortalPreview } from "../BrandPreview";
import { useAction } from "../ui";
import { TextAreaField, TextField } from "../widgets";
import { validateGroup } from "./model";
import { AuthenticationBox, StepFrame, brandFirst, type Ctx } from "./shared";
import { previewData } from "./BrandSteps";
import css from "../WhiteLabelSetup.module.css";

// --- step 5: the customer portal ----------------------------------------------------------------------

export function CustomerPortalStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const { draft, patch, errors, setup } = ctx;
  const portalName = setup.brand?.brand_name ?? draft.brandName;
  return (
    <StepFrame
      step="portal"
      nav={ctx.nav}
      action={action}
      intro={
        <p className={css.lead}>
          The words customers read in the portal. Both are optional.
        </p>
      }
      blocked={brandFirst(ctx)}
      validate={() => validateGroup(ctx.draft, "portal")}
      onErrors={ctx.setErrors}
      save={() => ctx.saveBrand(["portal"])}
      dirty={ctx.dirty("portal")}
      saveMessage="Customer portal saved."
    >
      <p>
        <strong>Portal name:</strong> {portalName}. The portal name is the brand name; change it on the Brand identity step.
      </p>
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
        hint="Shown at the top of your customers' dashboard, and on the sign-in page unless you turn that off."
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
    </StepFrame>
  );
}

// --- step 6: communication -------------------------------------------------------------------------------

/** The emails a customer might expect, and the fact that only one exists. */
const NOT_BUILT = ["Customer welcome email", "Trial emails", "Subscription emails"];

export function CommunicationStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const invitationHint = useId();
  const { draft, patch, errors } = ctx;
  return (
    <StepFrame
      step="communication"
      nav={ctx.nav}
      action={action}
      intro={
        <div className={css.callout}>
          <p>
            Today there is one email Workeasy360 sends in a partner&apos;s name: the invitation sent to a customer&apos;s owner. It is always sent
            from Workeasy360&apos;s own address, and that cannot be changed. What you set here is the sender name that appears next to it and
            where replies go.
          </p>
        </div>
      }
      blocked={brandFirst(ctx)}
      validate={() => validateGroup(ctx.draft, "communication")}
      onErrors={ctx.setErrors}
      save={() => ctx.saveBrand(["communication"])}
      dirty={ctx.dirty("communication")}
      saveMessage="Communication settings saved."
    >
      <div className={css.split}>
        <TextField
          label="Sender display name (optional)"
          value={draft.senderName}
          onChange={(v) => patch({ senderName: v })}
          maxLength={80}
          autoComplete="off"
          error={errors.senderName}
          hint="Up to 80 characters. If empty, the brand name is used."
        />
        <TextField
          label="Reply-to address (optional)"
          type="email"
          value={draft.replyTo}
          onChange={(v) => patch({ replyTo: v })}
          maxLength={254}
          autoComplete="off"
          error={errors.replyTo}
          hint="Where a reply goes. If empty, the support email is used."
        />
      </div>

      <div>
        <label className={css.check}>
          <input
            type="checkbox"
            checked={draft.useForInvitations}
            aria-describedby={invitationHint}
            onChange={(e) => patch({ useForInvitations: e.target.checked })}
          />
          <span>Use my brand on invitation emails</span>
        </label>
        <p id={invitationHint} className="w360-hint">
          The invitation to a customer&apos;s owner then names the brand, with the sender name and reply-to above. It takes effect once
          white-label is enabled.
        </p>
      </div>

      <section aria-label="Emails that do not exist yet">
        <h3 className={css.subTitle}>Not available yet</h3>
        <ul className={css.notAvailable}>
          {NOT_BUILT.map((name) => (
            <li key={name}>
              {name}: <span className="w360-badge w360-badge-neutral">Not available yet</span>
            </li>
          ))}
        </ul>
        <p className={css.reason}>Nothing is configured for these: Workeasy360 does not send them.</p>
      </section>
    </StepFrame>
  );
}

// --- step 8: the login experience ----------------------------------------------------------------------

export function LoginExperienceStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const { draft, patch } = ctx;
  return (
    <StepFrame
      step="login"
      nav={ctx.nav}
      action={action}
      intro={<p className={css.lead}>Choose what customers see around the sign-in form.</p>}
      blocked={brandFirst(ctx)}
      save={() => ctx.saveBrand(["login"])}
      dirty={ctx.dirty("login")}
      saveMessage="Login experience saved."
    >
      <fieldset className={css.group}>
        <legend>On the sign-in page</legend>
        <label className={css.check}>
          <input type="checkbox" checked={draft.loginShowWelcome} onChange={(e) => patch({ loginShowWelcome: e.target.checked })} />
          <span>Show the welcome message on the sign-in page</span>
        </label>
        <label className={css.check}>
          <input type="checkbox" checked={draft.loginShowSupport} onChange={(e) => patch({ loginShowSupport: e.target.checked })} />
          <span>Show support details on the sign-in page</span>
        </label>
      </fieldset>

      <PortalPreview data={previewData(ctx)} only="login" />

      <div className={css.callout}>
        <p>
          Authentication remains Workeasy360&apos;s own. There is no separate white-label sign-in: customers always sign in on Workeasy360&apos;s
          secure page, which shows this brand.
        </p>
      </div>
      <AuthenticationBox setup={ctx.setup} />
    </StepFrame>
  );
}
