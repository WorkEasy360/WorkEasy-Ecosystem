"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { adminApi, productMediaUrl, type PublisherBootstrap, type PublisherState } from "@/lib/api/controlPlane";
import { ApiError } from "@/lib/api/client";
import { DescriptionField } from "@/features/controlplane/widgets";
import { Modal, useClosing } from "@/features/controlplane/Modal";
import { describeError } from "@/features/controlplane/ui";
import { ImagePicker } from "../ImagePicker";
import { VideoPicker } from "../VideoPicker";
import { looksLikeVideo } from "../model";
import { PlanEditor } from "./PlanEditor";
import { ScreenshotManager } from "./ScreenshotManager";
import { StorePagePreview } from "./StorePagePreview";
import {
  LIMITS,
  blankDraft,
  check,
  effectiveSlug,
  emptySlot,
  fieldKey,
  fromState,
  toRequest,
  type ImageSlot,
  type Problems,
  type PublisherDraft,
} from "./model";
import styles from "./Publisher.module.css";

type Step = { label: string; done: boolean };
type Action = "draft" | "publish" | "preview";

/**
 * The one-page Product Publisher (ADR-068), in a popup that opens and closes
 * smoothly: Basic information, Product details & media, Plans & pricing, and
 * Advanced settings (closed), with Cancel | Save Draft | Preview | Publish to
 * Store always at the bottom. One save writes the product and its plans
 * together - the plans are the Plans section's own records - then uploads the
 * staged images and video; Publish then puts the product and its visible,
 * priced plans on sale, if the server's publish checklist agrees.
 */
