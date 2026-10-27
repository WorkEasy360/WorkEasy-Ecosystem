import type { ExperienceLabel } from "@/lib/api/partnerConsole";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/** The derived labels ("Reseller", "White-label", "Affiliate") a partner wears.
 * The server computes them from the capabilities the partner holds; this only
 * renders them - nothing in the browser derives or stores a kind of partner. */
export function ExperienceLabels({ labels }: { labels: ExperienceLabel[] }) {
  if (labels.length === 0) return <span className={styles.muted}>No capabilities yet</span>;
  return (
    <ul className={local.labels} aria-label="Derived from capabilities">
      {labels.map((l) => (
        <li key={l.key} className={local.label}>
          {l.label}
        </li>
      ))}
    </ul>
  );
}

/** The partner-type badges of the All Partners table and the partner's header
 * (ADR-048): one per label the server derived from the grants - a partner that
 * holds several capabilities shows several badges. Drawn in capitals by CSS; the
 * text is the label itself. */
export function TypeBadges({ labels }: { labels: ExperienceLabel[] }) {
  if (labels.length === 0) return <span className={styles.muted}>No capabilities yet</span>;
  return (
    <ul className={local.labels} aria-label="Partner type, from its capabilities">
      {labels.map((l) => (
        <li key={l.key} className={`${local.typeBadge} ${local[`type_${l.key}`] ?? ""}`}>
          {l.label}
        </li>
      ))}
    </ul>
  );
}

const PRIMARY_LABELS: Record<string, string> = {
  reseller: "Reseller",
  white_label: "White-label",
  affiliate: "Affiliate",
};

/** The one partner type the All Partners table shows: the workspace the
 * partner's portal opens (the server's `primary_experience` - after an upgrade,
 * the new one). A partner may hold more than one set of capabilities; this is
 * its current type, a display only, never an authority. */
export function PrimaryType({ value }: { value: string | null }) {
  if (!value) return <span className={styles.muted}>Not set</span>;
  return (
    <span className={`${local.typeBadge} ${local[`type_${value}`] ?? ""}`}>{PRIMARY_LABELS[value] ?? value.replace(/_/g, " ")}</span>
  );
}
