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
