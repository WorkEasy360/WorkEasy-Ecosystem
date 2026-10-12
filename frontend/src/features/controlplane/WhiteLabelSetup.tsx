"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api/client";
import { partnerConsoleApi, staffWhiteLabel, type WhiteLabelSetup as SetupData } from "@/lib/api/partnerConsole";
import { can, useAdmin } from "./ControlShell";
import { describeError, Status, type Load } from "./ui";
import { AppearanceStep, IdentityStep } from "./whitelabel/BrandSteps";
import { DomainStep } from "./whitelabel/DomainStep";
import {
  STEPS,
  STEP_LABELS,
  brandBody,
  checkLogo,
  draftOf,
  groupDirty,
  readDataUrl,
  serverProblem,
  type BrandDraft,
  type FieldErrors,
  type Group,
  type StepKey,
} from "./whitelabel/model";
import { CommunicationStep, CustomerPortalStep, LoginExperienceStep } from "./whitelabel/PortalSteps";
import { ProductsStep } from "./whitelabel/ProductsStep";
import { ConnectionStep, ReviewStep } from "./whitelabel/ReviewSteps";
import { SelectPartnerStep } from "./whitelabel/SelectPartnerStep";
import type { Ctx, Nav } from "./whitelabel/shared";
import styles from "./ControlPlane.module.css";
import css from "./WhiteLabelSetup.module.css";

/**
 * The staff white-label setup wizard: ten steps that configure an *existing*
 * partner account (``docs/partner-setup/design.md``).
 *
 * It never creates a partner and never stores a kind of partner. "Reseller",
 * "White-label" and "Affiliate" are labels the server derives from what the
 * partner can do, and every write is a command that always existed: ``update_brand``,
 * ``upload_logo``, ``update_product_grant``, the three domain commands and
 * ``enable_white_label``.
 *
 * Each step that edits the brand saves with ``PUT brand`` as its own action, so a
 * second-factor prompt appears at the step that needs it. The brand's own fields
 * are replaced by every save and its portal fields are merged, so each save sends
 * the stored brand fields back (see ``brandBody``) and re-reads the setup for the
 * new version. Nothing is served until white-label is enabled on the last step.
 *
 * ``partnerId`` pre-selects a partner (the setup then starts at the brand step);
 * without it the setup starts by choosing one.
 */
export function WhiteLabelSetup({ partnerId: initial }: { partnerId?: string }) {
  const [partnerId, setPartnerId] = useState<string | null>(initial ?? null);
  const [step, setStep] = useState<StepKey>(initial ? "brand" : "partner");
  const [moved, setMoved] = useState(false);
  const [notice, setNotice] = useState("");

  const go = useCallback((next: StepKey) => {
    setMoved(true);
    setNotice("");
    setStep(next);
  }, []);

  const nav: Nav = { step, go, announce: setNotice, focusHeading: moved };

  return (
    <div className={styles.stack}>
      <div className={css.wizard}>
        <p className={styles.muted}>
          <Link href="/admin/partners/white-label">All white-label partners</Link>
        </p>
        <h1>White-label setup</h1>
        <ol className={css.progress} aria-label="Setup progress">
          {STEPS.map((key, i) => (
            <li key={key} className={css.progressItem} aria-current={key === step ? "step" : undefined}>
              <span aria-hidden="true">{i + 1}.</span> {STEP_LABELS[key]}
            </li>
          ))}
        </ol>
        <p role="status" aria-live="polite" className={css.notice}>
          {notice}
        </p>
        <Wizard
          key={partnerId ?? "none"}
          partnerId={partnerId}
          step={step}
          nav={nav}
          onChoose={(id) => {
            setPartnerId(id);
            go("brand");
          }}
        />
      </div>
    </div>
  );
}

