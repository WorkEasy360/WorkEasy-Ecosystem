"use client";

import { useState } from "react";
import { usagePricingApi } from "@/lib/api/adminPanel";
import type { ConsoleProduct } from "@/lib/api/controlPlane";
import { FormPopup } from "../Modal";
import { TextField } from "../widgets";
import { EMPTY, UrlCell } from "../kit";
import { looksLikeHttps } from "./model";
import styles from "./ProductManager.module.css";

const PLATFORMS = [
  { key: "ios", label: "iOS" },
  { key: "android", label: "Android" },
  { key: "windows", label: "Windows" },
  { key: "macos", label: "macOS" },
  { key: "web", label: "Web" },
] as const;

type Links = Record<string, string>;

/** Where a product's apps are published. The catalog's own write, https only, audited by
 * the server. Saving replaces the whole set, so every platform is shown and sent together. */
export function AppLinksPanel({
  product,
  manage,
  busy,
  reload,
}: {
  product: ConsoleProduct & { app_links?: Links };
  manage: boolean;
  busy: boolean;
  reload: () => void;
}) {
  const [saved, setSaved] = useState<Links | null>(null);
  const known = saved ?? product.app_links;
  const links = known ?? {};
  const [draft, setDraft] = useState<Links>(links);

  const problemOf = (key: string) => {
    const value = (draft[key] ?? "").trim();
    return value && !looksLikeHttps(value) ? "Use an https:// address." : undefined;
  };
  const ready = PLATFORMS.every((p) => !problemOf(p.key));

  return (
    <section className={styles.card} aria-labelledby="app-links">
      <h2 id="app-links" className={styles.cardTitle}>
        App links
      </h2>
      <p className={styles.cardLead}>Where people install this product&apos;s apps.</p>
      <dl className={styles.facts}>
        {PLATFORMS.map((p) => (
          <div key={p.key}>
            <dt>{p.label}</dt>
            <dd>{links[p.key] ? <UrlCell url={links[p.key]} /> : EMPTY}</dd>
          </div>
        ))}
      </dl>
      {!known && <p className={styles.muted}>This page could not read the links already saved.</p>}
      {manage && (
        <div className={styles.toolbar}>
          <FormPopup
            label="Edit app links"
            title="App links"
            intro={
              known
                ? "Secure web addresses only. Leave a platform blank if it has no app."
                : "Secure web addresses only. This page could not read the links already saved, and saving replaces them."
            }
            submitLabel="Save app links"
            buttonClassName="w360-btn w360-btn-secondary"
            disabled={busy}
            canSubmit={ready}
            onOpen={() => setDraft(links)}
            onSubmit={async () => {
              const body: Links = {};
              for (const p of PLATFORMS) {
                const value = (draft[p.key] ?? "").trim();
                if (value) body[p.key] = value;
              }
              const answer = await usagePricingApi.setAppLinks(product.id, body);
              setSaved(answer.app_links);
              return "App links saved.";
            }}
            onDone={reload}
          >
            {PLATFORMS.map((p) => (
              <TextField
                key={p.key}
                label={p.label}
                type="url"
                autoComplete="off"
                placeholder="https://"
                maxLength={500}
                value={draft[p.key] ?? ""}
                error={problemOf(p.key)}
                onChange={(value) => setDraft((d) => ({ ...d, [p.key]: value }))}
              />
            ))}
          </FormPopup>
        </div>
      )}
    </section>
  );
}
