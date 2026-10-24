"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { partnerConsoleApi, staffWhiteLabel, type WhiteLabelSetup as SetupData } from "@/lib/api/partnerConsole";
import { describeError, type Load } from "../ui";
import { brandBody, checkLogo, draftOf, groupDirty, readDataUrl, serverProblem, type BrandDraft, type FieldErrors, type Group } from "./model";
import type { Ctx, Nav } from "./shared";

/**
 * The state the white-label steps share - the partner's setup read, the brand
 * draft being typed, the chosen logo and the field errors - for one partner,
 * staff side. The steps run inside the partner's Complete Your Profile
 * (ADR-048); every write is a command that always existed (``update_brand``,
 * ``upload_logo`` and the domain commands).
 *
 * Each step that edits the brand saves with ``PUT brand`` as its own action. The
 * brand's own fields
 * are replaced by every save and its portal fields are merged, so each save sends
 * the stored brand fields back (``brandBody``) and re-reads the setup for the new
 * version.
 */
export function useWhiteLabelContext(partnerId: string, nav: Nav): { load: Load<SetupData>; ctx: Ctx | null; stale: boolean } {
  const [load, setLoad] = useState<Load<SetupData>>({ status: "loading" });
  const [stale, setStale] = useState(false);
  const api = useMemo(() => staffWhiteLabel(partnerId), [partnerId]);
  const [edited, setEdited] = useState<BrandDraft | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [logo, setLogo] = useState<Ctx["logo"]>(null);
  // A logo already uploaded for this draft: a retry after a failed save must not upload it twice.
  const uploaded = useRef<{ file: File; id: string } | null>(null);

  useEffect(() => {
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
    try {
      const data = await partnerConsoleApi.whiteLabelSetup(partnerId);
      setLoad({ status: "ready", data });
      setStale(false);
    } catch {
      setStale(true);
    }
  }, [partnerId]);

  const setup = load.status === "ready" ? load.data : null;
  if (setup === null) return { load, ctx: null, stale };

  const draft = edited ?? draftOf(setup.brand);
  const stored = setup.brand;
  const savedLogo = stored?.logo_file_id ? `${api.logoUrl ?? ""}?v=${stored.version}` : null;

  const patch = (change: Partial<BrandDraft>) => {
    setEdited({ ...draft, ...change });
    setErrors((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(change)) delete next[key as keyof FieldErrors];
      return next;
    });
  };

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
    try {
      let logoId: string | undefined;
      if (logo && (groups.includes("identity") || stored === null)) {
        if (uploaded.current?.file !== logo.file) uploaded.current = { file: logo.file, id: (await api.uploadLogo(logo.file)).logo_file_id };
        logoId = uploaded.current.id;
      }
      await api.saveBrand(brandBody(stored, draft, groups, logoId), stored?.version);
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
  return { load, ctx, stale };
}
