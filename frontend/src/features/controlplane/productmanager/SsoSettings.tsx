"use client";

import { useState } from "react";
import { Icon } from "@/components/sidebar/icons";
import { ApiError } from "@/lib/api/client";
import {
  adminApi,
  type BackchannelDelivery,
  type BackchannelSummary,
  type ConsoleIntegration,
  type DomainVerifyOutcome,
  type ProductDomain,
  type ProductDomains,
  type SsoReadiness,
  type SsoReadinessCheck,
} from "@/lib/api/controlPlane";
import { FormPopup } from "../Modal";
import { LoadGate, Table, describeError, useLoad, type Load } from "../ui";
import {
  Alert,
  Badge,
  BoolBadge,
  Button,
  Card,
  CopyButton,
  DescriptionList,
  EMPTY,
  EmptyState,
  KeyText,
  Stack,
  StatCard,
  StatGrid,
  StatusBadge,
  formatDateTime,
  formatNumber,
  formatRelative,
  kit,
  type Tone,
} from "../kit";
import { TextField } from "../widgets";
import { hostnameProblem, type DomainBlock } from "./model";
import styles from "./ProductManager.module.css";

/**
 * Super Admin -> Products -> a product -> Integration: single sign-on
 * (ADR-069, ADR-070). Whether people can sign in through WorkEasy360 and what
 * is missing; which hostnames the registration uses and whether control of
 * each is proven (with the DNS record to publish); and where back-channel
 * logout tokens go and how their delivery went.
 *
 * Everything shown is the server's: the readiness verdict, the domain
 * statuses and the delivery categories. Viewers see it all; only the
 * integration management grant asks for a proof or runs a check.
 */

// --- state shared by the cards --------------------------------------------------------

/** The readiness verdict and the domain list of one product's integration,
 * loaded with the Integration tab (nothing when there is no integration or it
 * may not be seen). `refresh` re-reads both and keeps what is on screen until
 * the answers arrive; `setDomains` shows a domain answer the server just gave
 * (asking for a proof, a check) and re-reads the verdict. */
export function useSsoState(code: string, integration: ConsoleIntegration | null, visible: boolean) {
  const on = visible && integration !== null;
  const [readiness, reloadReadiness] = useLoad<SsoReadiness | null>(
    () => (on ? adminApi.integrationReadiness(code) : Promise.resolve(null)),
    [code, on],
  );
  const [loaded, reloadDomains] = useLoad<ProductDomains | null>(
    () => (on ? adminApi.integrationDomains(code) : Promise.resolve(null)),
    [code, on],
  );
  // A newer answer than `loaded`, until the next load replaces it.
  const [answer, setAnswer] = useState<{ data: ProductDomains; over: Load<ProductDomains | null> } | null>(null);
  const domains: Load<ProductDomains | null> = answer && answer.over === loaded ? { status: "ready", data: answer.data } : loaded;
  return {
    readiness,
    domains,
    refresh: () => {
      reloadReadiness();
      reloadDomains();
    },
    setDomains: (data: ProductDomains) => {
      setAnswer({ data, over: loaded });
      reloadReadiness();
    },
  };
}

// --- readiness ---------------------------------------------------------------------------

const VERDICT: Record<SsoReadiness["verdict"], { tone: Tone; icon: string; label: string; summary: string }> = {
  ready: { tone: "success", icon: "checkCircle", label: "Ready", summary: "People can sign in to this product through WorkEasy360." },
  attention: { tone: "warning", icon: "alert", label: "Needs attention", summary: "Sign-in works, with a gap to close below." },
  not_ready: { tone: "danger", icon: "xCircle", label: "Not ready", summary: "Sign-in through WorkEasy360 does not work yet." },
};

const CHECK: Record<SsoReadinessCheck["state"], { tone: Tone; icon: string; label: string }> = {
  ok: { tone: "success", icon: "check", label: "Done" },
  action: { tone: "danger", icon: "xCircle", label: "Action needed" },
  warning: { tone: "warning", icon: "alert", label: "Warning" },
  info: { tone: "info", icon: "info", label: "Note" },
};

