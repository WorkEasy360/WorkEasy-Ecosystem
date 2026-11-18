// The dependency-vulnerability gate (CI "Frontend" job and the daily
// "Dependency watch" workflow).
//
//   npm audit --json > audit.json; node scripts/audit-gate.mjs audit.json
//   node scripts/audit-gate.mjs --time-limited    # prints the IDs that expire
//
// The full audit, dev dependencies included, at moderate and above. Any
// advisory not accepted below fails the build. An acceptance names the
// advisory by ID, why it cannot reach production, who owns it, and (for every
// acceptance from 2026-11-18 on) the date it must be reviewed again: after
// that date the gate fails until someone looks again. Remove an ID once
// upstream ships a fix; the daily workflow fails when one appears.
import { readFileSync } from "node:fs";

export const ACCEPTED = new Map([
  [
    "GHSA-vfj7-8cjw-p6xm",
    {
      // braces <= 3.0.3: no fixed version. eslint-config-next -> fast-glob ->
      // micromatch (lint only). docs/audit-2026-10, owner decision on PR #1.
      owner: "platform owner",
      reviewBy: null,
    },
  ],
  [
    "GHSA-g3hv-75wg-m249",
    {
      // minimatch >=7.0.0 <11.0.1 (ReDoS-style DoS via globparts):
      // eslint-config-next -> typescript-eslint ->
      // @typescript-eslint/typescript-estree -> minimatch 10.x. Even the latest
      // typescript-estree (8.72.1) requires minimatch ^10, so there is no
      // in-range fix (owner decision 2026-11-05).
      owner: "platform owner",
      reviewBy: null,
    },
  ],
  [
    "GHSA-fc5q-39xc-vjpf",
    {
      // picomatch <= 4.0.7 (high: ReDoS through single-branch repeated
      // extglobs). Accepted 2026-11-18 by the owner's conditional approval,
      // every condition checked first (docs/security/picomatch-GHSA-fc5q.md):
      // - installed: picomatch 4.0.7 (vite 8.3.5, vitest 4.1.12 and its
      //   tinyglobby/fdir) and 2.3.2 (eslint-config-next 16.4.6 ->
      //   @next/eslint-plugin-next -> fast-glob -> micromatch): the test
      //   runner, the dev server and lint only;
      // - production: `npm ls picomatch --omit=dev` is empty, `npm audit
      //   --omit=dev` is clean, and the image runs `npm prune --omit=dev`;
      //   Next's own bundled copy (next/dist/compiled/picomatch) runs at
      //   request time only to match images.localPatterns/remotePatterns,
      //   which this app does not set (localPatterns undefined: never called;
      //   remotePatterns []: never called);
      // - input: the vulnerable regexes come from the glob PATTERN; every
      //   pattern here is written by a developer in tooling configuration,
      //   none comes from a request;
      // - no fix: 4.0.7 is the latest release and the advisory names no
      //   patched version (checked 2026-11-18).
      owner: "platform owner",
      reviewBy: "2026-12-18",
    },
  ],
]);

const LEVELS = ["info", "low", "moderate", "high", "critical"];

function expired(reviewBy, now) {
  return reviewBy !== null && now > new Date(`${reviewBy}T23:59:59Z`);
}

export function judge(report, now = new Date()) {
  if (report.error || !report.vulnerabilities) {
    return { failed: true, lines: [JSON.stringify(report.error ?? report, null, 2)] };
  }
  // Advisories are the object entries in `via`; string entries only point at
  // another vulnerable package.
  const advisories = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities)) {
    for (const via of vulnerability.via) {
      if (typeof via === "object") advisories.set(via.url, via);
    }
  }
  let failed = false;
  const lines = [];
  for (const [url, advisory] of advisories) {
    if (LEVELS.indexOf(advisory.severity) < LEVELS.indexOf("moderate")) continue;
    const id = url.split("/").pop();
    const line = `${id} ${advisory.name} ${advisory.range} (${advisory.severity}): ${advisory.title}`;
    const accepted = ACCEPTED.get(id);
    if (accepted && expired(accepted.reviewBy, now)) {
      lines.push(`acceptance expired on ${accepted.reviewBy} (owner: ${accepted.owner}), review it: ${line}`);
      failed = true;
    } else if (accepted) {
      const until = accepted.reviewBy ? `, review by ${accepted.reviewBy}` : "";
      lines.push(`accepted, still open upstream (owner: ${accepted.owner}${until}): ${line}`);
    } else {
      lines.push(line);
      failed = true;
    }
  }
  if (!failed) lines.push("No unaccepted advisories at moderate or above.");
  return { failed, lines };
}

if (process.argv[1]?.endsWith("audit-gate.mjs")) {
  if (process.argv[2] === "--time-limited") {
    for (const [id, { reviewBy }] of ACCEPTED) if (reviewBy) console.log(id);
  } else {
    const { failed, lines } = judge(JSON.parse(readFileSync(process.argv[2], "utf8")));
    for (const line of lines) console.log(line);
    if (failed) process.exit(1);
  }
}
