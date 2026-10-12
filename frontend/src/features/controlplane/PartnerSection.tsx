"use client";

import { useId } from "react";
import styles from "./ControlPlane.module.css";

/** A titled card on a partner's page: the one shape every tab's sections share. */
export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section className={`w360-card ${styles.card}`} aria-labelledby={id}>
      <h3 id={id} className={styles.cardTitle}>
        {title}
      </h3>
      {children}
    </section>
  );
}
