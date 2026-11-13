"use client";

import { useState } from "react";
import { iconLabel, popupButton } from "../kit/PopupLabel";
import { adminProgrammeApi, type AdminProductUpdate, type ProductUpdateBody } from "@/lib/api/adminProgramme";
import type { ConsoleProduct } from "@/lib/api/controlPlane";
import { dialog, FormDialog, FormPopup } from "../Modal";
import { LoadGate, Status, Table, useAction, useLoad, when } from "../ui";
import { SelectField, TextAreaField, TextField } from "../widgets";
import cp from "../ControlPlane.module.css";

/**
 * Product Manager → Updates (ADR-054): the feature announcements and releases
 * affiliates may link to. A draft is private; publishing gives it a public page
 * on the platform (`/products/<slug>/updates/<slug>`) and makes it linkable;
 * archiving takes the page down while existing links keep their attribution
 * and open the product page instead. The address is fixed once published,
 * because partners have shared it. Nothing here grants anyone the product.
 *
 * 2026-11-05 (the owner: every form in a popup): "+ New update" and each
 * row's Edit open the same fields in a popup.
 */

const slugify = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);

const EMPTY: Required<Omit<ProductUpdateBody, "banner_id">> & { banner_id: string } = {
  kind: "release",
  slug: "",
  title: "",
  summary: "",
  body: "",
  banner_id: "",
  landing_url: "",
};
type Draft = typeof EMPTY;
type ProductUpdates = Awaited<ReturnType<typeof adminProgrammeApi.productUpdates>>;

/** The update's fields, laid out in the popup's grid. */
function UpdateFields({
  form,
  set,
  slugLocked,
  onSlug,
  onTitle,
  productSlug,
  data,
}: {
  form: Draft;
  set: (patch: Partial<Draft>) => void;
  slugLocked: boolean;
  onSlug: (v: string) => void;
  onTitle: (v: string) => void;
  productSlug: string;
  data: ProductUpdates;
}) {
  return (
    <>
      <SelectField
        label="Kind"
        value={form.kind}
        onChange={(v) => set({ kind: v as "feature" | "release" })}
        options={[
          { value: "release", label: "Release or product update" },
          { value: "feature", label: "Feature announcement" },
        ]}
      />
      <TextField label="Title" value={form.title} required maxLength={120} onChange={onTitle} />
      <TextField
        label="Page address"
        value={form.slug}
        maxLength={64}
        disabled={slugLocked}
        onChange={onSlug}
        hint={`/products/${productSlug}/updates/${form.slug || "…"} - fixed once published.`}
      />
      <TextField label="Summary" value={form.summary} maxLength={300} onChange={(v) => set({ summary: v })} />
      <div className={dialog.span}>
        <TextAreaField
          label="Approved promotional text"
          value={form.body}
          maxLength={4000}
          rows={6}
          onChange={(v) => set({ body: v })}
          hint="Plain text affiliates may copy into their posts."
        />
      </div>
      <SelectField
        label="Banner"
        value={form.banner_id}
        onChange={(v) => set({ banner_id: v })}
        options={[
          { value: "", label: "No banner" },
          ...data.images.map((m) => ({ value: m.id, label: m.caption || `${m.kind} ${m.id.slice(0, 8)}` })),
        ]}
        hint="One of this product's own banner or screenshot images."
      />
      <TextField
        label="Full announcement page (optional)"
        value={form.landing_url}
        maxLength={500}
        inputMode="url"
        onChange={(v) => set({ landing_url: v })}
        hint={`An https page on ${data.approved_hosts.join(", ") || "an approved host"}. Shown on the update page, never redirected to.`}
      />
    </>
  );
}

