"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { adminApi, type NewProduct, type ProductsConsole } from "@/lib/api/controlPlane";
import { can, useAdmin } from "../ControlShell";
import { LoadGate, describeError, useLoad } from "../ui";
import { DescriptionField, ErrorSummary, ProgressList, Segmented, SelectField, TextField, useStepRunner, type StepDef } from "../widgets";
import { IconPicker } from "./IconPicker";
import { ImagePicker, ScreenshotsField, useDataUrl, type ShotItem } from "./ImagePicker";
import { SLUG_PATTERN, codeFrom, looksLikeHttps, slugFrom } from "./model";
import { StorePreview, type ListingPreview } from "./StorePreview";
import styles from "./ProductManager.module.css";

/**
 * Super Admin → Product Manager → Add Product (ADR-051): four short steps, not
 * one long form. Nothing is written until "Save Draft" or "Publish Product";
 * then the product is created as a hidden draft (with its integration and
 * trial, in one server transaction), its images are uploaded, and - only for
 * "Publish Product" - it is published, which the server refuses unless its
 * checklist is complete. Every rule is enforced again by the server.
 */

export const STEPS = ["Identity", "Store Listing", "Integration", "Availability"] as const;
type Allowed = "allowed" | "not_allowed";

export interface WizardDraft {
  name: string;
  slug: string;
  slugTouched: boolean;
  category: string;
  tagline: string;
  icon: string;
  storeTitle: string;
  storeSummary: string;
  description: string;
  learnMoreUrl: string;
  documentationUrl: string;
  supportUrl: string;
  applicationUrl: string;
  launchUrl: string;
  sso: "yes" | "no";
  redirectUri: string;
  healthUrl: string;
  customer: Allowed;
  reseller: Allowed;
  affiliate: Allowed;
  whiteLabel: Allowed;
  trial: "enabled" | "disabled";
  trialDays: string;
}

export const EMPTY_DRAFT: WizardDraft = {
  name: "",
  slug: "",
  slugTouched: false,
  category: "",
  tagline: "",
  icon: "",
  storeTitle: "",
  storeSummary: "",
  description: "",
  learnMoreUrl: "",
  documentationUrl: "",
  supportUrl: "",
  applicationUrl: "",
  launchUrl: "",
  sso: "yes",
  redirectUri: "",
  healthUrl: "",
  customer: "allowed",
  reseller: "allowed",
  affiliate: "allowed",
  whiteLabel: "allowed",
  trial: "disabled",
  trialDays: "14",
};

type Errors = Partial<Record<string, string>>;

/** The checks for one step. `strict` (Next and Publish) requires the starred
 * fields; a draft needs only a name and a slug, plus valid formats. */
export function validateStep(
  step: number,
  d: WizardDraft,
  ctx: { strict: boolean; hasLogo: boolean; takenSlugs: Set<string>; takenCodes: Set<string> },
): Errors {
  const e: Errors = {};
  const url = (key: keyof WizardDraft, label: string, required: boolean) => {
    const value = String(d[key]).trim();
    if (!value) {
      if (required) e[key] = `Enter the ${label}.`;
    } else if (!looksLikeHttps(value)) e[key] = `The ${label} must be an https:// address.`;
  };
  if (step === 0) {
    if (!d.name.trim()) e.name = "Enter the product name.";
    const slug = d.slug.trim();
    if (!slug) e.slug = "Enter a slug.";
    else if (!SLUG_PATTERN.test(slug)) e.slug = "Use lowercase letters and digits joined by single hyphens, starting with a letter.";
    else if (codeFrom(slug).length > 32) e.slug = "Use at most 32 characters.";
    else if (ctx.takenSlugs.has(slug) || ctx.takenCodes.has(codeFrom(slug))) e.slug = "This slug is already used by another product.";
    if (ctx.strict && !d.category) e.category = "Choose a category.";
    if (ctx.strict && !d.tagline.trim()) e.tagline = "Enter a short description.";
    if (ctx.strict && !ctx.hasLogo) e.logo = "Add the product logo.";
  }
  if (step === 1) {
    url("learnMoreUrl", "Learn More URL", false);
    url("documentationUrl", "documentation URL", false);
    url("supportUrl", "support URL", false);
  }
  if (step === 2) {
    url("applicationUrl", "application URL", ctx.strict);
    url("launchUrl", "launch URL", ctx.strict);
    url("healthUrl", "health check URL", false);
    if (d.sso === "yes" && d.launchUrl.trim()) url("redirectUri", "sign-in callback URL", true);
  }
  if (step === 3 && d.trial === "enabled") {
    const days = Number(d.trialDays);
    if (!/^\d+$/.test(d.trialDays.trim()) || days < 1 || days > 365) e.trialDays = "Enter a trial length from 1 to 365 days.";
  }
  return e;
}

