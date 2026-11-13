"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { buttonClass, type ButtonSize, type ButtonVariant } from "./Button";
import k from "./Kit.module.css";

const noSubscription = () => () => {};
const useInBrowser = () => useSyncExternalStore(noSubscription, () => true, () => false);
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * A floating panel drawn on <body> and placed under its trigger, so a table's
 * scroll container never clips it. Placed through the CSSOM (no style
 * attribute is rendered). Closes on Escape, an outside click, scroll and resize.
 */
export function usePopover<T extends HTMLElement = HTMLButtonElement, P extends HTMLElement = HTMLDivElement>(
  align: "start" | "end" = "start",
) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<T | null>(null);
  const panel = useRef<P | null>(null);

  const place = useCallback(() => {
    const t = trigger.current;
    const p = panel.current;
    if (!t || !p) return;
    const r = t.getBoundingClientRect();
    p.style.position = "fixed";
    p.style.minWidth = `${Math.max(r.width, 160)}px`;
    const width = p.offsetWidth || 200;
    const height = p.offsetHeight || 0;
    let left = align === "end" ? r.right - width : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    let top = r.bottom + 4;
    if (height && top + height > window.innerHeight - 8 && r.top - height - 4 > 8) top = r.top - height - 4;
    p.style.left = `${left}px`;
    p.style.top = `${top}px`;
    p.style.right = "auto";
  }, [align]);

  useIsoLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (panel.current?.contains(target) || trigger.current?.contains(target)) return;
      setOpen(false);
    };
    const onScroll = (e: Event) => {
      if (panel.current && e.target instanceof Node && panel.current.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  return { open, setOpen, triggerRef: trigger, panelRef: panel };
}

export interface MenuItem {
  label: string;
  icon?: string;
  onSelect?: () => void;
  href?: string;
  /** Opens in a new tab (an external URL). */
  external?: boolean;
  danger?: boolean;
  disabled?: boolean;
  /** A line above this item, to set apart a group (Sign out, Terminate). */
  separator?: boolean;
  /** Hidden when false. */
  when?: boolean;
}

/**
 * A menu button: Enter/Space/ArrowDown opens it on the first item, arrow keys
 * move, Home/End jump, Escape closes it and gives focus back, and choosing an
 * item closes it. Items are actions (buttons) or pages (links).
 */
export function Menu({
  label,
  items,
  icon,
  showLabel = true,
  variant = "secondary",
  size = "md",
  align = "end",
  header,
  triggerClassName,
  triggerContent,
}: {
  label: string;
  items: MenuItem[];
  icon?: string;
  /** false: an icon-only trigger (the row's "⋯"); the label is its name. */
  showLabel?: boolean;
  variant?: ButtonVariant;
  size?: ButtonSize;
  align?: "start" | "end";
  /** Content above the items (the account menu's name and email). */
  header?: React.ReactNode;
  triggerClassName?: string;
  /** Custom trigger content (the account button's avatar and name). */
  triggerContent?: React.ReactNode;
}) {
  const shown = items.filter((i) => i.when !== false);
  const { open, setOpen, triggerRef: trigger, panelRef: panel } = usePopover(align);
  const inBrowser = useInBrowser();
  const menuId = useId();
  const refs = useRef<(HTMLElement | null)[]>([]);
  const [focusIndex, setFocusIndex] = useState<number | null>(null);

  const enabled = shown.map((item, i) => (item.disabled ? -1 : i)).filter((i) => i >= 0);

  useEffect(() => {
    if (open && focusIndex !== null) refs.current[focusIndex]?.focus();
  }, [open, focusIndex]);

  const close = (focusTrigger = true) => {
    setOpen(false);
    setFocusIndex(null);
    if (focusTrigger) trigger.current?.focus();
  };

  const move = (delta: number) => {
    if (enabled.length === 0) return;
    const at = focusIndex === null ? -1 : enabled.indexOf(focusIndex);
    const next = enabled[(at + delta + enabled.length) % enabled.length];
    setFocusIndex(next);
  };

  const openAt = (where: "first" | "last") => {
    setOpen(true);
    setFocusIndex(where === "first" ? (enabled[0] ?? null) : (enabled[enabled.length - 1] ?? null));
  };

  if (shown.length === 0) return null;

  const onPanelKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      move(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(-1);
    } else if (e.key === "Home") {
      e.preventDefault();
      setFocusIndex(enabled[0] ?? null);
    } else if (e.key === "End") {
      e.preventDefault();
      setFocusIndex(enabled[enabled.length - 1] ?? null);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      close(false);
    }
  };

  const iconOnly = !showLabel && !triggerContent;
  return (
    <span className={k.menuWrap}>
      <button
        ref={trigger}
        type="button"
        className={triggerClassName ?? buttonClass(variant, size, iconOnly)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={iconOnly || triggerContent ? label : undefined}
        title={iconOnly ? label : undefined}
        onClick={(e) => {
          e.stopPropagation();
          if (open) close(false);
          else openAt("first");
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            openAt("first");
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            openAt("last");
          }
        }}
      >
        {triggerContent ?? (
          <>
            {icon && <Icon name={icon} size={16} />}
            {showLabel && <span>{label}</span>}
            {showLabel && <Icon name="chevronDown" size={16} />}
          </>
        )}
      </button>
      {open &&
        inBrowser &&
        createPortal(
          <div
            ref={panel}
            id={menuId}
            role="menu"
            aria-label={label}
            className={k.menu}
            onKeyDown={onPanelKey}
            onClick={(e) => e.stopPropagation()}
          >
            {header && (
              <>
                <div className={k.menuHead} role="presentation">
                  {header}
                </div>
                <div className={k.menuSep} role="separator" />
              </>
            )}
            {shown.map((item, i) => {
              const cls = `${k.menuItem} ${item.danger ? k.menuItemDanger : ""}`.trim();
              const content = (
                <>
                  {item.icon && <Icon name={item.icon} size={16} />}
                  <span>{item.label}</span>
                  {item.external && <Icon name="external" size={14} />}
                </>
              );
              const setRef = (el: HTMLElement | null) => {
                refs.current[i] = el;
              };
              return (
                <div key={`${item.label}-${i}`} role="none">
                  {item.separator && i > 0 && <div className={k.menuSep} role="separator" />}
                  {item.href ? (
                    item.external ? (
                      <a
                        ref={setRef}
                        role="menuitem"
                        tabIndex={-1}
                        className={cls}
                        href={item.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={() => close(false)}
                      >
                        {content}
                      </a>
                    ) : (
                      <Link ref={setRef} role="menuitem" tabIndex={-1} className={cls} href={item.href} onClick={() => close(false)}>
                        {content}
                      </Link>
                    )
                  ) : (
                    <button
                      ref={setRef}
                      type="button"
                      role="menuitem"
                      tabIndex={-1}
                      className={cls}
                      disabled={item.disabled}
                      onClick={() => {
                        close();
                        item.onSelect?.();
                      }}
                    >
                      {content}
                    </button>
                  )}
                </div>
              );
            })}
          </div>,
          document.body,
        )}
    </span>
  );
}

/** The row's one right-aligned "⋯" menu. */
export function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  return <Menu label={label} items={items} icon="more" showLabel={false} variant="ghost" size="sm" align="end" />;
}
