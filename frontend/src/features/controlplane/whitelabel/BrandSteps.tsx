"use client";

import { useId } from "react";
import { PortalPreview, type PreviewData } from "../BrandPreview";
import { useAction } from "../ui";
import { TextField } from "../widgets";
import { safeColor } from "../useBranding";
import { validateGroup } from "./model";
import { StepFrame, type Ctx } from "./shared";
import styles from "../ControlPlane.module.css";
import css from "../WhiteLabelSetup.module.css";

/** What the preview draws: the draft as it stands now, never what was last saved. */
export function previewData(ctx: Ctx): PreviewData {
  const { draft } = ctx;
  return {
    brandName: draft.brandName,
    shortName: draft.shortName,
    accent: draft.accent,
    logoSrc: ctx.logoSrc,
    welcome: draft.welcome,
    footer: draft.footer,
    supportName: draft.supportName,
    supportEmail: draft.supportEmail,
    supportPhone: draft.supportPhone,
    supportUrl: draft.supportUrl,
    websiteUrl: draft.websiteUrl,
    loginShowWelcome: draft.loginShowWelcome,
    loginShowSupport: draft.loginShowSupport,
    products: ctx.setup.products.filter((p) => p.visible_in_portal).map((p) => p.product_name),
  };
}

// --- step 2: brand identity ---------------------------------------------------------------------------

export function IdentityStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const pickerId = useId();
  const { draft, patch, errors, setup } = ctx;

  return (
    <StepFrame
      step="brand"
      nav={ctx.nav}
      action={action}
      intro={
        <p className={css.lead}>
          The name, colour and logo customers see on {setup.partner.display_name}&apos;s portal. Fields marked * are required; everything else is
          optional. Nothing is served until white-label is enabled on the last step.
        </p>
      }
      validate={() => validateGroup(ctx.draft, "identity")}
      onErrors={ctx.setErrors}
      save={() => ctx.saveBrand(["identity"])}
      dirty={ctx.dirty("identity") || ctx.logo !== null}
      saveMessage="Brand identity saved."
    >
      <div className={css.split}>
        <TextField
          label="Brand name"
          showRequired
          required
          value={draft.brandName}
          onChange={(v) => patch({ brandName: v })}
          maxLength={80}
          autoComplete="off"
          error={errors.brandName}
          hint="The portal name customers see."
        />
        <TextField
          label="Short name (optional)"
          value={draft.shortName}
          onChange={(v) => patch({ shortName: v })}
          maxLength={24}
          autoComplete="off"
          error={errors.shortName}
          hint="Up to 24 characters. Shown in the portal header where space is short."
        />
      </div>

      <LogoField ctx={ctx} />

      <div className={css.colourRow}>
        <TextField
          label="Primary colour (optional)"
          value={draft.accent}
          onChange={(v) => patch({ accent: v })}
          maxLength={7}
          spellCheck={false}
          autoComplete="off"
          error={errors.accent}
          hint="A six-digit hex colour such as #1a56db. Leave it empty to keep the Workeasy360 colour."
        />
        <div className={styles.field}>
          <label className="w360-label" htmlFor={pickerId}>
            Pick a colour
          </label>
          <input
            id={pickerId}
            type="color"
            className={css.colourPicker}
            value={(safeColor(draft.accent.trim()) ?? "#1a56db").toLowerCase()}
            onChange={(e) => patch({ accent: e.target.value })}
          />
        </div>
      </div>

      <fieldset className={css.group}>
        <legend>Support details shown to customers (all optional)</legend>
        <div className={css.split}>
          <TextField
            label="Support contact name (optional)"
            value={draft.supportName}
            onChange={(v) => patch({ supportName: v })}
            maxLength={80}
            autoComplete="off"
            error={errors.supportName}
          />
          <TextField
            label="Support email (optional)"
            type="email"
            value={draft.supportEmail}
            onChange={(v) => patch({ supportEmail: v })}
            maxLength={254}
            autoComplete="off"
            error={errors.supportEmail}
          />
          <TextField
            label="Support phone (optional)"
            type="tel"
            value={draft.supportPhone}
            onChange={(v) => patch({ supportPhone: v })}
            maxLength={32}
            autoComplete="off"
            error={errors.supportPhone}
          />
          <TextField
            label="Support page (https, optional)"
            value={draft.supportUrl}
            onChange={(v) => patch({ supportUrl: v })}
            maxLength={300}
            autoComplete="off"
            spellCheck={false}
            error={errors.supportUrl}
          />
        </div>
      </fieldset>

      <TextField
        label="Company website (https, optional)"
        value={draft.websiteUrl}
        onChange={(v) => patch({ websiteUrl: v })}
        maxLength={300}
        autoComplete="off"
        spellCheck={false}
        error={errors.websiteUrl}
      />
    </StepFrame>
  );
}

function LogoField({ ctx }: { ctx: Ctx }) {
  const id = useId();
  const hintId = useId();
  const errorId = useId();
  const { logo, setup, errors } = ctx;
  const saved = Boolean(setup.brand?.logo_file_id);
  return (
    <div className={css.logoField}>
      <label className="w360-label" htmlFor={id}>
        Logo (optional): PNG or JPEG, up to 512 KB
      </label>
      <input
        id={id}
        type="file"
        accept="image/png,image/jpeg"
        aria-describedby={errors.logo ? `${hintId} ${errorId}` : hintId}
        aria-invalid={errors.logo ? true : undefined}
        onChange={(e) => void ctx.pickLogo(e.target.files?.[0])}
      />
      <p id={hintId} className="w360-hint">
        {logo
          ? `${logo.file.name} will be saved with the brand and replaces the saved logo.`
          : saved
            ? "A logo is saved. Choose a file to replace it."
            : "No logo yet. SVG is not accepted."}
      </p>
      {errors.logo && (
        <p id={errorId} className="w360-error-text" role="alert">
          {errors.logo}
        </p>
      )}
      {ctx.logoSrc && (
        // eslint-disable-next-line @next/next/no-img-element -- a local data URL or the same-origin, server-validated logo
        <img className={css.logoThumb} src={ctx.logoSrc} alt="" />
      )}
    </div>
  );
}

// --- step 3: portal appearance ----------------------------------------------------------------------

export function AppearanceStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const unsaved = ctx.dirty("identity") || ctx.dirty("portal") || ctx.dirty("login") || ctx.logo !== null;
  return (
    <StepFrame
      step="appearance"
      nav={ctx.nav}
      action={action}
      intro={
        <p className={css.lead}>
          See the portal the way customers will, in four places. Switch between them with the tabs; the arrow keys move between tabs.
        </p>
      }
    >
      <PortalPreview data={previewData(ctx)} />
      <div className={css.callout}>
        <p>
          The layout and every security prompt stay Workeasy360&apos;s: you choose the name, colour, logo and words, not the page itself.
        </p>
        {unsaved && <p>The preview shows changes that are not saved yet. Save them on the step that edits them, or with Save draft on the last step.</p>}
      </div>
    </StepFrame>
  );
}
