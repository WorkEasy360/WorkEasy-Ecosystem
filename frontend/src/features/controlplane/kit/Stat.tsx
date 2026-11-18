import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import k from "./Kit.module.css";

export type StatTone = "green" | "blue" | "purple" | "orange" | "red";

/**
 * One KPI card: icon, label, value, and an optional change ("↑ 12%" green,
 * "↓ 3%" red, "0%" grey - no change is never drawn as growth) and note.
 */
export function StatCard({
  label,
  value,
  icon,
  tone = "green",
  delta,
  note,
  href,
}: {
  label: string;
  value: React.ReactNode;
  icon?: string;
  tone?: StatTone;
  /** Change in percent against the previous period (null: not comparable). */
  delta?: number | null;
  /** A short line under the value ("vs last month", "3 need review"). */
  note?: React.ReactNode;
  href?: string;
}) {
  const body = (
    <>
      <p className={k.statLabel}>{label}</p>
      {icon && (
        <span className={k.statIcon} data-tone={tone}>
          <Icon name={icon} size={18} />
        </span>
      )}
      <p className={k.statValue}>{value}</p>
      {(delta !== undefined && delta !== null) || note ? (
        <p className={k.statFoot}>
          {delta !== undefined && delta !== null && <Delta value={delta} />}
          {note && <span>{note}</span>}
        </p>
      ) : null}
    </>
  );
  return href ? (
    <Link href={href} className={k.stat}>
      {body}
    </Link>
  ) : (
    <div className={k.stat}>{body}</div>
  );
}

export function Delta({ value }: { value: number }) {
  const rounded = Math.round(value * 10) / 10;
  const dir = rounded > 0 ? "up" : rounded < 0 ? "down" : "flat";
  const text = `${Math.abs(rounded)}%`;
  return (
    <span className={k.delta} data-dir={dir}>
      {dir === "up" && <Icon name="arrowUp" size={12} />}
      {dir === "down" && <Icon name="arrowDown" size={12} />}
      <span className="w360-visually-hidden">{dir === "up" ? "Up " : dir === "down" ? "Down " : "No change, "}</span>
      {text}
    </span>
  );
}

/** KPI cards on a grid sized to their number, so no row is left with an orphan
 * card (3 -> 3, 4 -> 4, 5 -> 5, 6 -> 3 + 3). */
export function StatGrid({ children, label }: { children: React.ReactNode; label?: string }) {
  const count = Array.isArray(children) ? children.filter(Boolean).length : 1;
  return (
    <div className={k.statGrid} data-count={Math.min(count, 6)} role={label ? "group" : undefined} aria-label={label}>
      {children}
    </div>
  );
}