/** The wizard's fields as the create request. Images are uploaded after. */
export function toRequest(d: WizardDraft): NewProduct {
  const launch = d.launchUrl.trim();
  return {
    name: d.name.trim(),
    slug: d.slug.trim(),
    category: d.category,
    tagline: d.tagline.trim(),
    icon: d.icon,
    store_title: d.storeTitle.trim(),
    store_summary: d.storeSummary.trim(),
    description: d.description,
    marketing_url: d.learnMoreUrl.trim(),
    documentation_url: d.documentationUrl.trim(),
    support_url: d.supportUrl.trim(),
    application_url: d.applicationUrl.trim(),
    customer_store_enabled: d.customer === "allowed",
    reseller_enabled: d.reseller === "allowed",
    affiliate_enabled: d.affiliate === "allowed",
    white_label_enabled: d.whiteLabel === "allowed",
    integration: launch
      ? {
          launch_url: launch,
          sso_enabled: d.sso === "yes",
          redirect_uri: d.sso === "yes" ? d.redirectUri.trim() : undefined,
          health_url: d.healthUrl.trim() || undefined,
        }
      : null,
    trial: d.trial === "enabled" ? { enabled: true, duration_days: Number(d.trialDays) } : null,
  };
}

const ALLOWED: { value: Allowed; label: string }[] = [
  { value: "allowed", label: "Allowed" },
  { value: "not_allowed", label: "Not allowed" },
];

export function AddProductWizard() {
  const { permissions } = useAdmin();
  const [load] = useLoad(() => adminApi.productsConsole(), []);
  if (!can(permissions, "platform.staff_catalog.manage")) {
    return <p role="alert">Adding products needs the catalog management permission.</p>;
  }
  return (
    <LoadGate load={load} what="the catalog">
      {(data: ProductsConsole) => <Wizard catalog={data} />}
    </LoadGate>
  );
}

