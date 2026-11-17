"use client";

import { useId, useState } from "react";
import { adminApi, type ProductCategoryList, type ProductCategoryRow } from "@/lib/api/controlPlane";
import { Icon } from "@/components/sidebar/icons";
import { DialogHead, Modal, dialog, useClosing } from "../Modal";
import { describeError, useLoad } from "../ui";
import styles from "./Categories.module.css";

/** What changed in the list, so an open form can follow it: a product being
 * edited in "Sales" stays in it when "Sales" becomes "Sales & CRM". */
export type CategoryChange =
  | { kind: "added"; name: string }
  | { kind: "renamed"; from: string; to: string }
  | { kind: "deleted"; name: string; movedTo: string | null }
  | { kind: "reordered" };

/** The field's choices from a list: the list's order, then any category a
 * product carries that is not on it. */
export const optionsOf = (list: ProductCategoryList): string[] => [...list.categories.map((c) => c.name), ...list.unlisted.map((u) => u.name)];

const tidy = (raw: string) => raw.split(/\s+/).filter(Boolean).join(" ");
const productsLabel = (n: number) => (n === 0 ? "No products" : `${n} product${n === 1 ? "" : "s"}`);

/**
 * Super Admin → Products → Manage categories (2026-11-16, the owner: "make
 * category customisable"): the one list every category field offers. Add a
 * category, rename it (its products follow), move it up or down, or delete it
 * (a category in use first moves its products to another). Every change is the
 * server's, validated and audited there; the popup shows what it answered.
 */