/** Where on this tab a check that is not done is put right. */
const CHECK_TARGET: Record<string, { href: string; label: string }> = {
  domains: { href: "#sso-domains", label: "Go to Domains" },
  backchannel: { href: "#sso-backchannel", label: "Go to Back-channel logout" },
};

export function SsoReadinessCard({ load, onRefresh }: { load: Load<SsoReadiness | null>; onRefresh: () => void }) {
  return (
    <Card
      title="SSO readiness"
      description="Whether people can sign in to this product through WorkEasy360, and what is missing."
      actions={
        <Button size="sm" icon="refresh" aria-label="Refresh SSO readiness" onClick={onRefresh}>
          Refresh
        </Button>
      }
    >
      <LoadGate load={load} what="the SSO readiness">
        {(readiness) => {
          if (!readiness) return null;
          const verdict = VERDICT[readiness.verdict] ?? VERDICT.not_ready;
          return (
            <>
              <p className={styles.verdict}>
                <Badge tone={verdict.tone} icon={verdict.icon}>
                  {verdict.label}
                </Badge>
                <span className={styles.muted}>{verdict.summary}</span>
              </p>
              <ul className={styles.readiness} aria-label="Readiness checks">
                {readiness.checks.map((check) => {
                  const state = CHECK[check.state] ?? CHECK.info;
                  const target = check.state !== "ok" ? CHECK_TARGET[check.key] : undefined;
                  return (
                    <li key={check.key} className={styles.readinessItem}>
                      <span className={styles.readinessIcon} data-state={check.state} aria-hidden="true">
                        <Icon name={state.icon} size={16} />
                      </span>
                      <span className={styles.readinessText}>
                        <span className={styles.readinessLabel}>{check.label}</span>
                        {check.detail && <span className={styles.readinessDetail}>{check.detail}</span>}
                        {target && (
                          <a className={styles.readinessLink} href={target.href}>
                            {target.label}
                          </a>
                        )}
                      </span>
                      <Badge tone={state.tone}>{state.label}</Badge>
                    </li>
                  );
                })}
              </ul>
            </>
          );
        }}
      </LoadGate>
    </Card>
  );
}

// --- domains ------------------------------------------------------------------------------

const DOMAIN_STATUS: Record<ProductDomain["status"], string> = {
  pending: "Pending",
  verified: "Verified",
  lapsed: "Lapsed",
  missing: "Not requested",
};

export const METHOD_LABEL: Record<ProductDomain["method"], string> = {
  dns_txt: "Proven by DNS",
  pre_existing: "Registered before verification",
  loopback: "Local development",
  attested: "Attested by an operator",
  "": EMPTY,
};

/** What a check found, said for the person who asked. */
const VERIFY_OUTCOME: Record<DomainVerifyOutcome, { tone: Tone; say: (host: string, record: string) => string }> = {
  verified: { tone: "success", say: (host) => `${host} is verified.` },
  still_verified: { tone: "success", say: (host) => `The record is still published: ${host} stays verified.` },
  record_not_found: {
    tone: "warning",
    say: (_host, record) =>
      `No TXT record with the verification value was found at ${record}. A new DNS record can take a while to appear; try again in a few minutes.`,
  },
  lookup_failed: { tone: "warning", say: (_host, record) => `The DNS lookup of ${record} failed, so nothing changed. Try again shortly.` },
  control_lost: {
    tone: "danger",
    say: (host, record) => `The record at ${record} is gone: ${host} has lapsed. Publish the record again, then choose Verify.`,
  },
  superseded: {
    tone: "info",
    say: (host) => `A newer verification value was issued for ${host} meanwhile. Publish the value shown now, then choose Verify again.`,
  },
};

type DomainAction = "verify" | "request" | null;

/** What a manager can do for one hostname: check a published record, ask for
 * a value to publish, or nothing (proven by DNS, local development). */
export function domainAction(d: ProductDomain): DomainAction {
  if (d.status === "verified" && (d.method === "dns_txt" || d.method === "loopback")) return null;
  if (!d.record_value) return "request";
  if (d.status === "pending" || d.status === "lapsed") return "verify";
  // A carried-over or attested hostname: a DNS proof may replace it at any time.
  return d.status === "verified" ? "verify" : "request";
}

