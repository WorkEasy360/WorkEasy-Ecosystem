"use client";

import { cloneElement, isValidElement, useId, useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import k from "./Kit.module.css";

/**
 * A tooltip: shown on hover and on keyboard focus, hidden on Escape, linked to
 * its trigger with aria-describedby. The trigger must be focusable (a button,
 * a link) for keyboard users to reach it.
 */
export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: React.ReactNode;
  children: React.ReactElement;
  side?: "top" | "bottom" | "right";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!content) return children;
  const trigger = isValidElement(children)
    ? cloneElement(children as React.ReactElement<Record<string, unknown>>, { "aria-describedby": id })
    : children;
  return (
    <span
      className={k.tipWrap}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      {trigger}
      <span role="tooltip" id={id} className={open ? k.tip : "w360-visually-hidden"} data-side={side}>
        {content}
      </span>
    </span>
  );
}

/** A small (i) that explains a page or a term without a paragraph of copy. */
export function InfoTip({ children, label = "More information" }: { children: React.ReactNode; label?: string }) {
  return (
    <Tooltip content={children} side="bottom">
      <button type="button" className={k.infoButton} aria-label={label}>
        <Icon name="info" size={16} />
      </button>
    </Tooltip>
  );
}