export function ProductPublisher({
  productId: initialId,
  takenSlugs,
  onClose,
  onSaved,
}: {
  /** The product to edit, or null for Add Product. */
  productId: string | null;
  /** Slugs and codes other products hold (checked again by the server). */
  takenSlugs: Set<string>;
  onClose: () => void;
  /** After a save or a publish (the list refreshes). */
  onSaved?: (productId: string, published: boolean) => void;
}) {
  const titleId = useId();
  const { closing, close } = useClosing(onClose);
  const [boot, setBoot] = useState<PublisherBootstrap | null>(null);
  const [state, setState] = useState<PublisherState | null>(null);
  const [draft, setDraft] = useState<PublisherDraft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problems, setProblems] = useState<Problems>({});
  const [busy, setBusy] = useState<Action | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof adminApi.storePreview>> | null>(null);
  const [dirty, setDirty] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const body = useRef<HTMLDivElement | null>(null);
  const inFlight = useRef(false);

  const mediaUrl = useCallback((pid: string) => (mediaId: string) => productMediaUrl(pid, mediaId), []);

  useEffect(() => {
    let live = true;
    const load = initialId ? adminApi.publisherState(initialId) : adminApi.publisherBootstrap();
    load.then(
      (result) => {
        if (!live) return;
        setBoot(result);
        if (initialId) {
          const full = result as PublisherState;
          setState(full);
          setDraft(fromState(full, mediaUrl(full.product.id)));
        } else {
          setDraft(blankDraft(result.books[0]?.id ?? ""));
        }
      },
      (err) => live && setLoadError(describeError(err)),
    );
    return () => {
      live = false;
    };
  }, [initialId, mediaUrl]);

  const edit = useCallback((patch: Partial<PublisherDraft>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setDirty(true);
  }, []);

  const books = useMemo(() => boot?.books ?? [], [boot]);
  const productId = draft?.productId ?? null;
  const live = state?.product.manager_status === "active";
  const canCatalog = boot?.can_manage.catalog ?? false;
  const canPrice = boot?.can_manage.pricing ?? false;

  function requestClose() {
    if (busy) return;
    if (dirty) setConfirmLeave(true);
    else close();
  }

  /** Scroll the page's own body (never the sheet or the page behind it) so
   * that `target` is in view - `offset` pixels below the body's top. */
  function scrollBodyTo(target: HTMLElement, offset = 12) {
    const box = body.current;
    if (!box) return;
    const top = box.scrollTop + target.getBoundingClientRect().top - box.getBoundingClientRect().top - offset;
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (typeof box.scrollTo === "function") box.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
    else box.scrollTop = Math.max(0, top);
  }

  function focusFirstProblem(found: Problems) {
    const first = Object.keys(found)[0];
    if (!first) return;
    window.requestAnimationFrame(() => {
      const target = body.current?.querySelector<HTMLElement>('[aria-invalid="true"], [role="alert"]');
      if (!target) return;
      scrollBodyTo(target, 120);
      target.focus({ preventScroll: true });
    });
  }

  function jump(event: React.MouseEvent<HTMLAnchorElement>, id: string) {
    const target = body.current?.querySelector<HTMLElement>(`#${id}`);
    if (!target) return;
    event.preventDefault();
    scrollBodyTo(target);
    target.querySelector<HTMLElement>("h3")?.focus({ preventScroll: true });
  }

  async function uploadSlot(pid: string, kind: "logo" | "banner" | "video_poster", slot: ImageSlot, key: "logo" | "banner" | "poster", mark: (label: string) => void) {
    if (slot.removed && slot.mediaId && !slot.file) {
      await adminApi.deleteProductMedia(pid, slot.mediaId);
      mark(`${kind === "video_poster" ? "Video thumbnail" : kind === "logo" ? "Logo" : "Cover image"} removed`);
    }
    if (slot.file) {
      await adminApi.uploadProductMedia(pid, kind, slot.file, {
        onProgress: (percent) => setDraft((d) => (d ? { ...d, [key]: { ...d[key], progress: percent } } : d)),
      });
      mark(`${kind === "video_poster" ? "Video thumbnail" : kind === "logo" ? "Logo" : "Cover image"} uploaded`);
    }
  }

  /** Save the page (and, for `publish`, publish it). Every write is the
   * server's own; nothing is retried behind the person's back. */
  async function persist(action: Action) {
    if (!draft || inFlight.current) return;
    const found = check(draft, books, { publish: action === "publish", takenSlugs });
    setProblems(found);
    setMessage(null);
    if (Object.keys(found).length > 0) {
      setMessage({ kind: "error", text: action === "publish" ? "Fix the highlighted fields to publish." : "Fix the highlighted fields to save." });
      focusFirstProblem(found);
      return;
    }
    inFlight.current = true;
    setBusy(action);
    const done: Step[] = [];
    const mark = (label: string) => {
      done.push({ label, done: true });
      setSteps([...done]);
    };
    setSteps([]);
    let pid = draft.productId;
    try {
      const request = toRequest(draft, books);
      const saved = pid ? await adminApi.saveFromPublisher(pid, request) : await adminApi.createFromPublisher(request);
      pid = saved.product.id;
      mark(draft.productId ? "Product and plans saved" : "Product and plans created");
      // The product exists now: a retry of anything below updates it, never a second one.
      setDraft((d) => (d ? { ...d, productId: saved.product.id, plans: fromState(saved, mediaUrl(saved.product.id)).plans.map((p, i) => ({ ...p, advancedOpen: d.plans[i]?.advancedOpen ?? false })) } : d));

      await uploadSlot(pid, "logo", draft.logo, "logo", mark);
      await uploadSlot(pid, "banner", draft.banner, "banner", mark);
      for (const mediaId of draft.removedShots) {
        await adminApi.deleteProductMedia(pid, mediaId);
      }
      const order: string[] = [];
      for (const shot of draft.shots) {
        if (shot.mediaId) {
          order.push(shot.mediaId);
          if (shot.caption.trim() !== shot.savedCaption) await adminApi.captionProductMedia(pid, shot.mediaId, shot.caption.trim());
          continue;
        }
        if (!shot.file) continue;
        const row = await adminApi.uploadProductMedia(pid, "screenshot", shot.file, {
          caption: shot.caption.trim(),
          onProgress: (percent) => setDraft((d) => (d ? { ...d, shots: d.shots.map((s) => (s.key === shot.key ? { ...s, progress: percent } : s)) } : d)),
        });
        order.push(row.id);
      }
      if (draft.shots.length || draft.removedShots.length) mark("Screenshots saved");
      // The server keeps stored screenshots in their order and appends uploads;
      // reorder only when the page's order is something else.
      const stored = (saved.product.media ?? [])
        .filter((m) => m.kind === "screenshot" && order.includes(m.id))
        .sort((a, b) => a.sort_order - b.sort_order)
        .map((m) => m.id);
      const natural = [...stored, ...order.filter((mid) => !stored.includes(mid))];
      if (order.length > 1 && order.join() !== natural.join()) {
        await adminApi.reorderProductMedia(pid, order);
      }
      if (draft.videoMode === "upload" && draft.videoFile) {
        await adminApi.uploadProductMedia(pid, "video", draft.videoFile, {
          onProgress: (percent) => setDraft((d) => (d ? { ...d, videoProgress: percent } : d)),
        });
        mark("Video uploaded");
      } else if (draft.videoMode !== "upload" && draft.videoStored) {
        await adminApi.deleteProductMedia(pid, draft.videoStored.id);
      }
      if (draft.videoMode !== "none") await uploadSlot(pid, "video_poster", draft.poster, "poster", mark);
      else if (draft.poster.mediaId) await adminApi.deleteProductMedia(pid, draft.poster.mediaId);

      let latest: PublisherState;
      if (action === "publish") {
        latest = await adminApi.publishFromPublisher(pid);
        mark("Published to the Store");
      } else {
        latest = await adminApi.publisherState(pid);
      }
      setState(latest);
      setBoot(latest);
      setDraft(fromState(latest, mediaUrl(latest.product.id)));
      setDirty(false);
      setProblems({});
      onSaved?.(pid, action === "publish");
      if (action === "preview") {
        setPreview(await adminApi.storePreview(pid));
        setMessage({ kind: "ok", text: "Saved as a draft. This preview is visible to staff only." });
      } else {
        setMessage({
          kind: "ok",
          text:
            action === "publish"
              ? "Published. The product and its plans are now in the Digital Store and in Products & Pricing > Plans."
              : latest.product.manager_status === "active"
                ? "Saved. The changes are live in the Store; new prices apply to new purchases."
                : "Draft saved. It stays private until you publish it.",
        });
      }
    } catch (err) {
      const field = err instanceof ApiError ? fieldKey((err.body as { field?: string } | null)?.field) : null;
      const text = describeError(err);
      if (field) {
        const next = { [field]: text };
        setProblems(next);
        focusFirstProblem(next);
      }
      setMessage({
        kind: "error",
        text: pid && pid !== draft.productId ? `The product was saved as a draft, but a later step failed: ${text}` : text,
      });
      if (pid) {
        // Keep the page in step with what the server now holds.
        adminApi.publisherState(pid).then(
          (latest) => {
            setState(latest);
            setDraft((d) => (d ? { ...d, productId: latest.product.id } : d));
          },
          () => undefined,
        );
      }
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  }

  return (
    <Modal labelledBy={titleId} onClose={requestClose} size="xl" closing={closing}>
      <header className={styles.head}>
        <div>
          <h2 id={titleId} className={styles.title}>
            {initialId ? `Edit ${state?.product.name ?? "product"}` : "Add Product"}
          </h2>
          <p className={styles.subtitle}>Create your product, configure pricing, and publish it to the Digital Store.</p>
        </div>
        <div className={styles.headRight}>
          {state && <span className={`w360-badge ${live ? "w360-badge-success" : "w360-badge-neutral"}`}>{live ? "Live in Store" : "Draft"}</span>}
          <button type="button" className={styles.close} aria-label="Close" onClick={requestClose} disabled={busy !== null}>
            <span aria-hidden="true">×</span>
          </button>
        </div>
      </header>

      <div className={styles.frame}>
        <nav className={styles.sideNav} aria-label="Sections">
          {(
            [
              ["publisher-basic", "Basic information"],
              ["publisher-details", "Product details & media"],
              ["publisher-plans", "Plans & pricing"],
              ["publisher-advanced", "Advanced settings"],
            ] as const
          ).map(([id, label]) => (
            <a key={id} href={`#${id}`} onClick={(e) => jump(e, id)}>
              {label}
            </a>
          ))}
        </nav>
        <div ref={body} className={styles.body}>
          {loadError && (
            <p className="w360-error-text" role="alert">
              {loadError}
            </p>
          )}
          {!draft && !loadError && <p className={styles.loading}>Loading…</p>}
          {draft && boot && (
            <>
              {!canCatalog && (
                <p className={styles.notice} role="note">
                  You can view this page; saving needs the catalog management permission.
                </p>
              )}
              <BasicSection draft={draft} boot={boot} problems={problems} busy={busy !== null} edit={edit} productId={productId} />
              <DetailsSection draft={draft} problems={problems} busy={busy !== null} edit={edit} productId={productId} />
              <section id="publisher-plans" className={styles.section} aria-labelledby="publisher-plans-title">
                <h3 id="publisher-plans-title" className={styles.sectionTitle} tabIndex={-1}>
                  <span className={styles.sectionNumber}>3</span> Plans &amp; pricing
                </h3>
                <p className={styles.sectionLead}>
                  Each plan is saved as a plan of this product in Products &amp; Pricing &gt; Plans - one record, edited from either place.
                  {state && state.add_on_count > 0 ? ` Its ${state.add_on_count === 1 ? "add-on stays" : `${state.add_on_count} add-ons stay`} in Plans.` : ""}
                </p>
                <PlanEditor
                  plans={draft.plans}
                  books={books}
                  problems={problems}
                  maxPlans={boot.max_plans}
                  productCode={state?.product.code ?? null}
                  canPrice={canPrice}
                  busy={busy !== null}
                  onChange={(plans) => edit({ plans })}
                />
              </section>
              <AdvancedSection draft={draft} problems={problems} busy={busy !== null} edit={edit} />
            </>
          )}
        </div>
      </div>

      <footer className={styles.foot}>
        <div className={styles.footStatus} aria-live="polite">
          {busy && (
            <span className={styles.working}>
              <span className={styles.spinner} aria-hidden="true" />
              {busy === "publish" ? "Publishing…" : busy === "preview" ? "Preparing the preview…" : "Saving…"}
              {steps.length > 0 && ` ${steps[steps.length - 1].label}.`}
            </span>
          )}
          {!busy && message && <span className={message.kind === "ok" ? styles.ok : styles.bad}>{message.text}</span>}
        </div>
        <div className={styles.footActions}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={requestClose} disabled={busy !== null}>
            Cancel
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={!draft || busy !== null || !canCatalog} onClick={() => void persist("draft")}>
            {live ? "Save Changes" : "Save Draft"}
          </button>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={!draft || busy !== null || !canCatalog} onClick={() => void persist("preview")}>
            Preview
          </button>
          <button type="button" className="w360-btn w360-btn-primary" disabled={!draft || busy !== null || !canCatalog} onClick={() => void persist("publish")}>
            {live ? "Update Store" : "Publish to Store"}
          </button>
        </div>
      </footer>

      {preview && <StorePagePreview data={preview} onClose={() => setPreview(null)} />}
      {confirmLeave && (
        <LeaveConfirm
          onStay={() => setConfirmLeave(false)}
          onLeave={() => {
            setConfirmLeave(false);
            close();
          }}
        />
      )}
    </Modal>
  );
}

