"use client";

import { useEffect, useId, useRef } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { ButtonLink, CopyButton } from "./Button";
import { Menu, type MenuItem } from "./Menu";
import { InfoTip, Tooltip } from "./Tooltip";
import { EMPTY } from "./format";
import k from "./Kit.module.css";

const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(" ") || undefined;

// --- browser title -------------------------------------------------------------------

export const ADMIN_TITLE_SUFFIX = "WorkEasy360 Admin";
export const PARTNER_TITLE_SUFFIX = "WorkEasy360 Partner";
/** The User Admin panel (`<html data-console="user">`, the organization console). */
export const USER_TITLE_SUFFIX = "WorkEasy360";

/** "Users · WorkEasy360 Admin" while the page is open; "Customers · WorkEasy360
 * Partner" in the Partner panel (`<html data-console="partner">`). */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return;
    const before = document.title;
    const consoleName = document.documentElement.dataset.console;
    const suffix = consoleName === "partner" ? PARTNER_TITLE_SUFFIX : consoleName === "user" ? USER_TITLE_SUFFIX : ADMIN_TITLE_SUFFIX;
    document.title = `${title} · ${suffix}`;
    return () => {
      document.title = before;
    };
  }, [title]);
}

// --- page + header ---------------------------------------------------------------------

/** The page container: the same width and spacing on every console page. */
export function Page({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cx(k.page, className)}>{children}</div>;
}

export interface Crumb {
  label: string;
  href?: string;
}

/**
 * Every page's header, at the same place: breadcrumb (detail pages), the
 * title, ONE line of subtitle (a longer explanation goes in `info`, behind an
 * (i)), the primary action on the right with secondary actions in a "More"
 * menu, and optional tabs below. Also sets the browser tab's title.
 */
