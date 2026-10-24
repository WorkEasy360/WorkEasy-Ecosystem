"use client";

import { useId } from "react";
import { useAction } from "../ui";
import { TextField } from "../widgets";
import { validateGroup } from "./model";
import { StepFrame, brandFirst, type Ctx } from "./shared";
import css from "../WhiteLabelSetup.module.css";

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
