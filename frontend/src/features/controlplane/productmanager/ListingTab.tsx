"use client";

import { useState } from "react";
import { adminApi, productMediaUrl, type ConsoleProduct, type ProductChanges } from "@/lib/api/controlPlane";
import { dialog, FormPopup } from "../Modal";
import { describeError } from "../ui";
import { DescriptionField, SelectField, TextField } from "../widgets";
import { IconPicker } from "./IconPicker";
import { ImagePicker, ScreenshotsField, type ShotItem } from "./ImagePicker";
import { VideoPicker } from "./VideoPicker";
import { MerchandisingForm } from "./MerchandisingForm";
import { SLUG_PATTERN, looksLikeHttps, looksLikeVideo } from "./model";
import styles from "./ProductManager.module.css";

const TEXT_FIELDS = [
  "name",
  "slug",
  "category",
  "tagline",
  "icon",
  "store_title",
  "store_summary",
  "description",
  "video_url",
  "marketing_url",
  "documentation_url",
  "support_url",
] as const;
type TextKey = (typeof TEXT_FIELDS)[number];

const textOf = (p: ConsoleProduct) => Object.fromEntries(TEXT_FIELDS.map((k) => [k, p[k] ?? ""])) as Record<TextKey, string>;

/** The listing's checks, made as the admin types (the server makes them again). */
function problemsOf(draft: Record<TextKey, string>): Partial<Record<TextKey, string>> {
  const found: Partial<Record<TextKey, string>> = {};
  if (!draft.name.trim()) found.name = "Enter the product name.";
  if (!SLUG_PATTERN.test(draft.slug.trim())) found.slug = "Use lowercase letters and digits joined by single hyphens.";
  for (const key of ["marketing_url", "documentation_url", "support_url"] as const) {
    if (draft[key].trim() && !looksLikeHttps(draft[key].trim())) found[key] = "Use an https:// address.";
  }
  if (draft.video_url.trim() && !looksLikeVideo(draft.video_url.trim())) found.video_url = "Use a YouTube or Vimeo link.";
  return found;
}

/** A long text, cut to a line for the read-only summary. */
function excerpt(text: string, max = 180): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Identity and store-listing fields: the stored values on the page, and an
 * Edit popup that saves only what changed, through the catalog's one product
 * edit (validated and audited by the server). */
