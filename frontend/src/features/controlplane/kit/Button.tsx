"use client";

import { forwardRef, useId, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import k from "./Kit.module.css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "link";
export type ButtonSize = "sm" | "md";

const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(" ");

export function buttonClass(variant: ButtonVariant = "secondary", size: ButtonSize = "md", iconOnly = false, extra?: string) {
  return cx(k.btn, k[variant], size === "sm" && k.sm, iconOnly && k.iconOnly, extra);
}

type Common = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** An icon before the label (a name from components/sidebar/icons). */
  icon?: string;
  /** An icon after the label (a chevron on a menu button). */
  iconEnd?: string;
  loading?: boolean;
};

/** The console's one button: primary / secondary / ghost / danger / link, in
 * two heights (36, 32 in tables and toolbars). The label never wraps; a long
 * one is cut with an ellipsis. `loading` keeps the width and shows a spinner. */
export const Button = forwardRef<
  HTMLButtonElement,
  Common & React.ButtonHTMLAttributes<HTMLButtonElement>
>(function Button({ variant = "secondary", size = "md", icon, iconEnd, loading, className, children, disabled, type, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      className={buttonClass(variant, size, false, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className={k.spinner} aria-hidden="true" /> : icon && <Icon name={icon} size={16} />}
      {children !== undefined && children !== null && <span>{children}</span>}
      {iconEnd && <Icon name={iconEnd} size={16} />}
    </button>
  );
});

/** A link drawn as a button (an action that opens a page). */
export function ButtonLink({
  href,
  variant = "secondary",
  size = "md",
  icon,
  iconEnd,
  className,
  children,
  ...rest
}: Omit<Common, "loading"> & { href: string } & Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href">) {
  const external = /^https?:\/\//.test(href);
  const content = (
    <>
      {icon && <Icon name={icon} size={16} />}
      <span>{children}</span>
      {iconEnd && <Icon name={iconEnd} size={16} />}
    </>
  );
  if (external) {
    return (
      <a href={href} className={buttonClass(variant, size, false, className)} {...rest}>
        {content}
      </a>
    );
  }
  return (
    <Link href={href} className={buttonClass(variant, size, false, className)} {...rest}>
      {content}
    </Link>
  );
}

/** A button that is only an icon: it always has an accessible name and the
 * same name as a tooltip. */
export const IconButton = forwardRef<
  HTMLButtonElement,
  { icon: string; label: string; variant?: ButtonVariant; size?: ButtonSize } & Omit<
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    "children"
  >
>(function IconButton({ icon, label, variant = "ghost", size = "md", className, type, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      aria-label={label}
      title={label}
      className={buttonClass(variant, size, true, className)}
      {...rest}
    >
      <Icon name={icon} size={16} />
    </button>
  );
});

/** Copies a value (an ID, a URL) and says so for two seconds. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  const statusId = useId();
  return (
    <>
      <button
        type="button"
        className={k.copy}
        aria-label={done ? "Copied" : label}
        title={done ? "Copied" : label}
        data-done={done || undefined}
        aria-describedby={statusId}
        onClick={(e) => {
          e.stopPropagation();
          const done = () => {
            setDone(true);
            window.setTimeout(() => setDone(false), 2000);
          };
          if (navigator.clipboard?.writeText) navigator.clipboard.writeText(value).then(done, () => undefined);
          else done();
        }}
      >
        <Icon name={done ? "check" : "copy"} size={14} />
      </button>
      <span id={statusId} className="w360-visually-hidden" aria-live="polite">
        {done ? "Copied" : ""}
      </span>
    </>
  );
}
