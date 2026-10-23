"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { WhiteLabelApi, WhiteLabelSetup } from "@/lib/api/partnerConsole";
import type { useAction } from "../ui";
import { ErrorSummary } from "../widgets";
import {
  STEPS,
  STEP_LABELS,
  brandingOnDomain,
  type BrandDraft,
  type FieldErrors,
  type Group,
  type StepKey,
} from "./model";
import css from "../WhiteLabelSetup.module.css";

/** What a step needs from the wizard around it. The white-label steps also run
 * inside a partner's Complete Your Profile (ADR-048), whose steps are different:
 * `order` and `labelOf` then name that wizard's steps, and `go` moves within it. */
export interface Nav {
  step: string;
  go: (step: string) => void;
  /** The steps of the wizard this step is in, in order (default: the ten white-label steps). */
  order?: readonly string[];
  /** The visible name of a step (default: the white-label step names). */
  labelOf?: (step: string) => string;
  /** Said to assistive technology (and shown) after the step it came from is gone. */
  announce: (message: string) => void;
  /** True once the person has moved: the new step's heading takes focus. Never on first load. */
  focusHeading: boolean;
}

export interface Ctx {
  partnerId: string;
  setup: WhiteLabelSetup;
  api: WhiteLabelApi;
  draft: BrandDraft;
  patch: (change: Partial<BrandDraft>) => void;
  errors: FieldErrors;
  setErrors: (errors: FieldErrors) => void;
  dirty: (group: Group) => boolean;
  /** A logo the person chose that has passed the local checks and is not saved yet. */
  logo: { file: File; preview: string } | null;
  /** A local data URL, or the same-origin address of the saved logo, or nothing. */
  logoSrc: string | null;
  pickLogo: (file: File | undefined) => Promise<void>;
  /** ``PUT brand`` for these groups (uploading a chosen logo first), then re-read the setup. */
  saveBrand: (groups: Group[]) => Promise<void>;
  /** Re-read the setup (for the new brand version, domain or product facts). */
  refresh: () => Promise<void>;
  nav: Nav;
}

export type ActionHandle = ReturnType<typeof useAction>;

/**
 * The chrome of one step: heading (focused when the step changes), the list of
 * what to fix, the outcome of the step's action, the fields, and the buttons.
 *
 * A step owns its ``useAction`` and hands it here, so the step's own controls and
 * these buttons share one busy state, and a second-factor prompt appears in the
 * step that needs it.
 */
export function StepFrame({
  step,
  nav,
  action,
  intro,
  children,
  validate,
  onErrors,
  save,
  dirty = false,
  saveMessage,
  blocked,
  canContinue = true,
  advance,
  last = false,
  extraActions,
}: {
  step: string;
  nav: Nav;
  action: ActionHandle;
  intro?: React.ReactNode;
  children: React.ReactNode;
  /** What is wrong with the fields, keyed by field. Nothing is saved while any is. */
  validate?: () => FieldErrors;
  onErrors?: (errors: FieldErrors) => void;
  /** Saves this step's fields. Without it the step has nothing to save. */
  save?: () => Promise<unknown>;
  dirty?: boolean;
  saveMessage?: string;
  /** A plain message that stands in for the fields until something else is done. */
  blocked?: React.ReactNode;
  canContinue?: boolean;
  /** Where Continue goes when the step has nothing to save (default: the next step). */
  advance?: () => void;
  last?: boolean;
  extraActions?: React.ReactNode;
}) {
  const order: readonly string[] = nav.order ?? STEPS;
  const index = order.indexOf(step);
  const title = nav.labelOf?.(step) ?? STEP_LABELS[step as StepKey] ?? step;
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);
  const [listed, setListed] = useState<string[]>([]);
  const { act, busy, feedback } = action;
  const message = saveMessage ?? "Saved.";

  useEffect(() => {
    if (nav.focusHeading) heading.current?.focus();
  }, [nav.focusHeading]);
  useEffect(() => {
    if (listed.length > 0) summary.current?.focus();
  }, [listed]);

  function check(): boolean {
    const found = validate?.() ?? {};
    onErrors?.(found);
    const list = Object.values(found).filter((m): m is string => Boolean(m));
    setListed(list);
    return list.length === 0;
  }

  function onContinue() {
    const next = order[index + 1];
    // Only what is about to be saved is checked: a step left as it was saved is not held up.
    if (save && dirty) {
      if (!check()) return;
      void act(async () => {
        await save();
        nav.go(next);
        nav.announce(message);
      }, message);
    } else if (advance) {
      advance();
    } else {
      nav.go(next);
    }
  }

  function onSaveDraft() {
    if (!save || !check()) return;
    void act(async () => {
      await save();
    }, "Draft saved.");
  }

  return (
    <section className={css.step} aria-labelledby={`wl-step-${step}`}>
      <h2 id={`wl-step-${step}`} ref={heading} tabIndex={-1} className={css.stepTitle}>
        Step {index + 1} of {order.length}: {title}
      </h2>
      <ErrorSummary errors={listed} focusRef={summary} />
      {feedback}
      {intro}
      {blocked ?? children}
      <div className={css.nav}>
        {index > 0 && (
          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy} onClick={() => nav.go(order[index - 1])}>
            Back
          </button>
        )}
        {!last && index < order.length - 1 && (
          <button type="button" className="w360-btn w360-btn-primary" disabled={busy || blocked !== undefined || !canContinue} onClick={onContinue}>
            {save ? "Save & continue" : "Continue"}
          </button>
        )}
        {save && blocked === undefined && (
          <button type="button" className="w360-btn w360-btn-secondary" disabled={busy || !dirty} onClick={onSaveDraft}>
            Save draft
          </button>
        )}
        {extraActions}
      </div>
    </section>
  );
}

/** Stands in for a step's fields until the brand exists: portal words belong to
 * a brand, and the brand is made on the Brand identity step. */
export function brandFirst(ctx: Ctx): React.ReactNode | undefined {
  if (ctx.setup.brand !== null) return undefined;
  return (
    <div className={css.callout} role="status">
      <p>Save the brand identity first. These settings belong to the brand, and it does not exist yet.</p>
      <p>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => ctx.nav.go("brand")}>
          Go to Brand identity
        </button>
      </p>
    </div>
  );
}

/** "No" and "Yes" as words: a fact is never carried by colour alone. */
export const yesNo = (value: boolean): string => (value ? "Yes" : "No");

const AUTH_MODE = "workeasy360_secure_sign_in";

/**
 * The honest statement of how customers sign in, from the server (ADR-039): the
 * brand shows on a custom domain, and the credential is never typed there. The
 * server's own sentence is shown as it was written.
 */
export function AuthenticationBox({ setup }: { setup: WhiteLabelSetup }) {
  const titleId = useId();
  const { active, detail } = brandingOnDomain(setup.branding_on_custom_domain);
  const secure = setup.authentication.mode === AUTH_MODE && !setup.authentication.custom_domain_sign_in;
  return (
    <section className={css.statusBox} aria-labelledby={titleId}>
      <h3 id={titleId}>Custom domain and sign-in</h3>
      <p>
        <strong>Custom-domain branding:</strong> {active ? "Active" : "Not active"}
        {detail ? ` (${detail.replace(/\.$/, "")})` : ""}.
      </p>
      {secure && (
        <p>
          <strong>Authentication:</strong> Uses Workeasy360 secure sign-in.
        </p>
      )}
      <p>{setup.authentication.summary}</p>
    </section>
  );
}