function ListingForm({ product: p, categories, busy, reload }: { product: ConsoleProduct; categories: string[]; busy: boolean; reload: () => void }) {
  const [draft, setDraft] = useState<Record<TextKey, string>>(() => textOf(p));
  const errors = problemsOf(draft);
  const slugLocked = p.activated_at !== null;
  const set = (key: TextKey) => (value: string) => setDraft((d) => ({ ...d, [key]: value }));
  const changed = TEXT_FIELDS.filter((k) => draft[k] !== (p[k] ?? ""));
  const options = categories.includes(p.category) || !p.category ? categories : [p.category, ...categories];
  const facts: [string, React.ReactNode][] = [
    ["Product name", p.name || "—"],
    ["Product slug", <span key="s" className={styles.code}>{p.slug}</span>],
    ["Category", p.category || "—"],
    ["Short description", p.tagline || "—"],
    ["Store title", p.store_title || "Same as the product name"],
    ["Short store description", p.store_summary || "Same as the short description"],
    ["Product video link", p.video_url ? <span key="v" className={styles.url}>{p.video_url}</span> : "—"],
    ["Learn More URL", p.marketing_url ? <span key="m" className={styles.url}>{p.marketing_url}</span> : "—"],
    ["Documentation URL", p.documentation_url ? <span key="d" className={styles.url}>{p.documentation_url}</span> : "—"],
    ["Support URL", p.support_url ? <span key="u" className={styles.url}>{p.support_url}</span> : "—"],
  ];

  return (
    <section className={styles.card} aria-labelledby="listing-title">
      <div className={styles.sectionHead}>
        <h2 id="listing-title" className={styles.cardTitle}>
          Identity and store listing
        </h2>
        <div className={styles.toolbar}>
          <FormPopup
            label="Edit listing"
            title="Identity and store listing"
            size="lg"
            submitLabel="Save listing"
            disabled={busy}
            canSubmit={changed.length > 0 && Object.keys(errors).length === 0}
            onOpen={() => setDraft(textOf(p))}
            onSubmit={async () => {
              const changes: ProductChanges = Object.fromEntries(changed.map((k) => [k, k === "description" ? draft[k] : draft[k].trim()]));
              await adminApi.updateProduct(p.id, changes);
              return "Store listing saved.";
            }}
            onDone={reload}
          >
            <TextField label="Product name" showRequired required maxLength={128} value={draft.name} error={errors.name} onChange={set("name")} />
            <TextField
              label="Product slug"
              showRequired
              required
              maxLength={64}
              value={draft.slug}
              error={errors.slug}
              disabled={slugLocked}
              hint={slugLocked ? "Fixed: this product has been on sale, and links use it." : `Product code: ${p.code} (permanent).`}
              onChange={(v) => set("slug")(v.toLowerCase())}
            />
            <SelectField
              label="Category"
              showRequired
              value={draft.category}
              options={[{ value: "", label: "Choose a category" }, ...options.map((c) => ({ value: c, label: c }))]}
              onChange={set("category")}
            />
            <TextField label="Short description" showRequired maxLength={160} value={draft.tagline} hint={`${draft.tagline.length}/160`} onChange={set("tagline")} />
            <TextField label="Store title" maxLength={128} placeholder={draft.name} value={draft.store_title} hint="Leave empty to use the product name." onChange={set("store_title")} />
            <TextField
              label="Short store description"
              maxLength={200}
              placeholder={draft.tagline}
              value={draft.store_summary}
              hint={`The store card's line. ${draft.store_summary.length}/200`}
              onChange={set("store_summary")}
            />
            <div className={dialog.span}>
              <DescriptionField label="Full description" value={draft.description} maxLength={4000} onChange={set("description")} />
            </div>
            <div className={dialog.span}>
              <TextField
                label="Product video"
                type="url"
                placeholder="https://www.youtube.com/watch?v=..."
                maxLength={200}
                value={draft.video_url}
                error={errors.video_url}
                hint="Or a YouTube or Vimeo link instead of an upload (an uploaded video on the Video card is shown instead of this link). Leave empty for none."
                onChange={set("video_url")}
              />
            </div>
            <div className={dialog.span}>
              <IconPicker value={draft.icon} onChange={set("icon")} />
            </div>
            <TextField label="Learn More URL" type="url" placeholder="https://" value={draft.marketing_url} error={errors.marketing_url} onChange={set("marketing_url")} />
            <TextField label="Documentation URL" type="url" placeholder="https://" value={draft.documentation_url} error={errors.documentation_url} onChange={set("documentation_url")} />
            <TextField label="Support URL" type="url" placeholder="https://" value={draft.support_url} error={errors.support_url} onChange={set("support_url")} />
            {changed.length > 0 && (
              <p className={`${styles.muted} ${dialog.span}`}>
                {changed.length} unsaved change{changed.length === 1 ? "" : "s"}
              </p>
            )}
          </FormPopup>
        </div>
      </div>
      <dl className={styles.facts}>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
        <div className={styles.wide}>
          <dt>Full description</dt>
          <dd>{(p.description ?? "").trim() ? excerpt(p.description ?? "") : "—"}</dd>
        </div>
      </dl>
    </section>
  );
}

/** Logo, banner and screenshots, each stored the moment it is chosen (with
 * upload progress) and removed or reordered in place. */
