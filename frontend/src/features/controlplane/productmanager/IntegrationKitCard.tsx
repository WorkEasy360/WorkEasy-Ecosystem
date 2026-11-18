"use client";

/** Super Admin > Product > Integration: the integration kit. A product's
 * WorkEasy360 sign-in configuration, generated from its registration for the
 * framework it is built with (Django connector, Spring Security, openid-client,
 * or the contract for anything else). It holds no secret: the private key stays
 * in the product's secret manager. Each generation is audited. */

import { useState } from "react";
import { adminApi, type IntegrationKit, type IntegrationKitFramework } from "@/lib/api/controlPlane";
import { describeError } from "../ui";
import { Alert, Button, Card, CopyButton } from "../kit";
import { SelectField } from "../widgets";
import styles from "./ProductManager.module.css";

const FRAMEWORKS: { value: IntegrationKitFramework; label: string }[] = [
  { value: "django", label: "Django (workeasy360-sso connector)" },
  { value: "spring", label: "Spring Boot (Spring Security)" },
  { value: "node", label: "NestJS, Express or Next.js (openid-client)" },
  { value: "other", label: "Another runtime (the integration contract)" },
];

function download(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name.replace(/[^A-Za-z0-9._-]+/g, "_");
  link.click();
  URL.revokeObjectURL(url);
}

export function IntegrationKitCard({ code }: { code: string }) {
  const [framework, setFramework] = useState<IntegrationKitFramework>("django");
  const [kit, setKit] = useState<IntegrationKit | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      setKit(await adminApi.integrationKit(code, framework));
    } catch (err) {
      setKit(null);
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Integration kit"
      description="The product's sign-in configuration for its framework, generated from this registration. It holds no secret."
    >
      <div className={styles.toolbar}>
        <SelectField
          label="Framework"
          value={framework}
          options={FRAMEWORKS}
          onChange={(value) => {
            setFramework(value as IntegrationKitFramework);
            setKit(null);
          }}
        />
        <Button onClick={generate} disabled={busy}>
          {busy ? "Generating…" : "Generate kit"}
        </Button>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {kit && (
        <div aria-label="Generated integration kit">
          <p className={styles.muted}>
            {kit.adapter}. Issuer <code>{kit.facts.issuer}</code>, client <code>{kit.facts.client_id}</code>.
          </p>
          {kit.missing.length > 0 && (
            <Alert tone="warning" title="Still missing from the registration">
              <ul>
                {kit.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Alert>
          )}
          {kit.files.map((file) => (
            <section key={file.path} aria-label={file.path}>
              <h4>
                {file.path} <span className={styles.muted}>{file.purpose}</span>
              </h4>
              <pre className={styles.url} tabIndex={0}>
                {file.content}
              </pre>
              <div className={styles.toolbar}>
                <CopyButton value={file.content} label={`Copy ${file.path}`} />
                <Button size="sm" onClick={() => download(file.path, file.content)}>
                  Download
                </Button>
              </div>
            </section>
          ))}
          <h4>Next</h4>
          <ol>
            {kit.next.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      )}
    </Card>
  );
}
