import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import type { UpgradeRequest, UpgradeState } from "@/lib/api/partnerPanel";
import { WORKSPACES, entriesFor, pageAllowed, servedPaths } from "./experiences";
import { PAGE_PATHS } from "./workspacePages";
import { growView, targetFromSlug, targetSlug, upgradeHref } from "./upgrade";

/**
 * Which upgrade offers a dashboard draws (ADR-049, brief sections 7 and 10) and
 * that none of it reads stored state. The server decides what is possible; these
 * rules only lay its answer out.
 */

const REQUEST = (over: Partial<UpgradeRequest> = {}): UpgradeRequest => ({
  id: "r1",
  partner_id: "p1",
  from_experience: "affiliate",
  to_experience: "reseller",
  status: "requested",
  proposal: {},
  requested_at: "2026-10-24T10:00:00Z",
  submitted_at: null,
  decided_at: null,
  info_request_note: "",
  decision_note: "",
  capabilities_added: [],
  capabilities_removed: [],
  products_granted: [],
  version: 0,
  editable: true,
  ...over,
});

const STATE = (over: Partial<UpgradeState> = {}): UpgradeState => ({
  current: "affiliate",
  eligible: ["reseller", "white_label"],
  can_request: true,
  open: null,
  recent: [],
  steps: { reseller: [], white_label: [] },
  products: [],
  checklist: [],
  ...over,
});

describe("which upgrade buttons a dashboard shows", () => {
  it("an Affiliate is offered Reseller and White-label", () => {
    const view = growView(STATE(), "affiliate");
    expect(view).toMatchObject({ kind: "cards", targets: ["reseller", "white_label"], canAct: true });
  });

  it("a Reseller is offered White-label and never Reseller", () => {
    const view = growView(STATE({ current: "reseller", eligible: ["white_label"] }), "reseller");
    expect(view).toMatchObject({ kind: "cards", targets: ["white_label"] });
  });

  it("White-label has nothing above it, so nothing is drawn", () => {
    expect(growView(STATE({ current: "white_label", eligible: [] }), "white_label")).toEqual({ kind: "none" });
  });

  it("an open request replaces the cards: a partner has at most one", () => {
    const open = REQUEST({ status: "pending_approval", editable: false });
    expect(growView(STATE({ open }), "affiliate")).toMatchObject({ kind: "open", request: { status: "pending_approval" } });
  });

  it("a member who cannot act is shown the offer without a way to act", () => {
    expect(growView(STATE({ can_request: false }), "affiliate")).toMatchObject({ kind: "cards", canAct: false });
  });

  it("points an approved partner, still on its old workspace, to the new one", () => {
    const approved = REQUEST({ status: "approved", editable: false });
    const view = growView(STATE({ current: "reseller", eligible: ["white_label"], recent: [approved] }), "affiliate");
    expect(view).toMatchObject({ kind: "cards", note: { kind: "approved", target: "reseller" } });
    // On the new workspace itself there is nothing to announce.
    const there = growView(STATE({ current: "reseller", eligible: ["white_label"], recent: [approved] }), "reseller");
    expect(there).toMatchObject({ kind: "cards", note: null });
  });

  it("repeats staff's words when a request was not approved, and lets the partner ask again", () => {
    const rejected = REQUEST({ status: "rejected", editable: false, decision_note: "Not yet" });
    const view = growView(STATE({ recent: [rejected] }), "affiliate");
    expect(view).toMatchObject({ kind: "cards", targets: ["reseller", "white_label"], note: { kind: "rejected", text: "Not yet" } });
  });
});

describe("addresses", () => {
  it("maps targets to address segments both ways", () => {
    expect(targetSlug("white_label")).toBe("white-label");
    expect(targetFromSlug("white-label")).toBe("white_label");
    expect(targetFromSlug("reseller")).toBe("reseller");
    expect(targetFromSlug("affiliate")).toBeNull();
    expect(upgradeHref("/partner/affiliate", "reseller")).toBe("/partner/affiliate/upgrade/reseller");
    expect(upgradeHref("/partner/affiliate")).toBe("/partner/affiliate/upgrade");
  });

  it("every workspace serves the upgrade pages, behind the dashboard permission only", () => {
    const none = () => false;
    const dashboardOnly = (...keys: string[]) => keys.includes("dashboard.view");
    for (const def of Object.values(WORKSPACES)) {
      for (const path of ["upgrade", "upgrade/reseller", "upgrade/white-label"]) {
        expect(servedPaths(def).has(path)).toBe(true);
        expect(entriesFor(def, path).map((e) => e.key)).toEqual(["dashboard"]);
        expect(pageAllowed(def, path, dashboardOnly)).toBe(true);
        expect(pageAllowed(def, path, none)).toBe(false);
      }
    }
    for (const path of ["upgrade", "upgrade/reseller", "upgrade/white-label"]) expect(PAGE_PATHS).toContain(path);
  });

  it("the upgrade pages are not menu entries", () => {
    for (const def of Object.values(WORKSPACES)) {
      const paths = def.groups.flatMap((g) => g.entries.map((e) => e.path));
      expect(paths.some((p) => p.startsWith("upgrade"))).toBe(false);
    }
  });
});

describe("the upgrade screens read no stored state", () => {
  it("never touch browser storage or a stored partner type", () => {
    const dir = __dirname;
    const files = readdirSync(dir).filter((f) => /^(Upgrade.*\.tsx|upgrade\.ts)$/.test(f));
    expect(files.length).toBeGreaterThanOrEqual(4);
    for (const file of files) {
      const text = readFileSync(join(dir, file), "utf8");
      expect(text, file).not.toMatch(/localStorage|sessionStorage|document\.cookie|indexedDB/);
      expect(text, file).not.toMatch(/partner_type/);
    }
  });
});