export function UpdatesTab({ product, manage }: { product: ConsoleProduct; manage: boolean }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.productUpdates(product.id), [product.id]);
  const { act, busy, feedback } = useAction(reload);
  const [editing, setEditing] = useState<AdminProductUpdate | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [slugTouched, setSlugTouched] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const set = (patch: Partial<Draft>) => setForm((f) => ({ ...f, ...patch }));
  const startEdit = (u: AdminProductUpdate) => {
    setNotice(null);
    setEditing(u);
    setSlugTouched(true);
    setForm({
      kind: u.kind,
      slug: u.slug,
      title: u.title,
      summary: u.summary,
      body: u.body,
      banner_id: u.banner_id ?? "",
      landing_url: u.landing_url,
    });
  };
  const reset = () => {
    setEditing(null);
    setSlugTouched(false);
    setForm(EMPTY);
  };
  const body = (): ProductUpdateBody => ({
    ...form,
    banner_id: form.banner_id || null,
    // The address of a published update is fixed: never send it again.
    ...(editing && editing.status !== "draft" ? { slug: undefined } : {}),
  });
  const ready = !!form.title.trim() && !!form.slug.trim();
  const fieldsFor = (data: ProductUpdates) => (
    <UpdateFields
      form={form}
      set={set}
      slugLocked={editing !== null && editing.status !== "draft"}
      onTitle={(v) => set({ title: v, ...(slugTouched ? {} : { slug: slugify(v) }) })}
      onSlug={(v) => {
        setSlugTouched(true);
        set({ slug: v.toLowerCase() });
      }}
      productSlug={product.slug ?? ""}
      data={data}
    />
  );

  return (
    <div className={cp.stack}>
      <p className={cp.lead}>
        Feature announcements and releases that approved affiliates may promote with their own links. Only a published update has a
        public page and can be linked. Editing the text never changes which affiliate a visit belongs to.
      </p>
      {feedback}
      {notice && <p role="status">{notice}</p>}
      <LoadGate load={load} what="the updates">
        {(d) => (
          <>
            {manage && (
              <div className={cp.row}>
                <FormPopup
                  label={iconLabel("New update")}
                  buttonClassName={popupButton()}
                  title="New update"
                  size="lg"
                  submitLabel="Create draft"
                  disabled={busy}
                  canSubmit={ready}
                  onOpen={() => {
                    setNotice(null);
                    reset();
                  }}
                  onSubmit={async () => {
                    await adminProgrammeApi.createProductUpdate(product.id, body());
                    return "Draft update created.";
                  }}
                  onDone={() => {
                    reset();
                    reload();
                  }}
                >
                  {fieldsFor(d)}
                </FormPopup>
              </div>
            )}
            <Table
              caption={`Updates of ${product.name}`}
              head={["Title", "Kind", "Status", "Published", "Page", ""]}
              isEmpty={d.updates.length === 0}
              empty="No updates yet."
            >
              {d.updates.map((u) => (
                <tr key={u.id}>
                  <th scope="row">{u.title}</th>
                  <td>{u.kind === "feature" ? "Feature" : "Release"}</td>
                  <td>
                    <Status value={u.status} />
                  </td>
                  <td>{when(u.published_at)}</td>
                  <td>
                    {u.status === "published" ? (
                      <a href={u.page_path} target="_blank" rel="noopener noreferrer">
                        View page<span className="w360-visually-hidden"> for {u.title}</span>
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    {manage && u.status !== "archived" && (
                      <div className={cp.row}>
                        <button type="button" className="w360-btn w360-btn-secondary" aria-haspopup="dialog" aria-label={`Edit ${u.title}`} onClick={() => startEdit(u)}>
                          Edit
                        </button>
                        {u.status === "draft" && (
                          <button
                            type="button"
                            className="w360-btn w360-btn-primary"
                            disabled={busy}
                            aria-label={`Publish ${u.title}`}
                            onClick={() => void act(() => adminProgrammeApi.productUpdateAction(product.id, u.id, "publish"), `${u.title} published.`)}
                          >
                            Publish
                          </button>
                        )}
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          disabled={busy}
                          aria-label={`Archive ${u.title}`}
                          onClick={() => void act(() => adminProgrammeApi.productUpdateAction(product.id, u.id, "archive"), `${u.title} archived.`)}
                        >
                          Archive
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            {editing && (
              <FormDialog
                title={`Edit ${editing.title}`}
                size="lg"
                submitLabel="Save changes"
                canSubmit={ready && !busy}
                onSubmit={async () => {
                  await adminProgrammeApi.editProductUpdate(product.id, editing.id, body());
                  return "Update saved.";
                }}
                onDone={(m) => {
                  setNotice(m ?? null);
                  reload();
                }}
                onClose={reset}
              >
                {fieldsFor(d)}
              </FormDialog>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}
