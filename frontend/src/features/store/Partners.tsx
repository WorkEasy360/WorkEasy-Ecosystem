"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/sidebar/icons";
import { storeHostApi, type PartnerProgramme } from "@/lib/api/storeHost";
import { usePartnerHref } from "./StoreShell";
import { partnerPaths, storePaths } from "./links";
import styles from "./StorePages.module.css";
import shell from "./Store.module.css";

/**
 * Partners (ADR-065): the two public programmes - Affiliate and Reseller - with
 * what each is, who may apply, what it offers, the KYC documents an Admin asks
 * for and the application's stages; Apply Now and Application Status on the
 * Partner portal (partner.workeasy360.com), which runs the one shared application
 * and KYC - the Store keeps no partner records of its own. Existing partners sign
 * in there, where they activate customers' plans; the Store never does.
 *
 * Commission rates and partner prices are set per partner in the Partner Admin,
 * so this page names none.
 */

const OFFERS: Record<string, { icon: string; points: string[] }> = {
  affiliate: {
    icon: "megaphone",
    points: [
      "Referral links to WorkEasy360 products, with clicks and conversions in your dashboard",
      "Commission on the paid purchases of the customers you refer, at your programme's rates",
      "Commission statements and payouts in the Partner portal",
    ],
  },
  reseller: {
    icon: "store",
    points: [
      "Sell the WorkEasy360 products your partnership is granted",
      "Set up and manage your customers, and activate their plans, from the Partner portal",
      "Your customers keep one WorkEasy360 account for every product",
    ],
  },
};

const PORTAL_FEATURES = [
  { icon: "box", label: "Eligible Products" },
  { icon: "network", label: "Referrals" },
  { icon: "chart", label: "Referral Conversions" },
  { icon: "users", label: "Assigned Customers" },
  { icon: "checkCircle", label: "Customer Plan Activation" },
];

export function PartnersScreen() {
  const partner = usePartnerHref();
  const [data, setData] = useState<PartnerProgramme | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    storeHostApi.partnerProgramme().then(
      (d) => live && setData(d),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, []);
  const open = data?.applications_open ?? false;
  return (
    <div className={shell.container}>
      <nav aria-label="Breadcrumb" className={shell.crumbs}>
        <ol>
          <li>
            <Link href={storePaths.home}>Store</Link>
          </li>
          <li aria-current="page">Partners</li>
        </ol>
      </nav>
      <section className={styles.partnerHero} aria-labelledby="partners-title">
        <div>
          <h1 id="partners-title" className={styles.partnerHeroTitle}>
            Become a WorkEasy360 Partner
          </h1>
          <p>Refer customers or sell WorkEasy360 products - with one application, KYC checked by our team, and your own partner dashboard.</p>
          <div className={styles.heroCtas}>
            {open ? (
              <a href={partner(partnerPaths.apply())} className={styles.heroPrimary}>
                Apply Now <Icon name="chevronRight" size={18} />
              </a>
            ) : (
              data && <span className={styles.closedNote}>Applications are closed at the moment.</span>
            )}
            <a href={partner(partnerPaths.signIn)} className={styles.heroSecondary}>
              <Icon name="login" size={18} /> Partner sign in
            </a>
          </div>
        </div>
        <Icon name="handshake" size={120} className={styles.partnerHeroIcon} />
      </section>

      {failed && (
        <p role="alert" className={`${shell.notice} ${shell.warningNotice}`}>
          The programme details could not be loaded just now. Try again in a moment.
        </p>
      )}

      <div className={styles.programmes}>
        {(data?.programmes ?? []).map((p) => {
          const offer = OFFERS[p.key];
          return (
            <section key={p.key} id={p.key} className={`${shell.card} ${styles.programme}`} aria-labelledby={`${p.key}-title`}>
              <h2 id={`${p.key}-title`} className={styles.rechargeTitle}>
                <span className={styles.rechargeIcon}>
                  <Icon name={offer?.icon ?? "handshake"} size={24} />
                </span>
                {p.label} Programme
              </h2>
              <p>{p.description}</p>
              <h3 className={styles.boxLabel}>What it offers</h3>
              <ul className={shell.ticks}>
                {(offer?.points ?? []).map((pt) => (
                  <li key={pt}>
                    <Icon name="check" size={16} />
                    <span>{pt}</span>
                  </li>
                ))}
              </ul>
              <h3 className={styles.boxLabel}>Who can apply</h3>
              <p className={styles.boxHint}>
                Any business or professional can apply.{" "}
                {data?.kyc_required ? "Your partnership is activated only after your KYC is approved." : "Our team reviews every application."}
              </p>
              <div className={styles.boxActions}>
                {open && (
                  <a href={partner(partnerPaths.apply(p.key))} className={shell.primary}>
                    Apply as {p.label}
                  </a>
                )}
                <Link href={storePaths.policy("partner-terms")} className={shell.secondary}>
                  Partner Terms
                </Link>
              </div>
            </section>
          );
        })}
      </div>

      {data && (
        <div className={styles.partnerDetails}>
          <section className={`${shell.card} ${styles.programme}`} aria-labelledby="kyc-title">
            <h2 id="kyc-title" className={styles.detailHeading}>
              KYC requirements
            </h2>
            {data.kyc_requirements.length === 0 ? (
              <p className={styles.boxHint}>Your business details are all an application needs today. Our team may ask for more during review.</p>
            ) : (
              <ul className={styles.kycList}>
                {data.kyc_requirements.map((r) => (
                  <li key={r.label}>
                    <Icon name="checkCircle" size={18} />
                    <span>
                      <strong>
                        {r.label}
                        {!r.is_required && <span className={styles.optional}> (optional)</span>}
                      </strong>
                      {r.help_text && <span>{r.help_text}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className={styles.boxHint}>Documents are uploaded in the Partner portal after you apply, and kept in your one partner application.</p>
          </section>
          <section className={`${shell.card} ${styles.programme}`} aria-labelledby="status-title">
            <h2 id="status-title" className={styles.detailHeading}>
              Application status
            </h2>
            <ol className={styles.stages}>
              {data.stages.map((s) => (
                <li key={s.key}>{s.label}</li>
              ))}
            </ol>
            <p className={styles.boxHint}>Sign in to the Partner portal with the email you applied with to see where your application stands and answer any correction.</p>
            <a href={partner(partnerPaths.signIn)} className={shell.secondary}>
              Check Application Status
            </a>
          </section>
        </div>
      )}

      <section className={`${shell.card} ${styles.programme}`} aria-labelledby="existing-title">
        <h2 id="existing-title" className={styles.detailHeading}>
          Already a partner?
        </h2>
        <p>Everything partners do happens in the Partner portal:</p>
        <ul className={styles.portalFeatures}>
          {PORTAL_FEATURES.map((f) => (
            <li key={f.label}>
              <Icon name={f.icon} size={22} />
              {f.label}
            </li>
          ))}
        </ul>
        <a href={partner(partnerPaths.signIn)} className={shell.primary}>
          Go to the Partner portal <Icon name="external" size={16} />
        </a>
      </section>
    </div>
  );
}