function Wizard({ catalog }: { catalog: ProductsConsole }) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [d, setD] = useState<WizardDraft>(EMPTY_DRAFT);
  const [logo, setLogo] = useState<File | null>(null);
  const [banner, setBanner] = useState<File | null>(null);
  const [shots, setShots] = useState<ShotItem[]>([]);
  const [errors, setErrors] = useState<Errors>({});
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const created = useRef<string | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);
  const runner = useStepRunner();
  const logoPreview = useDataUrl(logo);
  const bannerPreview = useDataUrl(banner);
  const firstRender = useRef(true);

  const takenSlugs = new Set(catalog.products.map((p) => p.slug));
  const takenCodes = new Set(catalog.products.map((p) => p.code));
  const set = <K extends keyof WizardDraft>(key: K, value: WizardDraft[K]) => setD((cur) => ({ ...cur, [key]: value }));

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    heading.current?.focus();
  }, [step]);

  const checkCtx = (strict: boolean) => ({ strict, hasLogo: !!logo, takenSlugs, takenCodes });

  function check(steps: number[], strict: boolean): boolean {
    const found: Errors = {};
    let firstBad: number | null = null;
    for (const s of steps) {
      const e = validateStep(s, d, checkCtx(strict));
      if (Object.keys(e).length > 0 && firstBad === null) firstBad = s;
      Object.assign(found, e);
    }
    setErrors(found);
    if (firstBad !== null) {
      setStep(firstBad);
      requestAnimationFrame(() => summary.current?.focus());
      return false;
    }
    return true;
  }

  async function save(publish: boolean) {
    if (!check([0, 1, 2, 3], publish)) return;
    setFailure(null);
    const defs: StepDef[] = [
      {
        key: "create",
        label: "Create the product (hidden draft)",
        run: async () => {
          const made = await adminApi.createManagedProduct(toRequest(d));
          created.current = made.product.id;
        },
      },
    ];
    const upload = (key: string, label: string, kind: "logo" | "banner" | "screenshot", file: File, caption = "") =>
      defs.push({
        key,
        label,
        run: async () => {
          await adminApi.uploadProductMedia(created.current!, kind, file, {
            caption,
            onProgress: (pct) => setProgress((cur) => ({ ...cur, [key]: pct })),
          });
        },
      });
    if (logo) upload("logo", "Upload the logo", "logo", logo);
    if (banner) upload("banner", "Upload the banner", "banner", banner);
    shots.forEach((s, i) => s.file && upload(s.key, `Upload screenshot ${i + 1}`, "screenshot", s.file, s.caption.trim()));
    if (publish) {
      defs.push({
        key: "publish",
        label: "Publish to the store",
        run: async () => {
          await adminApi.productLifecycle(created.current!, "publish");
        },
      });
    }
    runner.prepare(defs);
    setSaving(true);
    try {
      await runner.runAll(defs);
      router.push(`/admin/products/${created.current}?saved=${publish ? "published" : "draft"}`);
    } catch (err) {
      setFailure(
        created.current
          ? `The product was saved as a draft, but a later step failed: ${describeError(err)} Try again, or finish on the product's page.`
          : describeError(err),
      );
    } finally {
      setSaving(false);
    }
  }

  const preview: ListingPreview = {
    name: d.name,
    icon: d.icon,
    storeTitle: d.storeTitle,
    storeSummary: d.storeSummary,
    tagline: d.tagline,
    category: d.category,
    description: d.description,
    trialDays: d.trial === "enabled" && Number(d.trialDays) > 0 ? Number(d.trialDays) : null,
    logoSrc: logoPreview,
    bannerSrc: bannerPreview,
    shots: shots.map((s) => ({ key: s.key, src: null, file: s.file ?? null, caption: s.caption })),
    learnMoreUrl: d.learnMoreUrl,
    documentationUrl: d.documentationUrl,
    supportUrl: d.supportUrl,
  };
  const errorList = Object.values(errors).filter((v): v is string => !!v);

  return (
    <div className={styles.page}>
      <div>
        <Link className={styles.back} href="/admin/products">
          <span aria-hidden="true">←</span> Product Manager
        </Link>
        <h1 className={styles.pageTitle}>Add Product</h1>
      </div>

      <div className={styles.wizard}>
        <ol className={styles.steps} aria-label="Steps">
          {STEPS.map((label, i) => (
            <li
              key={label}
              className={`${styles.step} ${i === step ? styles.stepCurrent : ""} ${i < step ? styles.stepDone : ""}`}
              aria-current={i === step ? "step" : undefined}
            >
              <span className={styles.stepNumber} aria-hidden="true">
                {i < step ? "✓" : i + 1}
              </span>
              <span className={styles.stepName}>
                <span className="w360-visually-hidden">Step {i + 1}: </span>
                {label}
              </span>
            </li>
          ))}
        </ol>

        <section className={styles.card} aria-labelledby="wizard-step-title">
          <h2 id="wizard-step-title" ref={heading} tabIndex={-1} className={styles.cardTitle}>
            {step + 1}. {STEPS[step]}
          </h2>
          <ErrorSummary errors={errorList} focusRef={summary} />

          {step === 0 && (
            <div className={styles.formGrid}>
              <TextField
                label="Product name"
                showRequired
                required
                maxLength={128}
                placeholder="For example, Easy CRM"
                value={d.name}
                error={errors.name}
                onChange={(v) =>
                  setD((cur) => ({ ...cur, name: v, slug: cur.slugTouched ? cur.slug : slugFrom(v) }))
                }
              />
              <TextField
                label="Product slug"
                showRequired
                required
                maxLength={32}
                placeholder="easy-crm"
                value={d.slug}
                error={errors.slug}
                hint={
                  <>
                    Lowercase, hyphen-separated, unique. Fixed once the product has been on sale.
                    {d.slug && SLUG_PATTERN.test(d.slug) && (
                      <>
                        {" "}
                        Product code: <span className={styles.code}>{codeFrom(d.slug)}</span> (permanent).
                      </>
                    )}
                  </>
                }
                onChange={(v) => setD((cur) => ({ ...cur, slug: v.toLowerCase(), slugTouched: true }))}
              />
              <SelectField
                label="Category"
                showRequired
                required
                value={d.category}
                error={errors.category}
                options={[{ value: "", label: "Choose a category" }, ...catalog.categories.map((c) => ({ value: c, label: c }))]}
                onChange={(v) => set("category", v)}
              />
              <TextField
                label="Short description"
                showRequired
                required
                maxLength={160}
                placeholder="Manage leads, deals and sales pipelines."
                value={d.tagline}
                error={errors.tagline}
                hint={`${d.tagline.length}/160`}
                onChange={(v) => set("tagline", v)}
              />
              <div className={styles.full}>
                <ImagePicker
                  label="Product logo"
                  kind="logo"
                  required
                  file={logo}
                  error={errors.logo}
                  busy={saving}
                  progress={progress.logo ?? null}
                  onSelect={(f) => {
                    setLogo(f);
                    setErrors((cur) => ({ ...cur, logo: undefined }));
                  }}
                  onRemove={() => setLogo(null)}
                />
              </div>
              <div className={styles.full}>
                <IconPicker value={d.icon} onChange={(key) => set("icon", key)} />
              </div>
            </div>
          )}

          {step === 1 && (
            <div className={styles.formGrid}>
              <TextField
                label="Store title"
                maxLength={128}
                placeholder={d.name || "Defaults to the product name"}
                value={d.storeTitle}
                hint="Leave empty to use the product name."
                onChange={(v) => set("storeTitle", v)}
              />
              <TextField
                label="Short store description"
                maxLength={200}
                placeholder={d.tagline || "One line for the store card"}
                value={d.storeSummary}
                hint={`The store card's line. ${d.storeSummary.length}/200`}
                onChange={(v) => set("storeSummary", v)}
              />
              <div className={styles.full}>
                <DescriptionField label="Full description" value={d.description} maxLength={4000} onChange={(v) => set("description", v)} />
              </div>
              <div className={styles.full}>
                <ImagePicker
                  label="Cover / banner image"
                  kind="banner"
                  file={banner}
                  busy={saving}
                  progress={progress.banner ?? null}
                  onSelect={setBanner}
                  onRemove={() => setBanner(null)}
                />
              </div>
              <div className={styles.full}>
                <ScreenshotsField
                  shots={shots.map((s) => ({ ...s, progress: progress[s.key] ?? null }))}
                  busy={saving}
                  onAdd={(files) =>
                    setShots((cur) => [...cur, ...files.map((file) => ({ key: `${Date.now()}-${file.name}-${Math.random()}`, file, caption: "" }))])
                  }
                  onMove={(from, to) =>
                    setShots((cur) => {
                      const next = [...cur];
                      const [moved] = next.splice(from, 1);
                      next.splice(to, 0, moved);
                      return next;
                    })
                  }
                  onRemove={(i) => setShots((cur) => cur.filter((_, n) => n !== i))}
                  onCaption={(i, caption) => setShots((cur) => cur.map((s, n) => (n === i ? { ...s, caption } : s)))}
                />
              </div>
              <TextField label="Learn More URL" type="url" placeholder="https://" value={d.learnMoreUrl} error={errors.learnMoreUrl} onChange={(v) => set("learnMoreUrl", v)} />
              <TextField label="Documentation URL" type="url" placeholder="https://" value={d.documentationUrl} error={errors.documentationUrl} onChange={(v) => set("documentationUrl", v)} />
              <TextField label="Support URL" type="url" placeholder="https://" value={d.supportUrl} error={errors.supportUrl} onChange={(v) => set("supportUrl", v)} />
              <div className={styles.toolbar}>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setPreviewing(true)}>
                  Preview Store Listing
                </button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className={styles.formGrid}>
              <TextField
                label="Application URL"
                showRequired
                type="url"
                placeholder="https://crm.workeasy360.com"
                value={d.applicationUrl}
                error={errors.applicationUrl}
                hint="The product's own address."
                onChange={(v) => set("applicationUrl", v)}
              />
              <TextField
                label="Launch URL"
                showRequired
                type="url"
                placeholder="https://crm.workeasy360.com/"
                value={d.launchUrl}
                error={errors.launchUrl}
                hint="Where an entitled customer goes on “Open product”."
                onChange={(v) => set("launchUrl", v)}
              />
              <Segmented
                legend="SSO enabled"
                options={[
                  { value: "yes", label: "Yes" },
                  { value: "no", label: "No" },
                ]}
                value={d.sso}
                onChange={(v) => set("sso", v)}
                hint={d.sso === "yes" ? "Integration type: OIDC (Workeasy360 sign-in)." : "Integration type: External (the product's own sign-in)."}
              />
              {d.sso === "yes" ? (
                <TextField
                  label="Sign-in callback URL"
                  type="url"
                  placeholder="https://crm.workeasy360.com/api/auth/callback"
                  value={d.redirectUri}
                  error={errors.redirectUri}
                  hint="The exact address Workeasy360 sign-in returns to."
                  onChange={(v) => set("redirectUri", v)}
                />
              ) : (
                <span />
              )}
              <TextField
                label="Health check URL (optional)"
                type="url"
                placeholder="https://crm.workeasy360.com/healthz"
                value={d.healthUrl}
                error={errors.healthUrl}
                onChange={(v) => set("healthUrl", v)}
              />
              <div>
                <span className="w360-label">Integration status</span>
                <p>
                  <span className="w360-badge w360-badge-neutral">Not configured</span>
                </p>
                <p className="w360-hint">Registered as a draft when you save. Credentials are added on the product&apos;s Integration tab, never here.</p>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className={styles.page}>
              <div className={styles.channelGrid}>
                <Segmented legend="Customer Store" options={ALLOWED} value={d.customer} onChange={(v) => set("customer", v)} />
                <Segmented legend="Reseller" options={ALLOWED} value={d.reseller} onChange={(v) => set("reseller", v)} />
                <Segmented legend="Affiliate" options={ALLOWED} value={d.affiliate} onChange={(v) => set("affiliate", v)} />
                <Segmented legend="White-label" options={ALLOWED} value={d.whiteLabel} onChange={(v) => set("whiteLabel", v)} />
              </div>
              <div className={styles.channelGrid}>
                <Segmented
                  legend="Free trial"
                  options={[
                    { value: "enabled", label: "Enabled" },
                    { value: "disabled", label: "Disabled" },
                  ]}
                  value={d.trial}
                  onChange={(v) => set("trial", v)}
                />
                {d.trial === "enabled" ? (
                  <TextField
                    label="Trial duration (days)"
                    inputMode="numeric"
                    value={d.trialDays}
                    error={errors.trialDays}
                    hint="1 to 365 days. Choose eligible plans once the product has plans."
                    onChange={(v) => set("trialDays", v)}
                  />
                ) : (
                  <span />
                )}
              </div>
              <p className={styles.note}>Plans/Pricing required before final Store sale activation.</p>
              <div className={styles.toolbar}>
                <button type="button" className="w360-btn w360-btn-secondary" onClick={() => setPreviewing(true)}>
                  Preview Store Listing
                </button>
              </div>
            </div>
          )}

          <ProgressList steps={runner.steps} title="Saving" />
          {failure && (
            <p className="w360-error-text" role="alert">
              {failure}
            </p>
          )}
        </section>

        <div className={styles.wizardBar}>
          <div className={styles.toolbar}>
            {step > 0 && (
              <button type="button" className="w360-btn w360-btn-secondary" disabled={saving} onClick={() => setStep((s) => s - 1)}>
                Back
              </button>
            )}
            <Link className="w360-btn w360-btn-secondary" href="/admin/products">
              Cancel
            </Link>
          </div>
          <div className={styles.toolbar}>
            <button type="button" className="w360-btn w360-btn-secondary" disabled={saving} onClick={() => void save(false)}>
              Save Draft
            </button>
            {step < STEPS.length - 1 ? (
              <button
                type="button"
                className="w360-btn w360-btn-primary"
                disabled={saving}
                onClick={() => {
                  if (check([step], true)) setStep((s) => s + 1);
                }}
              >
                Next
              </button>
            ) : (
              <button type="button" className="w360-btn w360-btn-primary" disabled={saving} onClick={() => void save(true)}>
                Publish Product
              </button>
            )}
          </div>
        </div>
      </div>
      {previewing && <StorePreview listing={preview} onClose={() => setPreviewing(false)} />}
    </div>
  );
}