function Field({ label, htmlFor, required, hint, error, children, wide }: { label: string; htmlFor: string; required?: boolean; hint?: React.ReactNode; error?: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`${styles.field} ${wide ? styles.span2 : ""}`}>
      <label className="w360-label" htmlFor={htmlFor}>
        {label}
        {required && (
          <span className={styles.required} aria-hidden="true">
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {hint && (
        <p id={`${htmlFor}-hint`} className="w360-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${htmlFor}-error`} className="w360-error-text">
          {error}
        </p>
      )}
    </div>
  );
}

const described = (id: string, hint: boolean, error?: string) => [hint ? `${id}-hint` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined;

function BasicSection({
  draft,
  boot,
  problems,
  busy,
  edit,
  productId,
}: {
  draft: PublisherDraft;
  boot: PublisherBootstrap;
  problems: Problems;
  busy: boolean;
  edit: (patch: Partial<PublisherDraft>) => void;
  productId: string | null;
}) {
  const id = useId();
  const f = (name: string) => `${id}-${name}`;
  const slug = effectiveSlug(draft);
  return (
    <section id="publisher-basic" className={styles.section} aria-labelledby={f("title")}>
      <h3 id={f("title")} className={styles.sectionTitle} tabIndex={-1}>
        <span className={styles.sectionNumber}>1</span> Basic information
      </h3>
      <div className={styles.grid}>
        <Field label="Product name" htmlFor={f("name")} required error={problems.name} hint={slug ? `Store address: /store/product/${slug}` : undefined}>
          <input
            id={f("name")}
            className="w360-input"
            value={draft.name}
            maxLength={LIMITS.name}
            required
            placeholder="e.g. Acme Desk"
            disabled={busy}
            aria-invalid={problems.name ? true : undefined}
            aria-describedby={described(f("name"), Boolean(slug), problems.name)}
            onChange={(e) => edit({ name: e.target.value })}
          />
        </Field>
        <Field label="Product category" htmlFor={f("category")} error={problems.category}>
          <select
            id={f("category")}
            className="w360-input"
            value={draft.category}
            disabled={busy}
            aria-invalid={problems.category ? true : undefined}
            onChange={(e) => edit({ category: e.target.value })}
          >
            <option value="">Choose a category</option>
            {[...new Set([...boot.categories, ...(draft.category ? [draft.category] : [])])].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Short description" htmlFor={f("tagline")} required wide error={problems.tagline} hint={`${draft.tagline.length}/${LIMITS.tagline} - shown on Store cards and at the top of the product page.`}>
          <input
            id={f("tagline")}
            className="w360-input"
            value={draft.tagline}
            maxLength={LIMITS.tagline}
            required
            disabled={busy}
            placeholder="One sentence about what the product does"
            aria-invalid={problems.tagline ? true : undefined}
            aria-describedby={described(f("tagline"), true, problems.tagline)}
            onChange={(e) => edit({ tagline: e.target.value })}
          />
        </Field>
        <Field label="Application URL" htmlFor={f("app")} required error={problems.applicationUrl} hint="The address of the product's web application (https). Sign-in through WorkEasy360 is set under Advanced settings.">
          <input
            id={f("app")}
            className="w360-input"
            type="url"
            inputMode="url"
            value={draft.applicationUrl}
            disabled={busy}
            placeholder="https://app.example.com"
            aria-invalid={problems.applicationUrl ? true : undefined}
            aria-describedby={described(f("app"), true, problems.applicationUrl)}
            onChange={(e) => edit({ applicationUrl: e.target.value })}
          />
        </Field>
        <div className={styles.field}>
          <ImagePicker
            label="Product logo"
            kind="logo"
            required
            file={draft.logo.file}
            currentUrl={draft.logo.mediaId && productId && !draft.logo.removed ? productMediaUrl(productId, draft.logo.mediaId) : null}
            progress={draft.logo.progress}
            error={problems.logo}
            busy={busy}
            onSelect={(file) => edit({ logo: { ...draft.logo, file, removed: false } })}
            onRemove={() => edit({ logo: { ...emptySlot(), mediaId: draft.logo.mediaId, removed: Boolean(draft.logo.mediaId) } })}
          />
        </div>
      </div>
    </section>
  );
}

function DetailsSection({
  draft,
  problems,
  busy,
  edit,
  productId,
}: {
  draft: PublisherDraft;
  problems: Problems;
  busy: boolean;
  edit: (patch: Partial<PublisherDraft>) => void;
  productId: string | null;
}) {
  const id = useId();
  const f = (name: string) => `${id}-${name}`;
  const setFeature = (index: number, value: string) => edit({ features: draft.features.map((x, i) => (i === index ? value : x)) });
  const storedVideoUrl = draft.videoStored && productId ? productMediaUrl(productId, draft.videoStored.id) : null;
  return (
    <section id="publisher-details" className={styles.section} aria-labelledby={f("title")}>
      <h3 id={f("title")} className={styles.sectionTitle} tabIndex={-1}>
        <span className={styles.sectionNumber}>2</span> Product details &amp; media
      </h3>
      <p className={styles.sectionLead}>Everything here appears on the product&apos;s page in the Digital Store.</p>

      <DescriptionField label="Full product description" value={draft.description} maxLength={LIMITS.description} error={problems.description} onChange={(description) => edit({ description })} />

      <ScreenshotManager
        shots={draft.shots}
        busy={busy}
        error={problems.shots}
        onChange={(shots) => edit({ shots })}
        onRemoveStored={(mediaId) => edit({ removedShots: [...draft.removedShots, mediaId] })}
      />

      <fieldset className={styles.group}>
        <legend>Product demo video</legend>
        <div className={styles.segment} role="radiogroup" aria-label="Demo video">
          {(
            [
              ["none", "No video"],
              ["upload", "Upload video"],
              ["link", "YouTube or Vimeo link"],
            ] as const
          ).map(([mode, text]) => (
            <button key={mode} type="button" role="radio" aria-checked={draft.videoMode === mode} className={draft.videoMode === mode ? styles.segmentOn : ""} disabled={busy} onClick={() => edit({ videoMode: mode })}>
              {text}
            </button>
          ))}
        </div>
        {draft.videoMode === "upload" && (
          <VideoPicker
            currentUrl={draft.videoFile ? null : storedVideoUrl}
            currentSize={draft.videoStored?.size_bytes ?? null}
            progress={draft.videoProgress}
            busy={busy}
            onSelect={(file) => edit({ videoFile: file })}
            onRemove={() => edit({ videoFile: null, videoMode: "none" })}
          />
        )}
        {draft.videoMode === "link" && (
          <Field label="Video link" htmlFor={f("video")} error={problems.videoUrl} hint="YouTube or Vimeo; the Store plays it in their privacy-friendly player.">
            <input
              id={f("video")}
              className="w360-input"
              type="url"
              value={draft.videoUrl}
              disabled={busy}
              placeholder="https://www.youtube.com/watch?v=..."
              aria-invalid={problems.videoUrl || (draft.videoUrl.trim() && !looksLikeVideo(draft.videoUrl.trim())) ? true : undefined}
              onChange={(e) => edit({ videoUrl: e.target.value })}
            />
          </Field>
        )}
        {draft.videoMode !== "none" && (
          <div className={styles.grid}>
            <Field label="Video title" htmlFor={f("vtitle")} error={problems.videoTitle}>
              <input id={f("vtitle")} className="w360-input" value={draft.videoTitle} maxLength={LIMITS.videoTitle} disabled={busy} placeholder="e.g. A two-minute tour" onChange={(e) => edit({ videoTitle: e.target.value })} />
            </Field>
            <div className={styles.field}>
              <ImagePicker
                label="Video thumbnail (optional)"
                kind="video_poster"
                file={draft.poster.file}
                currentUrl={draft.poster.mediaId && productId && !draft.poster.removed ? productMediaUrl(productId, draft.poster.mediaId) : null}
                progress={draft.poster.progress}
                busy={busy}
                onSelect={(file) => edit({ poster: { ...draft.poster, file, removed: false } })}
                onRemove={() => edit({ poster: { ...emptySlot(), mediaId: draft.poster.mediaId, removed: Boolean(draft.poster.mediaId) } })}
              />
            </div>
            <Field label="Video description" htmlFor={f("vdesc")} wide error={problems.videoDescription}>
              <textarea
                id={f("vdesc")}
                className={`w360-input ${styles.shortArea}`}
                rows={2}
                value={draft.videoDescription}
                maxLength={LIMITS.videoDescription}
                disabled={busy}
                onChange={(e) => edit({ videoDescription: e.target.value })}
              />
            </Field>
          </div>
        )}
      </fieldset>

      <fieldset className={styles.group}>
        <legend>Key features</legend>
        <p className="w360-hint">Shown as a features list on the Store page. Up to {LIMITS.features}.</p>
        <ul className={styles.featureList}>
          {draft.features.map((feature, i) => (
            <li key={i}>
              <label className="w360-visually-hidden" htmlFor={`${f("feature")}-${i}`}>
                Feature {i + 1}
              </label>
              <input
                id={`${f("feature")}-${i}`}
                className="w360-input"
                value={feature}
                maxLength={LIMITS.feature}
                disabled={busy}
                placeholder={i === 0 ? "e.g. Ticket management" : "Another feature"}
                aria-invalid={problems[`features.${i}`] ? true : undefined}
                onChange={(e) => setFeature(i, e.target.value)}
              />
              <button type="button" className={styles.iconButton} aria-label={`Remove feature ${i + 1}`} disabled={busy || draft.features.length === 1} onClick={() => edit({ features: draft.features.filter((_, j) => j !== i) })}>
                ✕
              </button>
            </li>
          ))}
        </ul>
        {problems.features && <p className="w360-error-text">{problems.features}</p>}
        <button type="button" className={`w360-btn w360-btn-secondary ${styles.addPlan}`} disabled={busy || draft.features.length >= LIMITS.features} onClick={() => edit({ features: [...draft.features, ""] })}>
          <span aria-hidden="true">+</span> Add Feature
        </button>
      </fieldset>

      <details className={styles.advanced}>
        <summary>Advanced details</summary>
        <div className={styles.grid}>
          <div className={`${styles.field} ${styles.span2}`}>
            <ImagePicker
              label="Cover / banner image"
              kind="banner"
              file={draft.banner.file}
              currentUrl={draft.banner.mediaId && productId && !draft.banner.removed ? productMediaUrl(productId, draft.banner.mediaId) : null}
              progress={draft.banner.progress}
              busy={busy}
              onSelect={(file) => edit({ banner: { ...draft.banner, file, removed: false } })}
              onRemove={() => edit({ banner: { ...emptySlot(), mediaId: draft.banner.mediaId, removed: Boolean(draft.banner.mediaId) } })}
            />
          </div>
          {(
            [
              ["marketingUrl", "Learn more URL"],
              ["documentationUrl", "Documentation URL"],
              ["supportUrl", "Support URL"],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} label={label} htmlFor={f(key)} error={problems[key]}>
              <input id={f(key)} className="w360-input" type="url" value={draft[key]} disabled={busy} placeholder="https://" aria-invalid={problems[key] ? true : undefined} onChange={(e) => edit({ [key]: e.target.value })} />
            </Field>
          ))}
          <Field label="Store title (SEO)" htmlFor={f("stitle")} error={problems.storeTitle} hint="Optional - the product name is used when empty.">
            <input id={f("stitle")} className="w360-input" value={draft.storeTitle} maxLength={LIMITS.storeTitle} disabled={busy} onChange={(e) => edit({ storeTitle: e.target.value })} />
          </Field>
          <Field label="Store summary (SEO)" htmlFor={f("ssum")} wide error={problems.storeSummary} hint="Optional - the short description is used when empty.">
            <input id={f("ssum")} className="w360-input" value={draft.storeSummary} maxLength={LIMITS.storeSummary} disabled={busy} onChange={(e) => edit({ storeSummary: e.target.value })} />
          </Field>
        </div>
      </details>
    </section>
  );
}

function AdvancedSection({ draft, problems, busy, edit }: { draft: PublisherDraft; problems: Problems; busy: boolean; edit: (patch: Partial<PublisherDraft>) => void }) {
  const id = useId();
  const f = (name: string) => `${id}-${name}`;
  const channel = (key: keyof PublisherDraft["channels"], on: boolean) => edit({ channels: { ...draft.channels, [key]: on } });
  return (
    <section id="publisher-advanced" className={styles.section} aria-labelledby={f("title")}>
      <details className={`${styles.advanced} ${styles.advancedSection}`}>
        <summary>
          <h3 id={f("title")} className={styles.sectionTitleInline}>
            Advanced settings
          </h3>
          <span className="w360-hint">Store address, sign-in, availability and free trial</span>
        </summary>
        <div className={styles.grid}>
          <Field label="Store address (slug)" htmlFor={f("slug")} error={problems.slug} hint={draft.slugLocked ? "Fixed: the product has been on sale under this address." : "Made from the product name; change it only if you must."}>
            <input
              id={f("slug")}
              className="w360-input"
              value={effectiveSlug(draft)}
              disabled={busy || draft.slugLocked}
              aria-invalid={problems.slug ? true : undefined}
              onChange={(e) => edit({ slug: e.target.value.toLowerCase(), slugTouched: true })}
            />
          </Field>
          {draft.hasIntegration ? (
            <p className={`${styles.notice} ${styles.span2}`} role="note">
              This product&apos;s launch address and sign-in are set on its Integration tab, with their credentials and health checks.
            </p>
          ) : (
            <>
              <Field label="Launch URL (optional override)" htmlFor={f("launch")} error={problems.launchUrl} hint="Where Open sends a customer, when it is not the application URL. Setting it registers the product's integration.">
                <input id={f("launch")} className="w360-input" type="url" value={draft.launchUrl} disabled={busy} placeholder="https://" aria-invalid={problems.launchUrl ? true : undefined} onChange={(e) => edit({ launchUrl: e.target.value })} />
              </Field>
              {draft.launchUrl.trim() && (
                <>
                  <label className={styles.check}>
                    <input type="checkbox" checked={draft.ssoEnabled} disabled={busy} onChange={(e) => edit({ ssoEnabled: e.target.checked })} />
                    <span>Customers sign in through WorkEasy360 (single sign-on)</span>
                  </label>
                  {draft.ssoEnabled && (
                    <Field label="Sign-in callback URL" htmlFor={f("cb")} error={problems.redirectUri} hint="The product's own OIDC callback address - the product must already support it.">
                      <input id={f("cb")} className="w360-input" type="url" value={draft.redirectUri} disabled={busy} placeholder="https://.../api/auth/callback/workeasy360" aria-invalid={problems.redirectUri ? true : undefined} onChange={(e) => edit({ redirectUri: e.target.value })} />
                    </Field>
                  )}
                  <Field label="Health check URL (optional)" htmlFor={f("health")} error={problems.healthUrl}>
                    <input id={f("health")} className="w360-input" type="url" value={draft.healthUrl} disabled={busy} placeholder="https://" onChange={(e) => edit({ healthUrl: e.target.value })} />
                  </Field>
                </>
              )}
            </>
          )}
          <fieldset className={`${styles.group} ${styles.span2}`}>
            <legend>Where it can be sold</legend>
            {(
              [
                ["customer_store_enabled", "Customer Store (store.workeasy360.com)"],
                ["reseller_enabled", "Resellers may sell it"],
                ["affiliate_enabled", "Affiliates may promote it"],
                ["white_label_enabled", "White-label partners may offer it"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className={styles.check}>
                <input type="checkbox" checked={draft.channels[key]} disabled={busy} onChange={(e) => channel(key, e.target.checked)} />
                <span>{label}</span>
              </label>
            ))}
            <p className="w360-hint">Partners still need the product granted to them, as before.</p>
          </fieldset>
          <fieldset className={`${styles.group} ${styles.span2}`}>
            <legend>Free trial</legend>
            <label className={styles.check}>
              <input type="checkbox" checked={draft.trialEnabled} disabled={busy} onChange={(e) => edit({ trialEnabled: e.target.checked })} />
              <span>Offer a free trial</span>
            </label>
            {draft.trialEnabled && (
              <Field label="Trial length (days)" htmlFor={f("tdays")} error={problems.trialDays} hint="Which plans the trial gives is set on the product's Availability tab.">
                <input id={f("tdays")} className="w360-input" type="number" min={1} max={365} value={draft.trialDays} disabled={busy} onChange={(e) => edit({ trialDays: e.target.value })} />
              </Field>
            )}
          </fieldset>
        </div>
      </details>
    </section>
  );
}

function LeaveConfirm({ onStay, onLeave }: { onStay: () => void; onLeave: () => void }) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={onStay} size="sm">
      <div className={styles.confirm}>
        <h2 id={titleId} className={styles.confirmTitle}>
          Leave without saving?
        </h2>
        <p>Your changes on this page have not been saved.</p>
        <div className={styles.confirmActions}>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onStay}>
            Keep editing
          </button>
          <button type="button" className="w360-btn w360-btn-danger" onClick={onLeave}>
            Discard changes
          </button>
        </div>
      </div>
    </Modal>
  );
}