export function CategoryManager({ onClose, onChanged }: { onClose: () => void; onChanged?: (change: CategoryChange, list: ProductCategoryList) => void }) {
  const titleId = useId();
  const newId = useId();
  const { closing, close } = useClosing(onClose);
  const [load] = useLoad(() => adminApi.productCategories(), []);
  const [fresh, setFresh] = useState<ProductCategoryList | null>(null);
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState<{ id: string; moveTo: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const list = fresh ?? (load.status === "ready" ? load.data : null);
  const manage = list?.can_manage ?? false;
  const full = list ? list.categories.length >= list.max_categories : false;

  async function run(work: () => Promise<{ list: ProductCategoryList; change: CategoryChange; notice: string }>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await work();
      setFresh(done.list);
      setNotice(done.notice);
      onChanged?.(done.change, done.list);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const add = (raw: string) => {
    const name = tidy(raw);
    if (!name) {
      setError("Name the category.");
      return;
    }
    void run(async () => {
      const row = await adminApi.createProductCategory(name);
      setNewName("");
      return { list: await adminApi.productCategories(), change: { kind: "added", name: row.name }, notice: `Added “${row.name}”.` };
    });
  };

  const rename = (row: ProductCategoryRow, raw: string) => {
    const name = tidy(raw);
    if (!name) {
      setError("Name the category.");
      return;
    }
    if (name === row.name) {
      setEditing(null);
      return;
    }
    void run(async () => {
      const renamed = await adminApi.renameProductCategory(row.id, name);
      setEditing(null);
      const moved = renamed.products ? ` and its ${productsLabel(renamed.products).toLowerCase()}` : "";
      return {
        list: await adminApi.productCategories(),
        change: { kind: "renamed", from: row.name, to: renamed.name },
        notice: `Renamed “${row.name}” to “${renamed.name}”${moved}.`,
      };
    });
  };

  const remove = (row: ProductCategoryRow, moveTo: string) => {
    const target = list?.categories.find((c) => c.id === moveTo) ?? null;
    void run(async () => {
      const after = await adminApi.deleteProductCategory(row.id, row.products ? moveTo : undefined);
      setDeleting(null);
      return {
        list: after,
        change: { kind: "deleted", name: row.name, movedTo: row.products && target ? target.name : null },
        notice: row.products && target ? `Deleted “${row.name}”; its products are now in “${target.name}”.` : `Deleted “${row.name}”.`,
      };
    });
  };

  const move = (index: number, by: -1 | 1) => {
    if (!list) return;
    const order = list.categories.map((c) => c.id);
    const to = index + by;
    if (to < 0 || to >= order.length) return;
    [order[index], order[to]] = [order[to], order[index]];
    void run(async () => ({ list: await adminApi.reorderProductCategories(order), change: { kind: "reordered" }, notice: "Order saved." }));
  };

  return (
    <Modal labelledBy={titleId} onClose={close} size="md" closing={closing}>
      <div className={dialog.form}>
        <DialogHead titleId={titleId} title="Product categories" onClose={close} />
        <div className={dialog.body}>
          <p className={dialog.intro}>
            The categories offered when a product is added or edited, in this order. Renaming one renames it on its products; deleting one that products use moves them to another category first.
          </p>
          {error && (
            <p className="w360-error-text" role="alert">
              {error}
            </p>
          )}
          <p className={styles.notice} role="status" aria-live="polite">
            {notice}
          </p>

          {load.status === "loading" && !list && <p className={styles.muted}>Loading categories…</p>}
          {load.status === "error" && !list && (
            <p className="w360-error-text" role="alert">
              {load.message}
            </p>
          )}
          {load.status === "forbidden" && !list && <p className={styles.muted}>You don&apos;t have access to the category list.</p>}

          {list && manage && (
            <form
              className={styles.addRow}
              onSubmit={(e) => {
                // Drawn on the body, but React passes the submit up to a form
                // this popup was opened from (the Listing popup): stop it here.
                e.preventDefault();
                e.stopPropagation();
                add(newName);
              }}
            >
              <label className="w360-label" htmlFor={newId}>
                New category
              </label>
              <div className={styles.addControls}>
                <input
                  id={newId}
                  className="w360-input"
                  value={newName}
                  maxLength={64}
                  placeholder="e.g. Field Service"
                  disabled={busy || full}
                  onChange={(e) => setNewName(e.target.value)}
                />
                <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || full || !tidy(newName)}>
                  Add category
                </button>
              </div>
              {full && <p className="w360-hint">The list holds at most {list.max_categories} categories. Delete one to add another.</p>}
            </form>
          )}

          {list && (
            <ul className={styles.list} aria-label="Categories">
              {list.categories.length === 0 && <li className={styles.empty}>No categories yet. Add the first one above.</li>}
              {list.categories.map((row, index) => (
                <li key={row.id} className={styles.row}>
                  {editing?.id === row.id ? (
                    <form
                      className={styles.editRow}
                      onSubmit={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        rename(row, editing.name);
                      }}
                    >
                      <input
                        className="w360-input"
                        aria-label={`New name for ${row.name}`}
                        value={editing.name}
                        maxLength={64}
                        autoFocus
                        disabled={busy}
                        onChange={(e) => setEditing({ id: row.id, name: e.target.value })}
                      />
                      <button type="submit" className={`w360-btn w360-btn-primary ${styles.small}`} disabled={busy || !tidy(editing.name)}>
                        Save
                      </button>
                      <button type="button" className={`w360-btn w360-btn-secondary ${styles.small}`} onClick={() => setEditing(null)}>
                        Cancel
                      </button>
                    </form>
                  ) : deleting?.id === row.id ? (
                    <div className={styles.deleteRow} role="group" aria-label={`Delete ${row.name}`}>
                      {row.products > 0 ? (
                        <label className={styles.moveLabel}>
                          Move the {productsLabel(row.products).toLowerCase()} in “{row.name}” to
                          <select className="w360-input" value={deleting.moveTo} disabled={busy} onChange={(e) => setDeleting({ id: row.id, moveTo: e.target.value })}>
                            <option value="">Choose a category</option>
                            {list.categories
                              .filter((c) => c.id !== row.id)
                              .map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                          </select>
                        </label>
                      ) : (
                        <span className={styles.deleteQuestion}>Delete “{row.name}”?</span>
                      )}
                      <div className={styles.rowActions}>
                        <button type="button" className={`w360-btn w360-btn-danger ${styles.small}`} disabled={busy || (row.products > 0 && !deleting.moveTo)} onClick={() => remove(row, deleting.moveTo)}>
                          Delete
                        </button>
                        <button type="button" className={`w360-btn w360-btn-secondary ${styles.small}`} onClick={() => setDeleting(null)}>
                          Keep it
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <span className={styles.name}>{row.name}</span>
                      <span className={styles.count}>{productsLabel(row.products)}</span>
                      {manage && (
                        <span className={styles.rowActions}>
                          <button type="button" className={styles.iconButton} aria-label={`Move ${row.name} up`} disabled={busy || index === 0} onClick={() => move(index, -1)}>
                            <Icon name="arrowUp" size={16} />
                          </button>
                          <button type="button" className={styles.iconButton} aria-label={`Move ${row.name} down`} disabled={busy || index === list.categories.length - 1} onClick={() => move(index, 1)}>
                            <Icon name="arrowDown" size={16} />
                          </button>
                          <button
                            type="button"
                            className={styles.iconButton}
                            aria-label={`Rename ${row.name}`}
                            disabled={busy}
                            onClick={() => {
                              setDeleting(null);
                              setEditing({ id: row.id, name: row.name });
                            }}
                          >
                            <Icon name="edit" size={16} />
                          </button>
                          <button
                            type="button"
                            className={`${styles.iconButton} ${styles.danger}`}
                            aria-label={`Delete ${row.name}`}
                            disabled={busy}
                            onClick={() => {
                              setEditing(null);
                              setDeleting({ id: row.id, moveTo: "" });
                            }}
                          >
                            <Icon name="trash" size={16} />
                          </button>
                        </span>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}

          {list && list.unlisted.length > 0 && (
            <section className={styles.unlisted} aria-label="In use but not on the list">
              <h3 className={styles.unlistedTitle}>In use but not on the list</h3>
              <p className="w360-hint">Products carry these from before. Add one to offer it in the category field.</p>
              <ul className={styles.list}>
                {list.unlisted.map((u) => (
                  <li key={u.name} className={styles.row}>
                    <span className={styles.name}>{u.name}</span>
                    <span className={styles.count}>{productsLabel(u.products)}</span>
                    {manage && (
                      <span className={styles.rowActions}>
                        <button type="button" className={`w360-btn w360-btn-secondary ${styles.small}`} disabled={busy || full} onClick={() => add(u.name)}>
                          Add to list
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
        <div className={dialog.foot}>
          <button type="button" className="w360-btn w360-btn-primary" onClick={close}>
            Done
          </button>
        </div>
      </div>
    </Modal>
  );
}
