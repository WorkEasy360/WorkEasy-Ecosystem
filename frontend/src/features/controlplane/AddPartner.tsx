"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AddReseller, isPurpose, type Purpose } from "./AddReseller";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * Partners → Add partner. First the business purpose, then the existing
 * set-up flow with that purpose's capability preset ticked.
 *
 * The purpose is a **preset selector, nothing more**: it lives in the address
 * bar (`?purpose=`), is never sent to the server, and is not recorded on the
 * partner. Every purpose creates the same thing - one ordinary partner account
 * with capability grants - and the person can change the ticked capabilities
 * before confirming.
 */

const CHOICES: { purpose: Purpose; label: string; description: string }[] = [
  {
    purpose: "reseller",
    label: "Reseller",
    description: "Creates and manages customers, sells approved products and earns commission.",
  },
  {
    purpose: "white_label",
    label: "White-label",
    description: "Uses its own brand and custom domains. Add selling or referral capabilities if it needs them.",
  },
  {
    purpose: "affiliate",
    label: "Affiliate",
    description: "Refers customers with referral codes and earns commission on the ones who sign up.",
  },
  {
    purpose: "custom",
    label: "Custom",
    description: "Start with nothing ticked and choose exactly the capabilities this partner holds.",
  },
];

function PurposeChooser() {
  return (
    <div className={styles.stack}>
      <h1>Add partner</h1>
      <p className={styles.lead}>
        What will this partner do? Your choice only pre-selects capabilities - you can change them on the next steps, and one
        partner can do several of these things at once.
      </p>
      <ul className={local.purposes} aria-label="Business purpose">
        {CHOICES.map((c) => (
          <li key={c.purpose}>
            <Link href={`/admin/partners/new?purpose=${c.purpose}`} className={`w360-card ${local.purpose}`}>
              <span className={local.purposeTitle}>{c.label}</span>
              <span className={styles.muted}>{c.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AddPartner() {
  const params = useSearchParams();
  const requested = params.get("purpose");
  if (!isPurpose(requested)) return <PurposeChooser />;
  // A new key per purpose: switching purpose starts a fresh draft.
  return <AddReseller key={requested} purpose={requested} />;
}
