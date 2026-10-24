"use client";

import Link from "next/link";
import { LoadGate, useLoad } from "@/features/controlplane/ui";
import { panelApi } from "@/lib/api/partnerPanel";
import { NotAvailable, PageHeader, usePanel } from "./PanelKit";
import styles from "./PartnerPanel.module.css";

/**
 * Role & Permissions: the role presets as they apply to this partner.
 *
 * A role is a preset over the capabilities the partner holds - never a new
 * permission - so this page only explains. Who has which role is on Team
 * Members; there are no custom roles (the server says why).
 */
export function Roles() {
  const { id, base } = usePanel();
  const [load] = useLoad(() => panelApi.roles(id), [id]);
  return (
    <div className={styles.page}>
      <PageHeader
        title="Role & Permissions"
        lead="What each role can do for your partner account. A role can narrow what your account holds and can never add to it."
        actions={<Link href={`${base}/team`}>Team Members</Link>}
      />
      <LoadGate load={load} what="roles">
        {(d) => (
          <>
            <ul className={styles.cards} aria-label="Roles">
              {d.roles.map((r) => (
                <li key={r.key} className={styles.card}>
                  <div className={styles.cardHead}>
                    <div>
                      <h2 className={styles.cardTitle}>{r.label}</h2>
                      <span className={styles.muted}>
                        {r.members} {r.members === 1 ? "person" : "people"}
                        {r.administrator ? " · runs the account" : ""}
                      </span>
                    </div>
                  </div>
                  <p className={styles.muted}>{r.description}</p>
                  {r.capabilities.length === 0 ? (
                    <p className={styles.muted}>Nothing your account holds applies to this role.</p>
                  ) : (
                    <ul className={styles.list} aria-label={`What ${r.label} can do`}>
                      {r.capabilities.map((c) => (
                        <li key={c.key} className={styles.listItem}>
                          <span>{c.label}</span>
                          <span className="w360-badge w360-badge-neutral">{c.access === "manage" ? "can change" : "view only"}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {r.owner_only && <p className={styles.muted}>Only an owner can give or change this role.</p>}
                </li>
              ))}
            </ul>
            <div className={styles.twoCol}>
              <section className={styles.panel} aria-labelledby="role-everyone">
                <h2 id="role-everyone">Every role</h2>
                <ul>
                  {d.account_access.everyone.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              </section>
              <section className={styles.panel} aria-labelledby="role-admins">
                <h2 id="role-admins">Roles that run the account</h2>
                <ul>
                  {d.account_access.administrators.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              </section>
            </div>
            <section className={styles.panel} aria-labelledby="role-held">
              <h2 id="role-held">What your partner account holds</h2>
              {d.partner_capabilities.length === 0 ? (
                <p className={styles.muted}>Workeasy360 has not enabled any capability yet.</p>
              ) : (
                <ul className={styles.list}>
                  {d.partner_capabilities.map((c) => (
                    <li key={c.key} className={styles.listItem}>
                      {c.label}
                    </li>
                  ))}
                </ul>
              )}
              <p className={styles.muted}>Nobody on your team can give themselves, or anyone else, more than this list. You cannot change your own role.</p>
            </section>
            <NotAvailable title="Custom roles" reasons={[d.not_implemented.custom_roles ?? ""]} />
          </>
        )}
      </LoadGate>
    </div>
  );
}