export function PageHeader({
  title,
  subtitle,
  info,
  crumbs,
  primary,
  actions,
  secondary,
  tabs,
  badges,
  meta,
  avatar,
  documentTitle,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  /** A longer explanation, behind an (i) after the subtitle. */
  info?: React.ReactNode;
  crumbs?: Crumb[];
  /** The page's main action (one). */
  primary?: React.ReactNode;
  /** More buttons beside the primary one (keep to one or two). */
  actions?: React.ReactNode;
  /** Secondary actions, in a "More actions" menu. */
  secondary?: MenuItem[];
  tabs?: React.ReactNode;
  /** Status badges beside the title (a detail page). */
  badges?: React.ReactNode;
  /** Key facts under the title (a detail page): small items. */
  meta?: React.ReactNode[];
  avatar?: React.ReactNode;
  /** The browser tab's title when `title` is not plain text. */
  documentTitle?: string;
}) {
  usePageTitle(documentTitle ?? (typeof title === "string" ? title : null));
  const hasSecondary = secondary && secondary.some((i) => i.when !== false);
  return (
    <header className={k.header}>
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol className={k.crumbs}>
            {crumbs.map((c, i) => (
              <li key={`${c.label}-${i}`}>
                {i > 0 && <Icon name="chevronRight" size={12} />}
                {c.href && i < crumbs.length - 1 ? (
                  <Link href={c.href}>{c.label}</Link>
                ) : (
                  <span aria-current={i === crumbs.length - 1 ? "page" : undefined}>{c.label}</span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className={k.headRow}>
        {avatar && <span className={k.headAvatar}>{avatar}</span>}
        <div className={k.headText}>
          <div className={k.titleRow}>
            <h1 className={k.title}>{title}</h1>
            {badges}
          </div>
          {(subtitle || info) && (
            <p className={k.subtitle}>
              {subtitle}
              {info && <InfoTip label="About this page">{info}</InfoTip>}
            </p>
          )}
          {meta && meta.length > 0 && (
            <ul className={k.meta}>
              {meta.filter(Boolean).map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ul>
          )}
        </div>
        {(primary || actions || hasSecondary) && (
          <div className={k.headActions}>
            {hasSecondary && <Menu label="More actions" items={secondary!} icon="more" showLabel={false} />}
            {actions}
            {primary}
          </div>
        )}
      </div>
      {tabs}
    </header>
  );
}

// --- tabs ----------------------------------------------------------------------------

export interface LinkTab {
  href: string;
  label: string;
  count?: number;
  current: boolean;
}

/** Tabs that are pages (a section's sub-pages): links, the current one marked. */
export function LinkTabs({ label, tabs }: { label: string; tabs: LinkTab[] }) {
  return (
    <nav aria-label={label} className={k.tabs}>
      {tabs.map((t) => (
        <Link key={t.href} href={t.href} className={k.tab} aria-current={t.current ? "page" : undefined}>
          {t.label}
          {t.count !== undefined && <span className={k.tabCount}>{t.count}</span>}
        </Link>
      ))}
    </nav>
  );
}

export interface StateTab<K extends string> {
  key: K;
  label: string;
  count?: number;
}

/** WAI-ARIA tabs within one page (arrow keys, Home/End), the underline style. */
export function Tabs<K extends string>({
  label,
  tabs,
  selected,
  onSelect,
  children,
}: {
  label: string;
  tabs: StateTab<K>[];
  selected: K;
  onSelect: (key: K) => void;
  children?: React.ReactNode;
}) {
  const base = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKey = (e: React.KeyboardEvent, index: number) => {
    let to: number | null = null;
    if (e.key === "ArrowRight") to = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft") to = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = tabs.length - 1;
    if (to === null) return;
    e.preventDefault();
    onSelect(tabs[to].key);
    refs.current[tabs[to].key]?.focus();
  };
  return (
    <>
      <div role="tablist" aria-label={label} className={k.tabs}>
        {tabs.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[t.key] = el;
            }}
            type="button"
            role="tab"
            id={`${base}-${t.key}`}
            aria-selected={t.key === selected}
            aria-controls={children !== undefined ? `${base}-panel` : undefined}
            tabIndex={t.key === selected ? 0 : -1}
            className={k.tab}
            onClick={() => onSelect(t.key)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
            {t.count !== undefined && <span className={k.tabCount}>{t.count}</span>}
          </button>
        ))}
      </div>
      {children !== undefined && (
        <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-${selected}`} className={k.tabPanel} tabIndex={0}>
          {children}
        </div>
      )}
    </>
  );
}

// --- card, section header, description list -------------------------------------------

export function Card({
  children,
  title,
  description,
  actions,
  flush = false,
  fill = false,
  labelledBy,
  as = "section",
}: {
  children: React.ReactNode;
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  /** No padding (a table or a list that runs edge to edge). */
  flush?: boolean;
  /** Fill the grid cell (equal heights in a row). */
  fill?: boolean;
  labelledBy?: string;
  as?: "section" | "div";
}) {
  const id = useId();
  const Tag = as;
  const titled = title !== undefined;
  return (
    <Tag className={cx(k.card, fill && k.cardFill)} aria-labelledby={titled ? (labelledBy ?? id) : undefined}>
      <div className={cx(k.cardBody, flush && k.cardFlush, fill && k.cardFill)}>
        {titled && <SectionHeader id={labelledBy ?? id} title={title} description={description} actions={actions} />}
        {children}
      </div>
    </Tag>
  );
}

/** A section's title, a one-line description and its action on the right. */
export function SectionHeader({
  title,
  description,
  actions,
  id,
  level = 2,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  id?: string;
  level?: 2 | 3;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className={k.sectionHead}>
      <div className={k.sectionText}>
        <H id={id} className={k.sectionTitle}>
          {title}
        </H>
        {description && <p className={k.sectionDesc}>{description}</p>}
      </div>
      {actions && <div className={k.sectionActions}>{actions}</div>}
    </div>
  );
}

export interface DescriptionItem {
  label: string;
  value: React.ReactNode;
  when?: boolean;
}

/** Labels and values on an aligned grid; an empty value is "—". */
export function DescriptionList({ items, columns = 1 }: { items: DescriptionItem[]; columns?: 1 | 2 }) {
  return (
    <dl className={k.dl} data-columns={columns}>
      {items
        .filter((i) => i.when !== false)
        .map((i) => (
          <Pair key={i.label} label={i.label} value={i.value} />
        ))}
    </dl>
  );
}

function Pair({ label, value }: { label: string; value: React.ReactNode }) {
  const blank = value === null || value === undefined || value === "" || value === false;
  return (
    <>
      <dt>{label}</dt>
      <dd>{blank ? EMPTY : value}</dd>
    </>
  );
}

// --- alert, empty state, skeleton --------------------------------------------------------

export type Tone = "success" | "warning" | "danger" | "info" | "neutral" | "brand";

const ALERT_ICON: Record<Tone, string> = {
  success: "checkCircle",
  warning: "alert",
  danger: "alert",
  info: "info",
  neutral: "info",
  brand: "info",
};

/** A banner inside the page (not a toast): an icon, a title, a line of text and
 * its actions. `danger` and `warning` are announced. */
export function Alert({
  tone = "info",
  title,
  children,
  actions,
}: {
  tone?: Tone;
  title?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const role = tone === "danger" || tone === "warning" ? "alert" : "status";
  return (
    <div className={cx(k.alert, k[`tone-${tone}`])} role={role}>
      <Icon name={ALERT_ICON[tone]} size={18} />
      <div className={k.alertBody}>
        {title && <p className={k.alertTitle}>{title}</p>}
        {children && <div className={k.alertText}>{children}</div>}
      </div>
      {actions && <div className={k.alertActions}>{actions}</div>}
    </div>
  );
}

/** One empty-state style everywhere: an icon, a title, a one-line hint and a call to action. */
export function EmptyState({
  icon = "box",
  title,
  hint,
  action,
  compact = false,
}: {
  icon?: string;
  title: string;
  hint?: React.ReactNode;
  action?: React.ReactNode | { label: string; href: string; icon?: string };
  compact?: boolean;
}) {
  const cta =
    action && typeof action === "object" && "href" in (action as object) && "label" in (action as object) ? (
      <ButtonLink
        href={(action as { href: string }).href}
        variant="primary"
        icon={(action as { icon?: string }).icon ?? "plus"}
      >
        {(action as { label: string }).label}
      </ButtonLink>
    ) : (
      (action as React.ReactNode)
    );
  return (
    <div className={cx(k.empty, compact && k.compact)}>
      <span className={k.emptyIcon}>
        <Icon name={icon} size={20} />
      </span>
      <p className={k.emptyTitle}>{title}</p>
      {hint && <p className={k.emptyHint}>{hint}</p>}
      {cta && <div className={k.emptyAction}>{cta}</div>}
    </div>
  );
}

/** A grey bar where content is loading. */
export function Skeleton({ width = "100%", height = 12 }: { width?: string; height?: number }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    ref.current.style.width = width;
    ref.current.style.height = `${height}px`;
  }, [width, height]);
  return <span ref={ref} className={k.skeleton} aria-hidden="true" />;
}

/** A loading block: a few skeleton lines, announced once. */
export function LoadingBlock({ what, lines = 3 }: { what: string; lines?: number }) {
  return (
    <div className={k.cardBody} aria-live="polite" aria-busy="true">
      <span className="w360-visually-hidden">Loading {what}…</span>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={i === 0 ? "40%" : i % 2 ? "90%" : "70%"} />
      ))}
    </div>
  );
}

// --- avatars, people, IDs, long text ------------------------------------------------------

export function initialsOf(name: string): string {
  const parts = name.trim().split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

export function Avatar({ name, size = "md" }: { name: string; size?: "md" | "lg" }) {
  return (
    <span className={cx(k.avatar, size === "lg" && k.avatarLg)} aria-hidden="true">
      {initialsOf(name)}
    </span>
  );
}

/** One line of text cut with an ellipsis, shown whole on hover. */
export function Trunc({ children, max }: { children: string | null | undefined; max?: "sm" | "md" | "lg" }) {
  const ref = useRefWidth(max);
  if (!children) return <>{EMPTY}</>;
  return (
    <span ref={ref} className={k.trunc} title={children}>
      {children}
    </span>
  );
}

function useRefWidth(max: "sm" | "md" | "lg" | undefined) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    if (ref.current && max) ref.current.style.maxWidth = max === "sm" ? "16ch" : max === "lg" ? "44ch" : "28ch";
  }, [max]);
  return ref;
}

/** A person in a table: avatar, name (a link when `href`), email under it. */
export function PersonCell({
  name,
  email,
  href,
  badge,
  showAvatar = true,
}: {
  name: string | null | undefined;
  email?: string | null;
  href?: string;
  badge?: React.ReactNode;
  showAvatar?: boolean;
}) {
  const primary = name || email || EMPTY;
  return (
    <span className={k.person}>
      {showAvatar && <Avatar name={primary} />}
      <span className={k.cellStack}>
        <span className={k.row}>
          {href ? (
            <Link href={href} className={k.cellLink}>
              <Trunc>{primary}</Trunc>
            </Link>
          ) : (
            <Trunc>{primary}</Trunc>
          )}
          {badge}
        </span>
        {name && email && (
          <span className={k.cellSub}>
            <Trunc>{email}</Trunc>
          </span>
        )}
      </span>
    </span>
  );
}

/** An ID or IP: monospace, cut to fit, with a copy button. */
export function IdCell({ value, label = "Copy ID", short = true }: { value: string | null | undefined; label?: string; short?: boolean }) {
  if (!value) return <>{EMPTY}</>;
  const text = short && value.length > 13 ? `${value.slice(0, 8)}…` : value;
  return (
    <span className={k.idCell}>
      <Tooltip content={value}>
        <code className={k.mono} tabIndex={0}>
          {text}
        </code>
      </Tooltip>
      <CopyButton value={value} label={label} />
    </span>
  );
}

/** A cell with a main line and a muted second line. */
export function Stack({ main, sub }: { main: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <span className={k.cellStack}>
      <span>{main}</span>
      {sub && <span className={k.cellSub}>{sub}</span>}
    </span>
  );
}

/** A URL cut to fit, with copy and open buttons. */
export function UrlCell({ url }: { url: string | null | undefined }) {
  if (!url) return <>{EMPTY}</>;
  return (
    <span className={k.idCell}>
      <Trunc max="md">{url.replace(/^https?:\/\//, "")}</Trunc>
      <CopyButton value={url} label="Copy URL" />
      <a className={k.copy} href={url} target="_blank" rel="noopener noreferrer" aria-label="Open in a new tab" title="Open in a new tab">
        <Icon name="external" size={14} />
      </a>
    </span>
  );
}

/** A small muted raw key (an event or template key), for the curious. */
export function KeyText({ children }: { children: string }) {
  return <code className={`${k.mono} ${k.muted}`}>{children}</code>;
}

export const kit = k;
