"use client";

import { useEffect, useId, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Icon } from "@/components/sidebar/icons";
import { usePopover } from "./Menu";
import k from "./Kit.module.css";

const noSubscription = () => () => {};

export interface SelectOption<V extends string = string> {
  value: V;
  label: string;
}

/**
 * The styled select (WAI-ARIA select-only combobox): a button showing the
 * choice and a listbox under it. Arrow keys move (and open it), Enter/Space
 * chooses, Escape closes, typing a letter jumps to the next option starting
 * with it. Never full width unless `fullWidth` (a field inside a form).
 */
export function Select<V extends string = string>({
  label,
  value,
  options,
  onChange,
  showLabel = false,
  prefix,
  size = "md",
  fullWidth = false,
  disabled = false,
  id,
}: {
  /** The accessible name ("Status", "Product"). */
  label: string;
  value: V;
  options: SelectOption<V>[];
  onChange: (value: V) => void;
  /** Draw the label above the control (in a form) rather than only naming it. */
  showLabel?: boolean;
  /** A short muted word inside the button before the value ("Status:"). */
  prefix?: string;
  size?: "sm" | "md";
  fullWidth?: boolean;
  disabled?: boolean;
  id?: string;
}) {
  const autoId = useId();
  const buttonId = id ?? `${autoId}-button`;
  const labelId = `${autoId}-label`;
  const listId = `${autoId}-list`;
  const { open, setOpen, triggerRef: trigger, panelRef: listRef } = usePopover<HTMLButtonElement, HTMLUListElement>("start");
  const inBrowser = useSyncExternalStore(noSubscription, () => true, () => false);
  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const [active, setActive] = useState(selectedIndex);

  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active, listRef]);

  const current = options.find((o) => o.value === value);

  const choose = (index: number) => {
    const option = options[index];
    if (option) onChange(option.value);
    setOpen(false);
    trigger.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    const last = options.length - 1;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        setActive(selectedIndex);
        setOpen(true);
        return;
      }
      setActive((a) => Math.min(last, Math.max(0, a + (e.key === "ArrowDown" ? 1 : -1))));
    } else if (e.key === "Home" && open) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && open) {
      e.preventDefault();
      setActive(last);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (open) choose(active);
      else {
        setActive(selectedIndex);
        setOpen(true);
      }
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "Tab" && open) {
      setOpen(false);
    } else if (e.key.length === 1 && /\S/.test(e.key)) {
      const ch = e.key.toLowerCase();
      const start = open ? active : selectedIndex;
      for (let step = 1; step <= options.length; step++) {
        const i = (start + step) % options.length;
        if (options[i].label.toLowerCase().startsWith(ch)) {
          if (open) setActive(i);
          else onChange(options[i].value);
          break;
        }
      }
    }
  };

  return (
    <span className={k.select} data-full={fullWidth || undefined} data-labelled={showLabel || undefined}>
      {showLabel ? (
        <label id={labelId} htmlFor={buttonId} className="w360-label">
          {label}
        </label>
      ) : null}
      <button
        ref={trigger}
        id={buttonId}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={showLabel ? undefined : label}
        aria-labelledby={showLabel ? `${labelId} ${buttonId}` : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        className={`${k.selectButton} ${size === "sm" ? k.sm : ""}`.trim()}
        data-full={fullWidth || undefined}
        disabled={disabled}
        onClick={() => {
          if (open) setOpen(false);
          else {
            setActive(selectedIndex);
            setOpen(true);
          }
        }}
        onKeyDown={onKeyDown}
      >
        <span className={k.selectValue}>
          {prefix && <span className={k.selectPrefix}>{prefix}</span>}
          {current?.label ?? "Choose…"}
        </span>
        <Icon name="chevronDown" size={16} />
      </button>
      {open &&
        inBrowser &&
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label={label}
            className={k.listbox}
            tabIndex={-1}
          >
            {options.map((option, index) => (
              <li
                key={option.value}
                id={`${listId}-${index}`}
                data-index={index}
                role="option"
                aria-selected={option.value === value}
                data-active={index === active || undefined}
                className={k.option}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(index)}
              >
                <span>{option.label}</span>
                {option.value === value && (
                  <span className={k.optionCheck}>
                    <Icon name="check" size={16} />
                  </span>
                )}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </span>
  );
}
