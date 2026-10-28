"use client";

import { useState } from "react";
import { adminApi, productMediaUrl, type ConsoleProduct, type ProductChanges } from "@/lib/api/controlPlane";
import { describeError } from "../ui";
import { DescriptionField, ErrorSummary, SelectField, TextField } from "../widgets";
import { IconPicker } from "./IconPicker";
import { ImagePicker, ScreenshotsField, type ShotItem } from "./ImagePicker";
import { SLUG_PATTERN, looksLikeHttps } from "./model";
import styles from "./ProductManager.module.css";

type Act = (action: () => Promise<unknown>, success: string) => Promise<void>;

const TEXT_FIELDS = [
  "name",
  "slug",
  "category",
  "tagline",
  "icon",
  "store_title",
  "store_summary",
  "description",
  "marketing_url",
  "documentation_url",
  "support_url",
] as const;
type TextKey = (typeof TEXT_FIELDS)[number];

/** Identity and store-listing fields. Saves only what changed, through the
 * catalog's one product edit (validated and audited by the server). */
function ListingForm({ product: p, categories, busy, act }: { product: ConsoleProduct; categories: string[]; busy: boolean; act: Act }) {
  const [draft, setDraft] = useState<Record<TextKey, string>>(() => Object.fromEntries(TEXT_FIELDS.map((k) => [k, p[k] ?? ""])) as Record<TextKey, string>);
  const [errors, setErrors] = useState<Partial<Record<TextKey, string>>>({});
  const slugLocked = p.activated_at !== null;
  const set = (key: TextKey) => (value: string) => setDraft((d) => ({ ...d, [key]: value }));
  const changed = TEXT_FIELDS.filter((k) => draft[k] !== (p[k] ?? ""));
  const options = categories.includes(p.category) || !p.category ? categories : [p.category, ...categories];

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const found: Partial<Record<TextKey, string>> = {};
    if (!draft.name.trim()) found.name = "Enter the product name.";
    if (!SLUG_PATTERN.test(draft.slug.trim())) found.slug = "Use lowercase letters and digits joined by single hyphens.";
    for (const key of ["marketing_url", "documentation_url", "support_url"] as const) {
      if (draft[key].trim() && !looksLikeHttps(draft[key].trim())) found[key] = "Use an https:// address.";
    }
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const changes: ProductChanges = Object.fromEntries(changed.map((k) => [k, k === "description" ? draft[k] : draft[k].trim()]));
    void act(() => adminApi.updateProduct(p.id, changes), "Store listing saved.");
  }

  return (
    <form className={styles.card} onSubmit={submit} noValidate aria-labelledby="listing-title">
      <h2 id="listing-title" className={styles.cardTitle}>
        Identity and store listing
      </h2>
      <ErrorSummary errors={Object.values(errors).filter((v): v is string => !!v)} />
      <div className={styles.formGrid}>
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
        <div className={styles.full}>
          <DescriptionField label="Full description" value={draft.description} maxLength={4000} onChange={set("description")} />
        </div>
        <div className={styles.full}>
          <IconPicker value={draft.icon} onChange={set("icon")} />
        </div>
        <TextField label="Learn More URL" type="url" placeholder="https://" value={draft.marketing_url} error={errors.marketing_url} onChange={set("marketing_url")} />
        <TextField label="Documentation URL" type="url" placeholder="https://" value={draft.documentation_url} error={errors.documentation_url} onChange={set("documentation_url")} />
        <TextField label="Support URL" type="url" placeholder="https://" value={draft.support_url} error={errors.support_url} onChange={set("support_url")} />
      </div>
      <div className={styles.toolbar}>
        <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || changed.length === 0}>
          Save listing
        </button>
        {changed.length > 0 && <span className={styles.muted}>{changed.length} unsaved change{changed.length === 1 ? "" : "s"}</span>}
      </div>
    </form>
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

  const upload = (key: string, kind: "logo" | "banner" | "screenshot", file: File, caption = "") =>
    adminApi.uploadProductMedia(p.id, kind, file, { caption, onProgress: (pct) => setProgress((cur) => ({ ...cur, [key]: pct })) });

  const shots: ShotItem[] = [
    ...stored.map((m) => ({ key: m.id, url: productMediaUrl(p.id, m.id), caption: captions[m.id] ?? m.caption, progress: null })),
    ...pending.map((s) => ({ ...s, progress: progress[s.key] ?? 0 })),
  ];
  const disabled = busy || working;

  return (
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
  );
}

export function ListingTab({
  product,
  categories,
  manage,
  busy,
  act,
  reload,
  onPreview,
}: {
  product: ConsoleProduct;
  categories: string[];
  manage: boolean;
  busy: boolean;
  act: Act;
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
  const mediaKey = product.media.map((m) => `${m.id}:${m.sort_order}:${m.caption}`).join("|");
  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={onPreview}>
          Preview Store Listing
        </button>
      </div>
      <ListingForm key={product.updated_at ?? ""} product={product} categories={categories} busy={busy} act={act} />
      <MediaSection key={mediaKey} product={product} busy={busy} reload={reload} />
    </div>
  );
}
