import Link from "next/link";
import type { AppDirectoryEntry } from "@/lib/api/types";
import { ProductIcon, productIconKnown } from "@/components/ProductIcon";
import { LaunchButton } from "./LaunchButton";
import styles from "./AppCard.module.css";

/**
 * What each *access* state says, and why the hint is its own line rather than
 * a fallback for a missing description.
 *
 * It used to be `{app.description || copy.hint}` - so the hint appeared only
 * for a product whose catalog entry had no description, which no real
 * product has. The result was a "Not activated" card that named no next step
 * at all, and a "No seat" card that never told anyone to ask an
 * administrator. Task 41's rule ("the UI must not lie", and must offer a
 * real call to action) is only met if the call to action is actually
 * rendered.
 */
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
    hint: "Your organization doesn't have this product yet. Explore products to buy it or start a trial.",
  },
  integration_suspended: {
    badge: "Temporarily unavailable",
    tone: "w360-badge-danger",
    hint: "This product is temporarily unavailable. Please try again shortly.",
  },
  product_unavailable: {
    badge: "Coming soon",
    tone: "w360-badge-neutral",
    hint: "This product isn't available on Workeasy360 yet.",
  },
};

const SELF_ASSIGN_HINT =
  "Your organization owns this product, but you don't hold a seat yet. Assign one - to yourself or a teammate - in Product access.";

/**
 * The *commercial* status (Phase 9/11) - a second, independent axis from the
 * access state above: an organization can hold a product (this badge) while
 * one particular member cannot open it yet (that one). "Suspended" here means
 * read-only, not revoked - the platform's policy is that a suspended
 * subscription still opens; it restricts what happens inside.
 */
function statusBadge(entitlementStatus: string | null): { label: string; tone: string; hint?: string } | null {
  switch (entitlementStatus) {
    case "trial":
      return { label: "Trial", tone: "w360-badge-neutral" };
    case "active":
      return { label: "Active", tone: "w360-badge-success" };
    case "past_due":
      return { label: "Active", tone: "w360-badge-success", hint: "Payment is overdue. Update your billing details." };
    case "grace":
      return { label: "Active", tone: "w360-badge-success", hint: "Payment is overdue. Update your billing details." };
    case "suspended":
      return { label: "Suspended", tone: "w360-badge-danger", hint: "Read-only until payment resumes." };
    case "cancelled":
      return { label: "Active", tone: "w360-badge-success", hint: "Ending - won't renew." };
    default:
      return null;
  }
}

/** Task 23: no external image URL - the product's own icon key (an inline
 * SVG this app draws) where one is set, else a deterministic monogram from
 * data the platform already controls (the product's name). */
function ProductGlyph({ app }: { app: AppDirectoryEntry }) {
  if (productIconKnown(app.icon)) {
    return (
      <span className={styles.icon} aria-hidden="true">
        <ProductIcon icon={app.icon} size={22} />
      </span>
    );
  }
  const letters = app.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return (
    <span className={styles.icon} aria-hidden="true">
      {letters || "?"}
    </span>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function AppCard({
  orgSlug,
  app,
  manageHref,
  seatsHref,
}: {
  orgSlug: string;
  app: AppDirectoryEntry;
  /** Where "Manage subscription" goes, for those who may manage it. */
  manageHref?: string;
  /** Product access (seat assignment), for those who may assign seats - so
   * the person who *is* the administrator is not told to ask one. */
  seatsHref?: string;
}) {
  const copy = STATE_COPY[app.state];
  const status = statusBadge(app.entitlement_status);
  const assignsSeats = app.state === "no_seat" && Boolean(seatsHref);
  const hint = assignsSeats ? SELF_ASSIGN_HINT : (copy.hint ?? status?.hint);

  return (
    <article className={`w360-card ${styles.card}`} aria-labelledby={`app-${app.product_code}`}>
      <div className={styles.top}>
        <ProductGlyph app={app} />
        <div className={styles.titleBlock}>
          <h2 id={`app-${app.product_code}`} className={styles.name}>
            {app.display_name}
          </h2>
          {app.category && <p className={styles.category}>{app.category}</p>}
          {(status || copy.badge) && (
            <ul className={styles.badgeRow} aria-label="Status">
              {status && <li className={`w360-badge ${status.tone}`}>{status.label}</li>}
              {copy.badge && <li className={`w360-badge ${copy.tone}`}>{copy.badge}</li>}
            </ul>
          )}
        </div>
      </div>
      <p className={styles.description}>{app.description}</p>
      {(app.plan || app.seats || app.trial || app.renews_at) && (
        <dl className={styles.facts}>
          {app.plan && (
            <div>
              <dt>Plan</dt>
              <dd>{app.plan.name}</dd>
            </div>
          )}
          {app.trial && (
            <div>
              <dt>Trial</dt>
              <dd>{app.trial.days_left === 1 ? "1 day left" : `${app.trial.days_left} days left`}</dd>
            </div>
          )}
          {app.seats && (
            <div>
              <dt>Seats</dt>
              <dd>
                {app.seats.used} of {app.seats.total}
              </dd>
            </div>
          )}
          {app.renews_at && !app.cancel_at && (
            <div>
              <dt>Renews</dt>
              <dd>{when(app.renews_at)}</dd>
            </div>
          )}
        </dl>
      )}
      {hint && <p className={styles.hint}>{hint}</p>}
      <div className={styles.footer}>
        {app.state === "active" && <LaunchButton orgSlug={orgSlug} productCode={app.product_code} />}
        {assignsSeats && seatsHref && (
          <Link
            className="w360-btn w360-btn-primary"
            href={seatsHref}
            aria-label={`Assign a seat: ${app.display_name}`}
          >
            Assign a seat
          </Link>
        )}
        {app.state === "no_seat" && !assignsSeats && (
          <span className="w360-badge w360-badge-neutral">Ask an admin</span>
        )}
        {app.state === "no_entitlement" && (
          <Link className="w360-btn w360-btn-secondary" href={`/o/${orgSlug}/explore`}>
            Explore products
          </Link>
        )}
        {manageHref && app.state !== "no_entitlement" && app.state !== "product_unavailable" && (
          <Link className="w360-btn w360-btn-secondary" href={manageHref}>
            Manage subscription
          </Link>
        )}
      </div>
    </article>
  );
}
