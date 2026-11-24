"use client";

import { useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { adminApi, type SignInSetup } from "@/lib/api/controlPlane";
import { LoadGate, describeError, useLoad } from "../ui";
import { Alert, Badge, Button, Card, CopyButton, KeyText, formatDateTime, type Tone } from "../kit";
import styles from "./ProductManager.module.css";

/** The five states, as the platform recorded them (ADR-074). */
const STATE: Record<NonNullable<SignInSetup["state"]>, { tone: Tone; icon: string; summary: string }> = {
  pending_configuration: {
    tone: "warning",
    icon: "alert",
    summary: "Waiting for the product: see the first check that is not done.",
  },
  configured: { tone: "info", icon: "info", summary: "The registration matches the product's document." },
  verification_required: {
    tone: "warning",
    icon: "alert",
    summary: "Everything matches. Publish the domain proof below, then check again.",
  },
  active: { tone: "success", icon: "checkCircle", summary: "People can sign in to this product through WorkEasy360." },
  failed: { tone: "danger", icon: "xCircle", summary: "A check found a problem. Nothing was activated." },
};

const CHECK: Record<SignInSetup["checks"][number]["status"], { tone: Tone; icon: string; label: string }> = {
  passed: { tone: "success", icon: "check", label: "Done" },
  pending: { tone: "warning", icon: "alert", label: "Waiting" },
  failed: { tone: "danger", icon: "xCircle", label: "Failed" },
};

/**
 * Super Admin > Products > a product > Integration: automatic sign-in setup
 * (ADR-074). Shows the state the platform recorded at its last check -
 * Pending Configuration, Configured, Verification Required, Active or Failed -
 * every check with its exact reason, and the client id the product is
 * configured with. "Check now" asks the server to run the checks; this card
 * never decides a state itself.
 */
export function SignInSetupCard({ code, manage, onChecked }: { code: string; manage: boolean; onChecked?: () => void }) {
  const [load, reload] = useLoad(() => adminApi.signInSetup(code), [code]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkNow = async () => {
    setBusy(true);
    setError(null);
    try {
      await adminApi.checkSignInSetup(code);
      reload();
      onChecked?.();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Automatic sign-in setup"
      description="WorkEasy360 reads the product's compatibility document, proves its domain, then registers and activates its sign-in. Nothing is activated before every check passes."
      actions={
        manage ? (
          <Button size="sm" icon="refresh" disabled={busy} onClick={() => void checkNow()} aria-label="Check the sign-in setup now">
            {busy ? "Checking…" : "Check now"}
          </Button>
        ) : undefined
      }
    >
      {error && <Alert tone="danger">{error}</Alert>}
      <LoadGate load={load} what="the sign-in setup">
        {(setup) => <SetupBody setup={setup} />}
      </LoadGate>
    </Card>
  );
}

function SetupBody({ setup }: { setup: SignInSetup }) {
  if (!setup.automatic || setup.state === null) {
    return (
      <p className={styles.muted}>
        This product&apos;s sign-in was set up by hand. Choose Check now to start automatic checks: a product that is
        already live is never switched off by them.
      </p>
    );
  }
  const state = STATE[setup.state];
  return (
    <>
      <p className={styles.verdict}>
        <Badge tone={state.tone} icon={state.icon}>
          {setup.state_label}
        </Badge>
        <span className={styles.muted}>{state.summary}</span>
      </p>
      <dl className={styles.facts}>
        <div>
          <dt>Client id</dt>
          <dd>
            {setup.client_id ? (
              <>
                <KeyText>{setup.client_id}</KeyText> <CopyButton value={setup.client_id} label="Copy client id" />
              </>
            ) : (
              "Prepared once there is an application URL"
            )}
          </dd>
        </div>
        <div>
          <dt>Product code</dt>
          <dd>
            <KeyText>{setup.product_code}</KeyText>
          </dd>
        </div>
        <div>
          <dt>Compatibility document</dt>
          <dd className={styles.url}>{setup.document_url || "—"}</dd>
        </div>
        <div>
          <dt>Connector</dt>
          <dd>{setup.connector?.name ? `${setup.connector.name} ${setup.connector.version}` : "Not seen yet"}</dd>
        </div>
        <div>
          <dt>Last checked</dt>
          <dd>{setup.last_checked_at ? formatDateTime(setup.last_checked_at) : "Not yet"}</dd>
        </div>
        <div>
          <dt>Next check</dt>
          <dd>{setup.next_check_at ? formatDateTime(setup.next_check_at) : "—"}</dd>
        </div>
      </dl>
      <ul className={styles.readiness} aria-label="Sign-in setup checks">
        {setup.checks.map((check) => {
          const shown = CHECK[check.status] ?? CHECK.pending;
          return (
            <li key={check.key} className={styles.readinessItem}>
              <span className={styles.readinessIcon} data-state={check.status === "passed" ? "ok" : check.status === "failed" ? "action" : "warning"} aria-hidden="true">
                <Icon name={shown.icon} size={16} />
              </span>
              <span className={styles.readinessText}>
                <span className={styles.readinessLabel}>{check.label}</span>
                {check.detail && <span className={styles.readinessDetail}>{check.detail}</span>}
              </span>
              <Badge tone={shown.tone}>{shown.label}</Badge>
            </li>
          );
        })}
      </ul>
    </>
  );
}
