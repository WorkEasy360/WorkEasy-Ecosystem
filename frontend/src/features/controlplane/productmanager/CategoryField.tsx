"use client";

import { useState } from "react";
import { adminApi } from "@/lib/api/controlPlane";
import { describeError } from "../ui";
import { CategoryManager, optionsOf, type CategoryChange } from "./CategoryManager";
import styles from "./Categories.module.css";

/** The select's last option: type a new category instead of choosing one. */
const NEW = "__new_category__";
const tidy = (raw: string) => raw.split(/\s+/).filter(Boolean).join(" ");

/**
 * A product's category (2026-11-16, the owner: "make category customisable"):
 * one of the Super Admin's own categories, or **+ New category…** to type one
 * (added to the list for every product), and **Manage categories** to rename,
 * reorder or delete them in a popup. The control only - the caller draws the
 * label (`id` is the control's id) and the field's error.
 */
export function CategoryField({
  id,
  value,
  options,
  onChange,
  canManage,
  disabled,
  invalid,
  describedBy,
  onListChange,
}: {
  id: string;
  value: string;
  /** The categories offered (the list, then any a product carries that is not on it). */
  options: string[];
  onChange: (name: string) => void;
  /** Adding, renaming and deleting need the catalog manage grant. */
  canManage: boolean;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  /** After the list changed here (the page's filter can follow it). */
  onListChange?: (options: string[]) => void;
}) {
  const [fresh, setFresh] = useState<string[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [managing, setManaging] = useState(false);

  const listed = fresh ?? options;
  const shown = value && !listed.includes(value) ? [...listed, value] : listed;
  const errorId = `${id}-new-error`;

  const settle = (next: string[]) => {
    setFresh(next);
    onListChange?.(next);
  };

  async function add() {
    const clean = tidy(name);
    if (!clean) {
      setError("Name the category.");
      return;
    }
    const existing = shown.find((c) => c.toLowerCase() === clean.toLowerCase());
    if (existing) {
      onChange(existing);
      setAdding(false);
      setName("");
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const row = await adminApi.createProductCategory(clean);
      settle(optionsOf(await adminApi.productCategories()));
      onChange(row.name);
      setAdding(false);
      setName("");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const follow = (change: CategoryChange, next: string[]) => {
    settle(next);
    if (change.kind === "renamed" && value === change.from) onChange(change.to);
    if (change.kind === "deleted" && value === change.name) onChange(change.movedTo ?? "");
  };

  return (
    <div className={styles.field}>
      {adding ? (
        <div className={styles.addControls}>
          <input
            id={id}
            className="w360-input"
            value={name}
            maxLength={64}
            placeholder="New category name"
            autoFocus
            disabled={busy || disabled}
            aria-invalid={error ? true : undefined}
            aria-describedby={[error ? errorId : "", describedBy ?? ""].filter(Boolean).join(" ") || undefined}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              // Enter adds the category; it never submits the form around it.
              if (e.key === "Enter") {
                e.preventDefault();
                void add();
              }
            }}
          />
          <button type="button" className={`w360-btn w360-btn-primary ${styles.small}`} disabled={busy || !tidy(name)} aria-busy={busy || undefined} onClick={() => void add()}>
            Add
          </button>
          <button
            type="button"
            className={`w360-btn w360-btn-secondary ${styles.small}`}
            disabled={busy}
            onClick={() => {
              setAdding(false);
              setName("");
              setError(null);
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <select
          id={id}
          className="w360-input"
          value={value}
          disabled={disabled}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy}
          onChange={(e) => {
            if (e.target.value === NEW) {
              setError(null);
              setAdding(true);
              return;
            }
            onChange(e.target.value);
          }}
        >
          <option value="">Choose a category</option>
          {shown.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
          {canManage && <option value={NEW}>+ New category…</option>}
        </select>
      )}
      {error && (
        <p id={errorId} className="w360-error-text" role="alert">
          {error}
        </p>
      )}
      {canManage && !adding && (
        <button type="button" className={styles.manageLink} disabled={disabled} onClick={() => setManaging(true)}>
          Manage categories
        </button>
      )}
      {managing && <CategoryManager onClose={() => setManaging(false)} onChanged={(change, list) => follow(change, optionsOf(list))} />}
    </div>
  );
}
