"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { adminApi, type Brand, type DomainRow } from "@/lib/api/controlPlane";
import { portalWhiteLabel, staffWhiteLabel, type WhiteLabelApi } from "@/lib/api/partnerConsole";
import { can, useAdmin, usePartner } from "./ControlShell";
import { LivePreview } from "./BrandPreview";
import { LoadGate, Status, useAction, useLoad } from "./ui";
import { ErrorSummary, TextField } from "./widgets";
import { safeColor } from "./useBranding";
import styles from "./ControlPlane.module.css";
import local from "./PartnerConsole.module.css";

/**
 * The white-label setup wizard (Phase 10d): brand identity, preview, domain
 * request, DNS verification, activation.
 *
 * **One wizard, two callers.** A partner runs it from its own portal; staff run
 * it for a partner from the console. The screens are the same and the calls go
 * through the same commands - only the address differs (``WhiteLabelApi``).
 *
 * **Every step is its own action.** Saving a brand and requesting a domain are
 * separate submits, each through ``useAction``, so a second-factor prompt
 * appears at the step that needs one and is never hidden inside a combined
 * submit. (For a partner, a brand is not a dangerous action and a domain is; for
 * staff, both are - the server decides, and this screen just handles the
 * prompt whichever step raises it.)
 *
 * **Activation is never automatic.** Verification proves a name; making it serve
 * a brand is a separate, deliberate step, and only from ``verified``.
 *
 * The preview is drawn from the draft in memory, and only "Save brand" persists.
 */

const STEPS = ["brand", "preview", "domain", "verify", "activate"] as const;
type StepKey = (typeof STEPS)[number];

const STEP_LABELS: Record<StepKey, string> = {
  brand: "Brand identity",
  preview: "Preview",
  domain: "Domain request",
  verify: "DNS verification",
  activate: "Activation",
};

const LOGO_TYPES = ["image/png", "image/jpeg"];
const MAX_LOGO_BYTES = 512 * 1024;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const LIVE_RANK: Record<string, number> = { active: 0, verified: 1, verification_pending: 2, requested: 3 };

interface BrandDraft {
  name: string;
  accent: string;
  email: string;
  url: string;
}

const draftOf = (brand: Brand | null): BrandDraft => ({
  name: brand?.brand_name ?? "",
  accent: brand?.accent_color ?? "",
  email: brand?.support_email ?? "",
  url: brand?.support_url ?? "",
});

const FAILURES: Record<string, string> = {
  record_not_found: "The record was not found yet. DNS changes can take a while to spread; check it again in a few minutes.",
  lookup_failed: "DNS could not be reached just now. Try again shortly - this is not a failed check.",
  verification_expired: "This request expired. Request the domain again to get a new record.",
  hostname_claimed: "Another partner has already proved control of this name.",
  control_lost: "The record disappeared, so the domain was taken off air.",
};

/** The domain the wizard is about: the most advanced live request, if any. */
export function currentDomain(rows: DomainRow[]): DomainRow | null {
  const live = rows.filter((r) => r.status in LIVE_RANK).sort((a, b) => LIVE_RANK[a.status] - LIVE_RANK[b.status]);
  return live[0] ?? null;
}

