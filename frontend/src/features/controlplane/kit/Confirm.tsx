"use client";

import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { DialogHead, Modal, dialog } from "../Modal";
import { autoReason, describeError } from "../ui";
import { Button } from "./Button";
import k from "./Kit.module.css";

export interface ConfirmOptions {
  title: string;
  /** What will happen, in a sentence or two. */
  message: React.ReactNode;
  confirmLabel: string;
  /** Red confirm button (suspend, remove, terminate). */
  danger?: boolean;
  /** The person must type this word to confirm (Terminate, Remove Super Admin). */
  typeToConfirm?: string;
  /** Offer an optional reason, sent to the audit log (the automatic one otherwise). */
  reason?: boolean;
  /** For the automatic reason: the action's name (default: the confirm label). */
  actionName?: string;
  /** For the automatic reason: where the action is taken. */
  where?: string;
  /** Names the dialog's button row ("Confirm Suspend"). */
  groupLabel?: string;
  /** Runs the action; a refusal stays in the dialog. Resolves to a success message for a toast. */
  onConfirm: (reason: string) => Promise<string | void> | string | void;
}

/**
 * The dialog every destructive action goes through: what will happen, an
 * optional reason (recorded in the audit log; an automatic one is sent when
 * left blank - the server requires one), type-to-confirm for the gravest
 * actions, Cancel and the action. A failure is shown in the dialog.
 */
export function ConfirmDialog({ options, onClose }: { options: ConfirmOptions; onClose: () => void }) {
  const titleId = useId();
  const typeId = useId();
  const reasonId = useId();
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const mustType = options.typeToConfirm;
  const ready = !mustType || typed.trim().toLowerCase() === mustType.toLowerCase();

  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const text = reason.trim().length >= 10 ? reason.trim() : autoReason(options.actionName ?? options.confirmLabel, options.where);
      const message = await options.onConfirm(text);
      onClose();
      if (message) toast.success(message);
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  };

  return (
    <Modal labelledBy={titleId} onClose={onClose}>
      <form
        className={dialog.form}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <DialogHead titleId={titleId} title={options.title} onClose={onClose} />
        <div className={k.confirmBody}>
          <div>{options.message}</div>
          {options.reason && (
            <div className={k.confirmField}>
              <label htmlFor={reasonId} className={k.fieldLabel}>
                Reason (optional, kept in the audit log)
              </label>
              <textarea id={reasonId} className={k.field} value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
            </div>
          )}
          {mustType && (
            <div className={k.confirmField}>
              <label htmlFor={typeId} className={k.fieldLabel}>
                Type <strong>{mustType}</strong> to confirm
              </label>
              <input
                id={typeId}
                className={k.field}
                value={typed}
                autoComplete="off"
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
          )}
          {error && (
            <p className="w360-error-text" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className={dialog.foot} role="group" aria-label={options.groupLabel ?? options.confirmLabel}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={options.danger ? "danger" : "primary"} loading={busy} disabled={!ready}>
            {options.confirmLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** `ask(options)` opens a ConfirmDialog; render `dialog` once in the component. */
export function useConfirm() {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const ask = useCallback((next: ConfirmOptions) => setOptions(next), []);
  const element = options ? <ConfirmDialog options={options} onClose={() => setOptions(null)} /> : null;
  return { ask, dialog: element };
}

// --- toasts --------------------------------------------------------------------------

type ToastTone = "success" | "danger" | "info";
interface ToastItem {
  id: number;
  tone: ToastTone;
  text: string;
}

interface ToastApi {
  success: (text: string) => void;
  error: (text: string) => void;
  info: (text: string) => void;
}

const noop = () => undefined;
const ToastContext = createContext<ToastApi>({ success: noop, error: noop, info: noop });

export const useToast = () => useContext(ToastContext);

/** Short-lived messages at the bottom right after an action (5 s; an error
 * stays until closed). Announced politely; errors assertively. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const remove = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);
  const push = useCallback((tone: ToastTone, text: string) => {
    const id = next.current++;
    setItems((all) => [...all.slice(-3), { id, tone, text }]);
  }, []);
  const api = useMemo<ToastApi>(
    () => ({
      success: (t) => push("success", t),
      error: (t) => push("danger", t),
      info: (t) => push("info", t),
    }),
    [push],
  );
  return (
    <ToastContext.Provider value={api}>
      {children}
      <ol className={k.toasts} aria-label="Notifications">
        {items.map((t) => (
          <Toast key={t.id} item={t} onClose={() => remove(t.id)} />
        ))}
      </ol>
    </ToastContext.Provider>
  );
}

function Toast({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => {
    if (item.tone === "danger") return;
    const t = window.setTimeout(onClose, 5000);
    return () => window.clearTimeout(t);
  }, [item.tone, onClose]);
  return (
    <li className={k.toast} data-tone={item.tone}>
      <Icon name={item.tone === "danger" ? "alert" : item.tone === "success" ? "checkCircle" : "info"} size={16} />
      <p role={item.tone === "danger" ? "alert" : "status"}>{item.text}</p>
      <button type="button" className={k.toastClose} aria-label="Dismiss" onClick={onClose}>
        <Icon name="x" size={14} />
      </button>
    </li>
  );
}