/** Why the record is shown, under it. */
function recordNote(d: ProductDomain): string {
  if (d.status === "verified" && d.method === "dns_txt") {
    return "Keep this record published: it is re-checked, and the hostname lapses if the record disappears.";
  }
  if (d.status === "verified") return "Optional: publish it and choose Verify to replace the carried-over proof with a DNS proof.";
  return "Publish this record at your DNS provider, then choose Verify.";
}

function DomainBlockAlert({
  block,
  domains,
  manage,
  working,
  onRequest,
}: {
  block: DomainBlock;
  domains: ProductDomains;
  manage: boolean;
  working: string | null;
  onRequest: (hostname: string) => void;
}) {
  const verified = new Set(domains.domains.filter((d) => d.status === "verified").map((d) => d.hostname));
  const remaining = block.hostnames.filter((h) => !verified.has(h));
  if (block.hostnames.length ? remaining.length === 0 : domains.all_verified) return null;
  const known = new Map(domains.domains.map((d) => [d.hostname, d]));
  // A hostname the refused save would have added has no row yet: ask for its value here.
  const unasked = remaining.filter((h) => !known.get(h)?.record_value);
  return (
    <Alert tone="danger" title="Refused: control of a hostname is not proven yet">
      <p>{block.detail}</p>
      {manage && unasked.length > 0 && (
        <div className={styles.toolbar}>
          {unasked.map((hostname) => (
            <Button key={hostname} size="sm" icon="plus" disabled={working !== null} onClick={() => onRequest(hostname)}>
              Get TXT value for {hostname}
            </Button>
          ))}
        </div>
      )}
    </Alert>
  );
}

function AddHostname({ code, disabled, onAnswer }: { code: string; disabled: boolean; onAnswer: (data: ProductDomains) => void }) {
  const [hostname, setHostname] = useState("");
  const problem = hostnameProblem(hostname);
  return (
    <FormPopup
      label="Add hostname"
      buttonClassName="w360-btn w360-btn-secondary"
      title="Add a hostname"
      intro="Before pointing a live product at a new hostname, ask for its verification value, publish the TXT record, and choose Verify. Saving a URL on an unproven hostname is refused."
      size="sm"
      submitLabel="Get verification value"
      disabled={disabled}
      canSubmit={!problem}
      onOpen={() => setHostname("")}
      onSubmit={async () => {
        const host = hostname.trim();
        onAnswer(await adminApi.requestIntegrationDomain(code, host));
        return `Verification value issued for ${host}. Publish its TXT record, then choose Verify.`;
      }}
    >
      <TextField
        label="Hostname"
        required
        autoComplete="off"
        spellCheck={false}
        placeholder="app.example.com"
        value={hostname}
        error={hostname.trim() ? problem : undefined}
        hint="The hostname only - no https:// or path."
        onChange={setHostname}
      />
    </FormPopup>
  );
}

function RecordValue({ value, label }: { value: string; label: string }) {
  return (
    <span className={styles.recordValue}>
      <code className={kit.mono}>{value}</code>
      <CopyButton value={value} label={label} />
    </span>
  );
}