function MediaSection({ product: p, busy, reload }: { product: ConsoleProduct; busy: boolean; reload: () => void }) {
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [problem, setProblem] = useState<Record<string, string | null>>({});
  const [working, setWorking] = useState(false);
  const logo = p.media.find((m) => m.kind === "logo");
  const banner = p.media.find((m) => m.kind === "banner");
  const stored = p.media.filter((m) => m.kind === "screenshot");
  const [captions, setCaptions] = useState<Record<string, string>>(() => Object.fromEntries(stored.map((m) => [m.id, m.caption])));
  const [pending, setPending] = useState<ShotItem[]>([]);

  async function run(key: string, task: () => Promise<unknown>) {
    setWorking(true);
    setProblem((cur) => ({ ...cur, [key]: null }));
    try {
      await task();
      reload();
    } catch (err) {
      setProblem((cur) => ({ ...cur, [key]: describeError(err) }));
    } finally {
      setWorking(false);
      setProgress((cur) => {
        const next = { ...cur };
        delete next[key];
        return next;
      });
    }
  }

  const upload = (key: string, kind: "logo" | "banner" | "screenshot" | "video", file: File, caption = "") =>
    adminApi.uploadProductMedia(p.id, kind, file, { caption, onProgress: (pct) => setProgress((cur) => ({ ...cur, [key]: pct })) });

  const shots: ShotItem[] = [
    ...stored.map((m) => ({ key: m.id, url: productMediaUrl(p.id, m.id), caption: captions[m.id] ?? m.caption, progress: null })),
    ...pending.map((s) => ({ ...s, progress: progress[s.key] ?? 0 })),
  ];
  const disabled = busy || working;

  return (
    <>
    <section className={styles.card} aria-labelledby="video-title">
      <h2 id="video-title" className={styles.cardTitle}>
        Video
      </h2>
      <VideoPicker
        currentUrl={p.video ? productMediaUrl(p.id, p.video.id) : null}
        currentSize={p.video?.size_bytes ?? null}
        progress={progress.video ?? null}
        error={problem.video}
        busy={disabled}
        onSelect={(file) => void run("video", () => upload("video", "video", file))}
        onRemove={() => p.video && void run("video", () => adminApi.deleteProductMedia(p.id, p.video!.id))}
      />
    </section>
    <section className={styles.card} aria-labelledby="images-title">
      <h2 id="images-title" className={styles.cardTitle}>
        Images
      </h2>
      <div className={styles.formGrid}>
        <ImagePicker
          label="Product logo"
          kind="logo"
          required
          file={null}
          currentUrl={logo ? productMediaUrl(p.id, logo.id) : null}
          progress={progress.logo ?? null}
          error={problem.logo}
          busy={disabled}
          onSelect={(file) => void run("logo", () => upload("logo", "logo", file))}
          onRemove={() => logo && void run("logo", () => adminApi.deleteProductMedia(p.id, logo.id))}
        />
        <ImagePicker
          label="Cover / banner image"
          kind="banner"
          file={null}
          currentUrl={banner ? productMediaUrl(p.id, banner.id) : null}
          progress={progress.banner ?? null}
          error={problem.banner}
          busy={disabled}
          onSelect={(file) => void run("banner", () => upload("banner", "banner", file))}
          onRemove={() => banner && void run("banner", () => adminApi.deleteProductMedia(p.id, banner.id))}
        />
        <div className={styles.full}>
          <ScreenshotsField
            shots={shots}
            busy={disabled}
            error={problem.shots}
            onAdd={(files) => {
              const items = files.map((file) => ({ key: `new-${Date.now()}-${file.name}-${Math.random()}`, file, caption: "" }));
              setPending(items);
              void run("shots", async () => {
                for (const item of items) await upload(item.key, "screenshot", item.file);
              }).then(() => setPending([]));
            }}
            onMove={(from, to) => {
              const order = stored.map((m) => m.id);
              const [moved] = order.splice(from, 1);
              order.splice(to, 0, moved);
              void run("shots", () => adminApi.reorderProductMedia(p.id, order));
            }}
            onRemove={(i) => {
              const target = stored[i];
              if (target) void run("shots", () => adminApi.deleteProductMedia(p.id, target.id));
            }}
            onCaption={(i, caption) => {
              const target = stored[i];
              if (target) setCaptions((cur) => ({ ...cur, [target.id]: caption }));
            }}
            onCaptionCommit={(i) => {
              const target = stored[i];
              if (target && (captions[target.id] ?? "") !== target.caption) {
                void run("shots", () => adminApi.captionProductMedia(p.id, target.id, (captions[target.id] ?? "").trim()));
              }
            }}
          />
        </div>
      </div>
    </section>
    </>
  );
}

export function ListingTab({
  product,
  categories,
  manage,
  busy,
  reload,
  onPreview,
}: {
  product: ConsoleProduct;
  categories: string[];
  manage: boolean;
  busy: boolean;
  reload: () => void;
  onPreview: () => void;
}) {
  if (!manage) {
    return (
      <div className={styles.card}>
        <p className={styles.muted}>Editing the listing needs the catalog management permission.</p>
        <div>
          <button type="button" className="w360-btn w360-btn-secondary" onClick={onPreview}>
            Preview Store Listing
          </button>
        </div>
      </div>
    );
  }
  // Keyed by the stored values, so a reload after a save shows the server's copy.
  const mediaKey = [...product.media.map((m) => `${m.id}:${m.sort_order}:${m.caption}`), product.video?.id ?? ""].join("|");
  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onPreview}>
          Preview Store Listing
        </button>
      </div>
      <ListingForm product={product} categories={categories} busy={busy} reload={reload} />
      <MerchandisingForm product={product} busy={busy} reload={reload} />
      <MediaSection key={mediaKey} product={product} busy={busy} reload={reload} />
    </div>
  );
}
