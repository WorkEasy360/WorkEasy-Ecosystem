"use client";

import { useState } from "react";
import { adminProgrammeApi, type AdminProductUpdate, type ProductUpdateBody } from "@/lib/api/adminProgramme";
import type { ConsoleProduct } from "@/lib/api/controlPlane";
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

export function UpdatesTab({ product, manage }: { product: ConsoleProduct; manage: boolean }) {
  const [load, reload] = useLoad(() => adminProgrammeApi.productUpdates(product.id), [product.id]);
  const { act, busy, feedback } = useAction(reload);
  const [editing, setEditing] = useState<AdminProductUpdate | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [slugTouched, setSlugTouched] = useState(false);
  const set = (patch: Partial<typeof EMPTY>) => setForm((f) => ({ ...f, ...patch }));
  const startEdit = (u: AdminProductUpdate) => {
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

  return (
    <div className={cp.stack}>
      <p className={cp.lead}>
        Feature announcements and releases that approved affiliates may promote with their own links. Only a published update has a
        public page and can be linked. Editing the text never changes which affiliate a visit belongs to.
      </p>
      {feedback}
      <LoadGate load={load} what="the updates">
        {(d) => (
          <>
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
                        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => startEdit(u)}>
                          Edit<span className="w360-visually-hidden"> {u.title}</span>
                        </button>
                        {u.status === "draft" && (
                          <button
                            type="button"
                            className="w360-btn w360-btn-primary"
                            disabled={busy}
                            onClick={() => void act(() => adminProgrammeApi.productUpdateAction(product.id, u.id, "publish"), `${u.title} published.`)}
                          >
                            Publish<span className="w360-visually-hidden"> {u.title}</span>
                          </button>
                        )}
                        <button
                          type="button"
                          className="w360-btn w360-btn-secondary"
                          disabled={busy}
                          onClick={() => void act(() => adminProgrammeApi.productUpdateAction(product.id, u.id, "archive"), `${u.title} archived.`)}
                        >
                          Archive<span className="w360-visually-hidden"> {u.title}</span>
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
            {manage && (
              <form
                className={cp.form}
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    if (editing) await adminProgrammeApi.editProductUpdate(product.id, editing.id, body());
                    else await adminProgrammeApi.createProductUpdate(product.id, body());
                    reset();
                  }, editing ? "Update saved." : "Draft update created.");
                }}
              >
                <h3>{editing ? `Edit ${editing.title}` : "New update"}</h3>
                <SelectField
                  label="Kind"
                  value={form.kind}
                  onChange={(v) => set({ kind: v as "feature" | "release" })}
                  options={[
                    { value: "release", label: "Release or product update" },
                    { value: "feature", label: "Feature announcement" },
                  ]}
                />
                <TextField
                  label="Title"
                  value={form.title}
                  required
                  maxLength={120}
                  onChange={(v) => set({ title: v, ...(slugTouched ? {} : { slug: slugify(v) }) })}
                />
                <TextField
                  label="Page address"
                  value={form.slug}
                  maxLength={64}
                  disabled={editing !== null && editing.status !== "draft"}
                  onChange={(v) => {
                    setSlugTouched(true);
                    set({ slug: v.toLowerCase() });
                  }}
                  hint={`/products/${product.slug ?? ""}/updates/${form.slug || "…"} - fixed once published.`}
                />
                <TextField label="Summary" value={form.summary} maxLength={300} onChange={(v) => set({ summary: v })} />
                <TextAreaField
                  label="Approved promotional text"
                  value={form.body}
                  maxLength={4000}
                  rows={6}
                  onChange={(v) => set({ body: v })}
                  hint="Plain text affiliates may copy into their posts."
                />
                <SelectField
                  label="Banner"
                  value={form.banner_id}
                  onChange={(v) => set({ banner_id: v })}
                  options={[
                    { value: "", label: "No banner" },
                    ...d.images.map((m) => ({ value: m.id, label: m.caption || `${m.kind} ${m.id.slice(0, 8)}` })),
                  ]}
                  hint="One of this product's own banner or screenshot images."
                />
                <TextField
                  label="Full announcement page (optional)"
                  value={form.landing_url}
                  maxLength={500}
                  inputMode="url"
                  onChange={(v) => set({ landing_url: v })}
                  hint={`An https page on ${d.approved_hosts.join(", ") || "an approved host"}. Shown on the update page, never redirected to.`}
                />
                <div className={cp.row}>
                  <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !form.title.trim() || !form.slug.trim()}>
                    {editing ? "Save changes" : "Create draft"}
                  </button>
                  {editing && (
                    <button type="button" className="w360-btn w360-btn-secondary" onClick={reset}>
                      Cancel
                    </button>
                  )}
                </div>
              </form>
            )}
          </>
        )}
      </LoadGate>
    </div>
  );
}
