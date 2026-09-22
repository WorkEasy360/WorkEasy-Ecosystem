import type { AppDirectoryEntry } from "@/lib/api/types";
import { LaunchForm } from "./LaunchForm";
import styles from "./AppCard.module.css";

const STATE_COPY: Record<AppDirectoryEntry["state"], { badge: string; tone: string; hint?: string }> = {
  active: { badge: "", tone: "" },
  no_seat: {
    badge: "No seat",
    tone: "w360-badge-warning",
    hint: "Your organization owns this product, but you haven't been assigned a seat. Ask an administrator.",
  },
  no_entitlement: {
    badge: "Not activated",
    tone: "w360-badge-neutral",
    hint: "Contact your Workeasy360 account team to activate this product.",
  },
  integration_suspended: {
    badge: "Temporarily unavailable",
    tone: "w360-badge-danger",
    hint: "This product is temporarily unavailable. Please try again shortly.",
  },
  product_unavailable: {
    badge: "Coming soon",
    tone: "w360-badge-neutral",
  },
};

/** Task 23: no external image URL - a deterministic monogram, generated
 * from data the platform already controls (the product's own name). */
function monogram(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return letters || "?";
}

export function AppCard({
  orgSlug,
  app,
}: {
  orgSlug: string;
  app: AppDirectoryEntry;
}) {
  const copy = STATE_COPY[app.state];

  return (
    <article className={`w360-card ${styles.card}`} aria-labelledby={`app-${app.product_code}`}>
      <div className={styles.top}>
        <span className={styles.icon} aria-hidden="true">
          {monogram(app.display_name)}
        </span>
        <div>
          <h2 id={`app-${app.product_code}`} className={styles.name}>
            {app.display_name}
          </h2>
          {copy.badge && (
            <span className={`w360-badge ${copy.tone}`} style={{ marginTop: 4 }}>
              {copy.badge}
            </span>
          )}
        </div>
      </div>
      <p className={styles.description}>{app.description || copy.hint}</p>
      <div className={styles.footer}>
        {app.state === "active" && <LaunchForm orgSlug={orgSlug} productCode={app.product_code} />}
        {app.state === "no_seat" && (
          <span className="w360-badge w360-badge-neutral">Ask an admin</span>
        )}
      </div>
    </article>
  );
}