export function DomainsCard({
  code,
  load,
  manage,
  block,
  onAnswer,
}: {
  code: string;
  load: Load<ProductDomains | null>;
  manage: boolean;
  /** The latest refusal because a hostname is not proven (activation or an edit). */
  block: DomainBlock | null;
  onAnswer: (data: ProductDomains) => void;
}) {
  const [working, setWorking] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: Tone; text: string } | null>(null);

  async function run(hostname: string, action: "verify" | "request") {
    setWorking(hostname);
    setNotice(null);
    try {
      if (action === "verify") {
        const answer = await adminApi.verifyIntegrationDomain(code, hostname);
        onAnswer(answer);
        const record = answer.domains.find((d) => d.hostname === hostname)?.record_name ?? `_w360-product-verify.${hostname}`;
        const said = VERIFY_OUTCOME[answer.outcome];
        setNotice(said ? { tone: said.tone, text: said.say(hostname, record) } : { tone: "info", text: `Checked ${hostname}.` });
      } else {
        onAnswer(await adminApi.requestIntegrationDomain(code, hostname));
        setNotice({ tone: "info", text: `Verification value issued for ${hostname}. Publish its TXT record, then choose Verify.` });
      }
    } catch (err) {
      setNotice({
        tone: "danger",
        text:
          err instanceof ApiError && err.status === 429
            ? "Too many verification checks: each one is a DNS lookup. Wait a minute, then try again."
            : describeError(err),
      });
    } finally {
      setWorking(null);
    }
  }

  return (
    <Card
      title="Domains"
      labelledBy="sso-domains"
      description="Control of every hostname the sign-in uses is proven with a DNS TXT record before the integration can be activated, and before a live product is pointed at a new hostname."
      actions={manage ? <AddHostname code={code} disabled={working !== null} onAnswer={onAnswer} /> : undefined}
    >
      <LoadGate load={load} what="the product's domains">
        {(data) => {
          if (!data) return null;
          const records = data.domains.filter((d) => d.record_value);
          return (
            <>
              {notice && (
                <Alert tone={notice.tone}>
                  <p>{notice.text}</p>
                </Alert>
              )}
              {block && <DomainBlockAlert block={block} domains={data} manage={manage} working={working} onRequest={(h) => void run(h, "request")} />}
              {!data.all_verified && data.unverified.length > 0 && (
                <Alert tone="warning" title={`Not proven yet: ${data.unverified.join(", ")}`}>
                  <p>The integration cannot be activated, or resumed, until every hostname it uses is verified.</p>
                </Alert>
              )}
              {data.domains.length === 0 ? (
                <EmptyState icon="globe" title="No hostnames yet" hint="They appear once the registration uses a URL." compact />
              ) : (
                <Table caption="Hostnames" head={["Hostname", "Status", "Method", "Last check", ...(manage ? ["Action"] : [])]}>
                  {data.domains.map((d) => {
                    const action = manage ? domainAction(d) : null;
                    return (
                      <tr key={d.hostname}>
                        <td>
                          <span className={kit.row}>
                            <code className={kit.mono}>{d.hostname}</code>
                            {!d.in_use && <Badge tone="neutral">Not in use</Badge>}
                          </span>
                        </td>
                        <td>
                          <Stack
                            main={<StatusBadge value={d.status} label={DOMAIN_STATUS[d.status] ?? undefined} />}
                            sub={d.status === "lapsed" ? "The record disappeared on repeated re-checks." : undefined}
                          />
                        </td>
                        <td>
                          <Stack
                            main={METHOD_LABEL[d.method] ?? d.method}
                            sub={d.method === "pre_existing" ? "Ask for a DNS proof to replace it." : undefined}
                          />
                        </td>
                        <td>
                          {d.last_checked_at ? (
                            <Stack main={formatDateTime(d.last_checked_at)} sub={d.last_outcome ? <KeyText>{d.last_outcome}</KeyText> : undefined} />
                          ) : (
                            <span className={kit.muted}>Not checked yet</span>
                          )}
                        </td>
                        {manage && (
                          <td>
                            {action === "verify" && (
                              <Button size="sm" aria-label={`Verify ${d.hostname}`} loading={working === d.hostname} disabled={working !== null} onClick={() => void run(d.hostname, "verify")}>
                                Verify
                              </Button>
                            )}
                            {action === "request" && (
                              <Button
                                size="sm"
                                aria-label={`${d.status === "verified" ? "Ask for a DNS proof" : "Get TXT value"} for ${d.hostname}`}
                                loading={working === d.hostname}
                                disabled={working !== null}
                                onClick={() => void run(d.hostname, "request")}
                              >
                                {d.status === "verified" ? "Ask for a DNS proof" : "Get TXT value"}
                              </Button>
                            )}
                            {action === null && <span className={kit.muted}>{EMPTY}</span>}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </Table>
              )}
              {records.length > 0 && (
                <div className={styles.records}>
                  <h3 className={styles.subHead}>TXT records to publish</h3>
                  {records.map((d) => (
                    <div key={d.hostname} className={styles.record}>
                      <p className={styles.recordHost}>{d.hostname}</p>
                      <DescriptionList
                        items={[
                          { label: "Name", value: <RecordValue value={d.record_name} label={`Copy record name for ${d.hostname}`} /> },
                          { label: "Type", value: d.record_type || "TXT" },
                          { label: "Value", value: <RecordValue value={d.record_value} label={`Copy record value for ${d.hostname}`} /> },
                        ]}
                      />
                      <p className={styles.recordNote}>{recordNote(d)}</p>
                    </div>
                  ))}
                </div>
              )}
            </>
          );
        }}
      </LoadGate>
    </Card>
  );
}

// --- back-channel logout -------------------------------------------------------------------

const DELIVERY_STATUS: Record<BackchannelDelivery["status"], string> = {
  pending: "Pending",
  delivered: "Delivered",
  failed: "Failed",
  dead: "Gave up",
};

const SCOPE: Record<BackchannelDelivery["scope"], string> = {
  session: "One sign-in ended",
  everywhere: "Signed out everywhere",
};

const RETRYABLE_4XX = new Set([408, 425, 429]);

/** A short plain-language reading of a delivery outcome category
 * (`http_503`, `connect_timeout`, `rejected_<reason>`), or "". */
export function outcomeHint(outcome: string, status?: BackchannelDelivery["status"]): string {
  const retried = status === "dead" ? "WorkEasy360 gave up after its last retry." : "Delivery is retried.";
  const http = /^http_(\d{3})$/.exec(outcome);
  if (http) {
    const code = Number(http[1]);
    if (code >= 200 && code < 300) return "The product accepted the logout token.";
    if (RETRYABLE_4XX.has(code)) return `The product asked to try again later. ${retried}`;
    if (code >= 400 && code < 500) return "The product refused the logout token: check its back-channel logout handler and client ID. Not retried.";
    if (code >= 500) return `The product was unavailable. ${retried}`;
    return `The product gave an unexpected answer. ${retried}`;
  }
  if (outcome.startsWith("rejected_")) return "WorkEasy360 would not call this address (its outbound address checks refused it). Not retried.";
  switch (outcome) {
    case "connect_timeout":
    case "read_timeout":
    case "dns_timeout":
      return `The product did not answer in time. ${retried}`;
    case "dns_failure":
      return `The hostname could not be resolved. ${retried}`;
    case "tls_error":
      return `The product's TLS certificate or handshake failed. ${retried}`;
    case "no_backchannel_uri":
      return "The back-channel logout URL was removed before this was sent.";
    case "mint_failed":
    case "delivery_error":
      return `WorkEasy360 could not send it this time. ${retried}`;
    default:
      return "";
  }
}

const times = (n: number, one: string, many = `${one}s`) => `${formatNumber(n)} ${n === 1 ? one : many}`;

function RetryStatus({ summary }: { summary: BackchannelSummary }) {
  const retry = summary.retry ?? { pending: 0, retrying: 0, next_attempt_at: null, max_attempts: 0, attempt_limit: 0 };
  const dead = summary.counts?.dead ?? 0;
  const hours = summary.window_hours ?? 24;
  if (retry.pending === 0 && dead === 0) return <span>Nothing is waiting to be retried.</span>;
  return (
    <span className={styles.lines}>
      {retry.pending > 0 && (
        <span>
          {formatNumber(retry.pending)} pending · {formatNumber(retry.retrying)} retrying
        </span>
      )}
      {retry.next_attempt_at && (
        <span>
          Next attempt {formatDateTime(retry.next_attempt_at)} ({formatRelative(retry.next_attempt_at)})
        </span>
      )}
      {retry.pending > 0 && retry.attempt_limit > 0 && (
        <span className={kit.muted}>
          Most attempts so far: {formatNumber(retry.max_attempts)} of {formatNumber(retry.attempt_limit)}
        </span>
      )}
      {dead > 0 && (
        <span className={kit.row}>
          <Badge tone="danger" icon="alert">
            Gave up
          </Badge>
          <span>
            {times(dead, "delivery", "deliveries")} in the last {hours} h, after every retry
            {retry.attempt_limit > 0 ? ` (${retry.attempt_limit} attempts)` : ""}.
          </span>
        </span>
      )}
    </span>
  );
}

function DeliveryStatus({ summary }: { summary: BackchannelSummary }) {
  const counts = summary.counts ?? {};
  const n = (key: BackchannelDelivery["status"]) => counts[key] ?? 0;
  const hours = summary.window_hours ?? 24;
  const last = summary.last_delivery ?? null;
  const error = summary.last_error ?? null;
  return (
    <>
      <DescriptionList
        items={[
          {
            label: "Last delivery status",
            value: last ? (
              <Stack
                main={<StatusBadge value={last.status} label={DELIVERY_STATUS[last.status]} />}
                sub={`${formatDateTime(last.created_at)} · ${times(last.attempts, "attempt")} · ${SCOPE[last.scope] ?? last.scope}`}
              />
            ) : (
              `None in the last ${hours} h`
            ),
          },
          {
            label: "Last delivery error",
            value: error ? (
              <Stack
                main={
                  <span className={kit.row}>
                    <KeyText>{error.outcome || "unknown"}</KeyText>
                    <span>{outcomeHint(error.outcome, error.status)}</span>
                  </span>
                }
                sub={`${formatDateTime(error.finished_at ?? error.created_at)} · ${DELIVERY_STATUS[error.status] ?? error.status} · ${times(error.attempts, "attempt")}`}
              />
            ) : (
              "None"
            ),
          },
          { label: "Retry status", value: <RetryStatus summary={summary} /> },
        ]}
      />
      <StatGrid label={`Deliveries in the last ${hours} hours`}>
        <StatCard label="Delivered" value={formatNumber(n("delivered"))} icon="checkCircle" tone="green" note={`Last ${hours} h`} />
        <StatCard label="Pending" value={formatNumber(n("pending"))} icon="clock" tone="blue" note="Still being sent" />
        <StatCard label="Failed" value={formatNumber(n("failed"))} icon="xCircle" tone="orange" note="Refused; not retried" />
        <StatCard label="Gave up" value={formatNumber(n("dead"))} icon="alert" tone="red" note="After every retry" />
      </StatGrid>
    </>
  );
}

export function BackchannelCard({ code, integration, manage }: { code: string; integration: ConsoleIntegration; manage: boolean }) {
  const uri = integration.backchannel_logout_uri ?? "";
  const localOnly = integration.sign_in_mode === "local_only";
  const [summary, setSummary] = useState<BackchannelSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setProblem(null);
    adminApi
      .integrationBackchannel(code)
      .then(setSummary)
      .catch((err: unknown) => setProblem(`Couldn't load the delivery status. ${describeError(err)}`))
      .finally(() => setLoading(false));
  };

  const meaning = uri
    ? "When a WorkEasy360 sign-in ends, or someone signs out everywhere, a logout token is posted to this URL and the product ends its own session within seconds."
    : localOnly
      ? "Not used: the product signs people in itself."
      : `Someone who signs out of WorkEasy360 stays signed in to the product until its next entitlement check.${manage ? " Add the URL with Edit authentication, under Authentication & credentials." : ""}`;

  return (
    <Card
      title="Back-channel logout"
      labelledBy="sso-backchannel"
      description="How a WorkEasy360 sign-out reaches the product's own session."
      actions={
        summary ? (
          <Button size="sm" icon="refresh" aria-label="Refresh delivery status" loading={loading} onClick={load}>
            Refresh
          </Button>
        ) : undefined
      }
    >
      <DescriptionList
        items={[
          { label: "Back-channel logout URL", value: uri ? <code className={kit.mono}>{uri}</code> : null },
          {
            label: "Registration status",
            value: (
              <Stack
                main={<BoolBadge value={!!uri} yes="Registered" no="Not registered" noTone={localOnly ? "neutral" : "warning"} />}
                sub={meaning}
              />
            ),
          },
        ]}
      />
      {summary ? (
        <DeliveryStatus summary={summary} />
      ) : (
        <div className={styles.toolbar}>
          <Button icon="history" loading={loading} onClick={load}>
            Show delivery status
          </Button>
        </div>
      )}
      {problem && (
        <p className="w360-error-text" role="alert">
          {problem}
        </p>
      )}
    </Card>
  );
}