export function WhiteLabelWizard({ api, partnerName }: { api: WhiteLabelApi; partnerName: string }) {
  const [rev, setRev] = useState(0);
  const [brandLoad] = useLoad(() => api.brand(), [api, rev]);
  const [domainLoad] = useLoad(() => api.domains(), [api, rev]);
  const { act, busy, feedback } = useAction(() => setRev((n) => n + 1));
  const [step, setStep] = useState<StepKey>("brand");
  const [edited, setEdited] = useState<BrandDraft | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [filePreview, setFilePreview] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [hostname, setHostname] = useState("");
  const hostId = useId();
  const heading = useRef<HTMLHeadingElement | null>(null);
  const summary = useRef<HTMLDivElement | null>(null);
  const first = useRef(true);
  // A logo already uploaded for this draft: a retry after a failed save must not upload it twice.
  const uploaded = useRef<{ file: File; id: string } | null>(null);

  const brand = brandLoad.status === "ready" ? brandLoad.data.brand : null;
  const domains = domainLoad.status === "ready" ? domainLoad.data.results : [];
  const domain = currentDomain(domains);
  const draft = edited ?? draftOf(brand);
  const set = (patch: Partial<BrandDraft>) => setEdited({ ...draft, ...patch });
  const dirty = edited !== null || file !== null;
  const saved = brand !== null && !dirty;
  const logoSrc = filePreview ?? (brand?.logo_file_id ? api.logoUrl : null);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    heading.current?.focus();
  }, [step]);
  useEffect(() => {
    if (problems.length > 0) summary.current?.focus();
  }, [problems]);

  const index = STEPS.indexOf(step);

  function brandProblems(): string[] {
    const out: string[] = [];
    if (!draft.name.trim()) out.push("Enter the brand name.");
    if (draft.accent.trim() && safeColor(draft.accent.trim()) === null) out.push("The accent colour must be a six-digit hex colour such as #1a56db.");
    if (draft.email.trim() && !EMAIL_PATTERN.test(draft.email.trim())) out.push("Enter a valid support email, or leave it empty.");
    if (draft.url.trim() && !/^https:\/\/[^\s/?#]+\.[^\s]+$/i.test(draft.url.trim())) out.push("The support page must be a plain https:// address, or empty.");
    return out;
  }

  function pickLogo(chosen: File | undefined) {
    setProblems([]);
    setFile(null);
    setFilePreview(null);
    if (!chosen) return;
    if (!LOGO_TYPES.includes(chosen.type)) {
      setProblems(["Use a PNG or JPEG image. SVG is not accepted."]);
      return;
    }
    if (chosen.size > MAX_LOGO_BYTES) {
      setProblems(["The logo must be 512 KB or smaller."]);
      return;
    }
    setFile(chosen);
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" && setFilePreview(reader.result);
    reader.readAsDataURL(chosen);
  }

  async function goTo(next: StepKey) {
    setProblems([]);
    setStep(next);
  }

  function onNext() {
    if (step === "brand") {
      const found = brandProblems();
      setProblems(found);
      if (found.length > 0) return;
    }
    void goTo(STEPS[index + 1]);
  }

  function saveBrand() {
    const found = brandProblems();
    setProblems(found);
    if (found.length > 0) return;
    void act(async () => {
      let logoId = brand?.logo_file_id ?? undefined;
      if (file) {
        if (uploaded.current?.file !== file) uploaded.current = { file, id: (await api.uploadLogo(file)).logo_file_id };
        logoId = uploaded.current.id;
      }
      await api.saveBrand(
        {
          brand_name: draft.name.trim(),
          accent_color: draft.accent.trim(),
          support_email: draft.email.trim(),
          support_url: draft.url.trim(),
          theme: {},
          logo_file_id: logoId,
        },
        brand?.version,
      );
      setEdited(null);
      setFile(null);
      setFilePreview(null);
      uploaded.current = null;
    }, "Brand saved.");
  }

  // What lets the person move on from each step - facts the server reported.
  const canAdvance: Record<StepKey, boolean> = {
    brand: true,
    preview: saved,
    domain: domain !== null,
    verify: domain !== null && (domain.status === "verified" || domain.status === "active"),
    activate: false,
  };

  return (
    <div className={styles.stack}>
      <ol className={local.steps} aria-label="Steps">
        {STEPS.map((key, i) => (
          <li key={key} className={local.step} aria-current={key === step ? "step" : undefined}>
            <span aria-hidden="true">{i + 1}.</span> {STEP_LABELS[key]}
          </li>
        ))}
      </ol>
      <div className={local.wizard}>
        <h2 ref={heading} tabIndex={-1} className={styles.cardTitle}>
          Step {index + 1} of {STEPS.length}: {STEP_LABELS[step]}
        </h2>
        <ErrorSummary errors={problems} focusRef={summary} />
        {feedback}
        <LoadGate load={brandLoad} what="the brand">
          {() => (
            <LoadGate load={domainLoad} what="the domains">
              {() => (
                <>
                  {step === "brand" && (
                    <>
                      <p className={styles.muted}>
                        Your name, accent colour and logo are shown to {partnerName}&apos;s customers on its domain. Nothing is saved until you
                        approve it on the next step.
                      </p>
                      <TextField label="Brand name" value={draft.name} onChange={(v) => set({ name: v })} maxLength={80} required autoComplete="off" />
                      <TextField
                        label="Accent colour (hex, e.g. #1a56db)"
                        value={draft.accent}
                        onChange={(v) => set({ accent: v })}
                        maxLength={7}
                        spellCheck={false}
                        autoComplete="off"
                        error={draft.accent.trim() && safeColor(draft.accent.trim()) === null ? "Enter a six-digit hex colour such as #1a56db." : undefined}
                      />
                      <LogoField brand={brand} file={file} onPick={pickLogo} />
                      <div className={local.split}>
                        <TextField label="Support email (optional)" type="email" value={draft.email} onChange={(v) => set({ email: v })} maxLength={254} />
                        <TextField label="Support page (https, optional)" value={draft.url} onChange={(v) => set({ url: v })} maxLength={300} />
                      </div>
                    </>
                  )}

                  {step === "preview" && (
                    <>
                      <LivePreview brandName={draft.name} accent={draft.accent} logoSrc={logoSrc} supportEmail={draft.email} />
                      {saved ? (
                        <p role="status">This brand is saved.</p>
                      ) : (
                        <p className={styles.muted}>This is a preview only. Approve it to save the brand.</p>
                      )}
                      <div>
                        <button type="button" className="w360-btn w360-btn-primary" onClick={saveBrand} disabled={busy || !dirty}>
                          Save brand
                        </button>
                      </div>
                    </>
                  )}

                  {step === "domain" && (
                    <DomainStep
                      domain={domain}
                      closed={domains.filter((d) => !(d.status in LIVE_RANK))}
                      hostname={hostname}
                      setHostname={setHostname}
                      hostId={hostId}
                      busy={busy}
                      onRequest={() =>
                        void act(async () => {
                          await api.requestDomain(hostname.trim().toLowerCase());
                          setHostname("");
                        }, "Domain requested. Publish the DNS record, then check it.")
                      }
                    />
                  )}

                  {step === "verify" && (
                    <VerifyStep
                      domain={domain}
                      busy={busy}
                      onCheck={() => domain && void act(() => api.verifyDomain(domain.id), "Checked the DNS record.")}
                    />
                  )}

                  {step === "activate" && (
                    <ActivateStep
                      brand={brand}
                      domain={domain}
                      busy={busy}
                      onActivate={() => domain && void act(() => api.activateDomain(domain.id), "The domain is active and now serves the brand.")}
                      onDisable={() => domain && void act(() => api.disableDomain(domain.id), "The domain was disabled and no longer serves the brand.")}
                    />
                  )}
                </>
              )}
            </LoadGate>
          )}
        </LoadGate>

        <div className={styles.row}>
          <button type="button" className="w360-btn w360-btn-secondary" disabled={index === 0 || busy} onClick={() => void goTo(STEPS[index - 1])}>
            Back
          </button>
          {step !== "activate" && (
            <button type="button" className="w360-btn w360-btn-primary" disabled={busy || !canAdvance[step]} onClick={onNext}>
              Next
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function LogoField({ brand, file, onPick }: { brand: Brand | null; file: File | null; onPick: (file: File | undefined) => void }) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label className="w360-label" htmlFor={id}>
        Logo (PNG or JPEG, up to 512 KB)
      </label>
      <input id={id} type="file" accept="image/png,image/jpeg" onChange={(e) => onPick(e.target.files?.[0])} />
      {file && <span className={styles.muted}>{file.name} will replace the saved logo.</span>}
      {!file && brand?.logo_file_id && <span className={styles.muted}>A logo is saved. Choose a file to replace it.</span>}
    </div>
  );
}

function DomainStep({
  domain,
  closed,
  hostname,
  setHostname,
  hostId,
  busy,
  onRequest,
}: {
  domain: DomainRow | null;
  closed: DomainRow[];
  hostname: string;
  setHostname: (v: string) => void;
  hostId: string;
  busy: boolean;
  onRequest: () => void;
}) {
  return (
    <>
      <p className={styles.muted}>
        A custom domain serves the brand on your own address, such as app.example.com. Requesting one only asks: nothing is served until you
        prove you control the name, and activate it.
      </p>
      {domain ? (
        <p role="status">
          <span className={styles.mono}>{domain.hostname}</span> is requested: <Status value={domain.status} />. Continue to prove you control it.
        </p>
      ) : (
        <form
          className={styles.form}
          onSubmit={(e) => {
            e.preventDefault();
            onRequest();
          }}
        >
          <div className={styles.field}>
            <label className="w360-label" htmlFor={hostId}>
              Hostname (e.g. app.example.com)
            </label>
            <input id={hostId} className="w360-input" required value={hostname} onChange={(e) => setHostname(e.target.value)} autoComplete="off" spellCheck={false} />
          </div>
          <div>
            <button type="submit" className="w360-btn w360-btn-primary" disabled={busy || !hostname.trim()}>
              Request domain
            </button>
          </div>
        </form>
      )}
      {closed.length > 0 && (
        <p className={styles.muted}>
          Closed requests: {closed.map((d) => `${d.hostname} (${d.status})`).join(", ")}.
        </p>
      )}
    </>
  );
}

function VerifyStep({ domain, busy, onCheck }: { domain: DomainRow | null; busy: boolean; onCheck: () => void }) {
  if (!domain) return <p role="status">Request a domain first.</p>;
  const proven = domain.status === "verified" || domain.status === "active";
  return (
    <>
      <p>
        Prove you control <span className={styles.mono}>{domain.hostname}</span> by adding this DNS record where the domain is managed:
      </p>
      <dl className={local.review}>
        <div>
          <dt>Type</dt>
          <dd>{domain.verification_record.type}</dd>
        </div>
        <div>
          <dt>Name</dt>
          <dd className={styles.mono}>{domain.verification_record.name}</dd>
        </div>
        <div>
          <dt>Value</dt>
          <dd className={styles.mono}>{domain.verification_record.value}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            <Status value={domain.status} />
          </dd>
        </div>
      </dl>
      {domain.failure_code && !proven && <p role="status">{FAILURES[domain.failure_code] ?? domain.failure_code.replace(/_/g, " ")}</p>}
      {proven ? (
        <p role="status">Ownership is verified. Continue to activation - the domain does not serve anything until you activate it.</p>
      ) : (
        <div>
          <button type="button" className="w360-btn w360-btn-primary" onClick={onCheck} disabled={busy}>
            Check verification
          </button>
        </div>
      )}
    </>
  );
}

function ActivateStep({
  brand,
  domain,
  busy,
  onActivate,
  onDisable,
}: {
  brand: Brand | null;
  domain: DomainRow | null;
  busy: boolean;
  onActivate: () => void;
  onDisable: () => void;
}) {
  if (!domain) return <p role="status">Request and verify a domain first.</p>;
  return (
    <>
      <dl className={local.review}>
        <div>
          <dt>Brand</dt>
          <dd>{brand ? `${brand.brand_name} (saved)` : "Not saved yet"}</dd>
        </div>
        <div>
          <dt>Domain</dt>
          <dd>
            <span className={styles.mono}>{domain.hostname}</span> <Status value={domain.status} />
          </dd>
        </div>
      </dl>
      {domain.status === "active" ? (
        <>
          <p role="status">Live: this domain serves the brand.</p>
          <div>
            <button type="button" className="w360-btn w360-btn-secondary" onClick={onDisable} disabled={busy}>
              Disable the domain
            </button>
          </div>
        </>
      ) : domain.status === "verified" ? (
        <>
          <p>Verification is done. Activating makes {domain.hostname} serve this brand to visitors. It changes only what they see - never who they are or what they may do.</p>
          <div>
            <button type="button" className="w360-btn w360-btn-primary" onClick={onActivate} disabled={busy || brand === null}>
              Activate the domain
            </button>
            {brand === null && <span className={styles.muted}> Save the brand first.</span>}
          </div>
        </>
      ) : (
        <p role="status">The domain is not verified yet, so it cannot be activated. Go back and check the DNS record.</p>
      )}
    </>
  );
}

// --- the two places it runs -------------------------------------------------------------------------------

/** Staff preparing a partner (Partners → White-label → a partner). */
export function StaffWhiteLabelPage({ partnerId }: { partnerId: string }) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const [load, reload] = useLoad(() => adminApi.partner(partnerId), [partnerId]);
  const api = useMemo(() => staffWhiteLabel(partnerId), [partnerId]);
  const { act, busy, feedback } = useAction(reload);
  const REQUIRED = ["branding.manage", "domain.manage"];

  return (
    <div className={styles.stack}>
      <LoadGate load={load} what="this partner">
        {(p) => {
          const held = new Set(p.capabilities.filter((c) => c.is_effective).map((c) => c.capability));
          const missing = REQUIRED.filter((k) => !held.has(k));
          return (
            <>
              <div className={local.headerRow}>
                <h1>
                  White-label: {p.display_name} <Status value={p.status} />
                </h1>
                <Link href={`/admin/partners/${p.id}`}>Back to the partner</Link>
              </div>
              {p.status === "terminated" ? (
                <p role="alert">A terminated partner cannot take a new brand or domain.</p>
              ) : !manage ? (
                <p role="alert">You need the permission to change partners to set up white-label.</p>
              ) : missing.length > 0 ? (
                <section className={`w360-card ${styles.card}`} aria-labelledby="enable-wl">
                  <h2 id="enable-wl" className={styles.cardTitle}>
                    Enable white-label
                  </h2>
                  <p>
                    White-label is a capability, not a kind of account. This partner does not yet hold: {missing.join(", ")}. Enabling
                    grants exactly those capabilities.
                  </p>
                  {feedback}
                  <button
                    type="button"
                    className="w360-btn w360-btn-primary"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        for (const key of missing) await adminApi.grantCapability(p.id, key, "Enabled for white-label setup");
                      }, "White-label enabled.")
                    }
                  >
                    Enable white-label
                  </button>
                </section>
              ) : (
                <WhiteLabelWizard api={api} partnerName={p.display_name} />
              )}
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}

/** A partner setting up its own white-label (its portal). */
export function PortalWhiteLabelPage({
  partnerId,
  partnerName,
  capabilities,
  active,
}: {
  partnerId: string;
  partnerName: string;
  capabilities: string[];
  active: boolean;
}) {
  const api = useMemo(() => portalWhiteLabel(partnerId), [partnerId]);
  const enabled = capabilities.includes("branding.manage") && capabilities.includes("domain.manage");
  return (
    <div className={styles.stack}>
      <h1>Set up white-label</h1>
      {!enabled ? (
        <p role="status">White-label is not enabled for your account. Ask Workeasy360 to enable it.</p>
      ) : !active ? (
        <p role="status">This partner account is not active, so its brand and domain cannot be changed.</p>
      ) : (
        <WhiteLabelWizard api={api} partnerName={partnerName} />
      )}
    </div>
  );
}

/** The partner portal's page: reads which partner it is and what it may do from
 * the portal context (which the server verified), never from the address. */
export function PortalWhiteLabelScreen() {
  const { partner } = usePartner();
  if (!partner) return null;
  return (
    <PortalWhiteLabelPage
      partnerId={partner.partner_id}
      partnerName={partner.display_name}
      capabilities={partner.capabilities}
      active={partner.status === "active"}
    />
  );
}