/** The one read the whole wizard uses, and a way to re-read it after a change. */
function useSetup(partnerId: string | null) {
  const [load, setLoad] = useState<Load<SetupData>>({ status: "loading" });
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!partnerId) return;
    let cancelled = false;
    partnerConsoleApi
      .whiteLabelSetup(partnerId)
      .then((data) => {
        if (!cancelled) setLoad({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoad(
          err instanceof ApiError && (err.status === 403 || err.status === 404)
            ? { status: "forbidden" }
            : { status: "error", message: describeError(err) },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [partnerId]);

  /** Never throws: a change that went through must not read as a failure because
   * the follow-up read did. The person is told to reload instead. */
  const refresh = useCallback(async () => {
    if (!partnerId) return;
    try {
      const data = await partnerConsoleApi.whiteLabelSetup(partnerId);
      setLoad({ status: "ready", data });
      setStale(false);
    } catch {
      setStale(true);
    }
  }, [partnerId]);

  return { load, refresh, stale };
}

function Wizard({
  partnerId,
  step,
  nav,
  onChoose,
}: {
  partnerId: string | null;
  step: StepKey;
  nav: Nav;
  onChoose: (partnerId: string) => void;
}) {
  const { permissions } = useAdmin();
  const manage = can(permissions, "platform.staff_partner.manage");
  const { load, refresh, stale } = useSetup(partnerId);
  const api = useMemo(() => (partnerId ? staffWhiteLabel(partnerId) : null), [partnerId]);
  const [edited, setEdited] = useState<BrandDraft | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [logo, setLogo] = useState<Ctx["logo"]>(null);
  // A logo already uploaded for this draft: a retry after a failed save must not upload it twice.
  const uploaded = useRef<{ file: File; id: string } | null>(null);

  const setup = load.status === "ready" ? load.data : null;
  const draft = edited ?? draftOf(setup?.brand ?? null);

  if (step === "partner" || partnerId === null) {
    return <SelectPartnerStep nav={nav} current={partnerId} onChoose={onChoose} />;
  }

  if (load.status === "loading") return <p aria-live="polite">Loading the partner…</p>;
  if (load.status === "forbidden") return <ChooseAnother nav={nav} message="You don't have access to this partner, or it does not exist." />;
  if (load.status === "error") return <ChooseAnother nav={nav} message={load.message} />;
  if (setup === null || api === null) return null;

  if (!manage) return <p role="alert">You need the permission to change partners to set up white-label.</p>;
  if (setup.partner.status === "terminated") {
    return <ChooseAnother nav={nav} message="A terminated partner cannot take a new brand or domain." />;
  }

  const patch = (change: Partial<BrandDraft>) => {
    setEdited({ ...draft, ...change });
    setErrors((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(change)) delete next[key as keyof FieldErrors];
      return next;
    });
  };

  const stored = setup.brand;
  const savedLogo = stored?.logo_file_id ? `${api.logoUrl ?? ""}?v=${stored.version}` : null;

  async function pickLogo(file: File | undefined) {
    const without = (prev: FieldErrors) => {
      const next = { ...prev };
      delete next.logo;
      return next;
    };
    if (!file) {
      setLogo(null);
      setErrors(without);
      return;
    }
    const problem = await checkLogo(file);
    if (problem) {
      setLogo(null);
      setErrors((prev) => ({ ...prev, logo: problem }));
      return;
    }
    const preview = await readDataUrl(file);
    if (preview === null) {
      setLogo(null);
      setErrors((prev) => ({ ...prev, logo: "That file could not be read. Choose it again." }));
      return;
    }
    setLogo({ file, preview });
    setErrors(without);
  }

  async function saveBrand(groups: Group[]) {
    const client = api;
    if (!client) return;
    try {
      let logoId: string | undefined;
      if (logo && (groups.includes("identity") || stored === null)) {
        if (uploaded.current?.file !== logo.file) uploaded.current = { file: logo.file, id: (await client.uploadLogo(logo.file)).logo_file_id };
        logoId = uploaded.current.id;
      }
      await client.saveBrand(brandBody(stored, draft, groups, logoId), stored?.version);
    } catch (err) {
      const found = serverProblem(err);
      if (found) setErrors((prev) => ({ ...prev, [found.key]: found.message }));
      throw err;
    }
    uploaded.current = null;
    setLogo(null);
    await refresh();
  }

  const ctx: Ctx = {
    partnerId,
    setup,
    api,
    draft,
    patch,
    errors,
    // Local checks replace the field errors, except the logo's: that one belongs to the file picker.
    setErrors: (found) => setErrors((prev) => ({ ...found, ...(prev.logo ? { logo: prev.logo } : {}) })),
    dirty: (group) => groupDirty(draft, stored, group),
    logo,
    logoSrc: logo?.preview ?? savedLogo,
    pickLogo,
    saveBrand,
    refresh,
    nav,
  };

  return (
    <>
      <p>
        Partner: <strong>{setup.partner.display_name}</strong> <Status value={setup.partner.status} />
      </p>
      {stale && (
        <p role="alert" className="w360-error-text">
          The change was saved, but this page could not be refreshed. Reload the page before making more changes.
        </p>
      )}
      <StepBody step={step} ctx={ctx} />
    </>
  );
}

function StepBody({ step, ctx }: { step: StepKey; ctx: Ctx }) {
  switch (step) {
    case "brand":
      return <IdentityStep ctx={ctx} />;
    case "appearance":
      return <AppearanceStep ctx={ctx} />;
    case "products":
      return <ProductsStep ctx={ctx} />;
    case "portal":
      return <CustomerPortalStep ctx={ctx} />;
    case "communication":
      return <CommunicationStep ctx={ctx} />;
    case "domain":
      return <DomainStep ctx={ctx} />;
    case "login":
      return <LoginExperienceStep ctx={ctx} />;
    case "connection":
      return <ConnectionStep ctx={ctx} />;
    case "review":
      return <ReviewStep ctx={ctx} />;
    default:
      return null;
  }
}

function ChooseAnother({ nav, message }: { nav: Nav; message: string }) {
  return (
    <div className={css.step}>
      <p role="alert">{message}</p>
      <div>
        <button type="button" className="w360-btn w360-btn-secondary" onClick={() => nav.go("partner")}>
          Choose a different partner
        </button>
      </div>
    </div>
  );
}
