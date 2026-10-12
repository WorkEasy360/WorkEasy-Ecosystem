"use client";

import { useState } from "react";
import type { DomainRow } from "@/lib/api/controlPlane";
import { Status, useAction } from "../ui";
import { TextField } from "../widgets";
import { FAILURES } from "../WhiteLabelWizard";
import { hostnameProblem, serverProblem } from "./model";
import { AuthenticationBox, StepFrame, type Ctx } from "./shared";
import styles from "../ControlPlane.module.css";
import css from "../WhiteLabelSetup.module.css";

/** The lifecycle a custom domain moves through, in order. */
const LIFECYCLE: { status: string; label: string }[] = [
  { status: "requested", label: "Requested" },
  { status: "verification_pending", label: "Verification pending" },
  { status: "verified", label: "Verified" },
  { status: "active", label: "Active" },
];

/**
 * Step 7 (optional): a custom domain.
 *
 * Four separate acts, each its own action so a second-factor prompt appears where
 * it is needed: request a host name, check the DNS record, activate, disable.
 * **Activation is never automatic** - verification proves a name is the partner's,
 * and only a deliberate Activate, from ``verified``, makes it serve the brand.
 *
 * A custom domain shows the brand. It never signs anyone in (ADR-039): the box at
 * the bottom says so in the server's own words.
 */
export function DomainStep({ ctx }: { ctx: Ctx }) {
  const action = useAction();
  const [hostname, setHostname] = useState("");
  const [hostError, setHostError] = useState<string | undefined>(undefined);
  const { setup, api } = ctx;
  const domain = setup.domain;

  function request() {
    const problem = hostnameProblem(hostname);
    setHostError(problem ?? undefined);
    if (problem) return;
    void action.act(async () => {
      try {
        await api.requestDomain(hostname.trim().toLowerCase());
      } catch (err) {
        const found = serverProblem(err);
        if (found?.key === "hostname") setHostError(found.message);
        throw err;
      }
      setHostname("");
      await ctx.refresh();
    }, "Domain requested. Publish the DNS record, then check it.");
  }

  const run = (call: (id: string) => Promise<unknown>, message: string) => {
    if (!domain) return;
    void action.act(async () => {
      await call(domain.id);
      await ctx.refresh();
    }, message);
  };

  return (
    <StepFrame
      step="domain"
      nav={ctx.nav}
      action={action}
      intro={
        <p className={css.lead}>
          A custom domain is optional. It shows the brand on your own address, such as app.example.com. Requesting one only asks: nothing is
          served until you prove you control the name and then activate it yourself.
        </p>
      }
    >
      {domain === null ? (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault();
            request();
          }}
        >
          <TextField
            label="Host name to request (for example app.example.com)"
            value={hostname}
            onChange={(v) => {
              setHostname(v);
              setHostError(undefined);
            }}
            autoComplete="off"
            spellCheck={false}
            maxLength={253}
            error={hostError}
          />
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={action.busy || !hostname.trim()}>
              Request domain
            </button>
          </div>
        </form>
      ) : (
        <DomainDetail
          domain={domain}
          busy={action.busy}
          brandSaved={setup.brand !== null}
          onCheck={() => run(api.verifyDomain, "Checked the DNS record.")}
          onActivate={() => run(api.activateDomain, "The domain is active and now serves the brand.")}
          onDisable={() => run(api.disableDomain, "The domain was disabled and no longer serves the brand.")}
        />
      )}
      <AuthenticationBox setup={setup} />
    </StepFrame>
  );
}

function DomainDetail({
  domain,
  busy,
  brandSaved,
  onCheck,
  onActivate,
  onDisable,
}: {
  domain: DomainRow;
  busy: boolean;
  brandSaved: boolean;
  onCheck: () => void;
  onActivate: () => void;
  onDisable: () => void;
}) {
  const current = LIFECYCLE.findIndex((s) => s.status === domain.status);
  const proven = domain.status === "verified" || domain.status === "active";
  return (
    <div className={css.step}>
      <p>
        <span className={styles.mono}>{domain.hostname}</span> <Status value={domain.status} />
      </p>
      <ol className={css.lifecycle} aria-label="Domain status">
        {LIFECYCLE.map((s, i) => (
          <li key={s.status} className={css.lifecycleItem} aria-current={i === current ? "step" : undefined}>
            {s.label}
          </li>
        ))}
      </ol>

      {domain.status !== "active" && (
        <>
          <p>
            Prove you control <span className={styles.mono}>{domain.hostname}</span> by adding this DNS TXT record where the domain is managed:
          </p>
          <DnsRecord record={domain.verification_record} />
        </>
      )}

      {domain.failure_code && !proven && <p role="status">{FAILURES[domain.failure_code] ?? domain.failure_code.replace(/_/g, " ")}</p>}

      {!proven && (
        <div>
          <button type="button" className="w360-btn w360-btn-primary" onClick={onCheck} disabled={busy}>
            Check verification
          </button>
        </div>
      )}

      {domain.status === "verified" && (
        <div className={css.callout}>
          <p>
            Ownership is verified. The domain does not serve anything until you activate it. Activating makes {domain.hostname} show this brand to
            visitors: it changes only what they see, never who they are or what they may do.
          </p>
          <p>
            <button type="button" className="w360-btn w360-btn-primary" onClick={onActivate} disabled={busy || !brandSaved}>
              Activate domain
            </button>
            {!brandSaved && <span className={css.reason}> Save the brand identity first.</span>}
          </p>
        </div>
      )}

      {domain.status === "active" && (
        <div className={css.callout}>
          <p>Live: this domain shows the brand.</p>
          <p>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={onDisable} disabled={busy}>
              Disable domain
            </button>
          </p>
        </div>
      )}
    </div>
  );
}

/** The record to publish: Type, Host and Value, each with its own Copy button. */
function DnsRecord({ record }: { record: DomainRow["verification_record"] }) {
  const [said, setSaid] = useState("");
  const rows: [string, string][] = [
    ["Type", record.type],
    ["Host", record.name],
    ["Value", record.value],
  ];

  async function copy(label: string, value: string) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("no clipboard");
      await navigator.clipboard.writeText(value);
      setSaid(`Copied ${label}`);
    } catch {
      setSaid("Copying is not available here. Select the text and copy it yourself.");
    }
  }

  return (
    <div>
      <dl className={css.dns}>
        {rows.map(([label, value]) => (
          <div key={label} className={css.dnsRow}>
            <dt>{label}</dt>
            <dd className={css.dnsLine}>
              <code className={css.dnsValue}>{value}</code>
              <button type="button" className="w360-btn w360-btn-secondary" onClick={() => void copy(label, value)}>
                Copy <span className="w360-visually-hidden">{label.toLowerCase()}</span>
              </button>
            </dd>
          </div>
        ))}
      </dl>
      <p role="status" aria-live="polite" className={css.reason}>
        {said}
      </p>
    </div>
  );
}
