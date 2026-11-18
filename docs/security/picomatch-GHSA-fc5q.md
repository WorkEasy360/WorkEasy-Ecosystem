# GHSA-fc5q-39xc-vjpf (picomatch): a time-limited exception

Written for: whoever reviews the frontend dependency gate.

| | |
|---|---|
| Advisory | [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), picomatch `<= 4.0.7`, high: ReDoS through single-branch repeated extglobs |
| Fixed version | none. 4.0.7 is the latest release, and the advisory names no patched version (checked 2026-11-18) |
| Decision | accepted by ID in `frontend/scripts/audit-gate.mjs`, under the owner's conditional approval of 2026-11-18 |
| Owner | platform owner |
| Review by | **2026-12-18**. After this date the CI gate fails again until someone reviews it |
| Removal | the daily `Dependency watch` workflow fails as soon as the advisory names a fixed version. Then: upgrade, run the gate, delete the ID |

## Conditions checked before accepting (2026-11-18, `frontend/package-lock.json` at Next.js 16.4.6)

1. **Installed versions.** picomatch comes in twice:
   - **4.0.7** via `vite` 8.3.5 (from `@vitejs/plugin-react`), `vitest` 4.1.12 and its `tinyglobby` 0.2.17 / `fdir` 6.5.0;
   - **2.3.2** via `eslint-config-next` 16.4.6 → `@next/eslint-plugin-next` → `fast-glob` 3.3.1 → `micromatch` 4.0.8.
2. **Development only.** Every path is the test runner, the dev server or lint. `npm ls picomatch --omit=dev` is empty, and `npm audit --omit=dev` reports 0 vulnerabilities.
3. **Absent from the production runtime.** `deploy/docker/frontend.Dockerfile` runs `npm prune --omit=dev` before the runtime stage copies `node_modules`.
   - Next.js carries its own compiled copy, `next/dist/compiled/picomatch`. At request time it is called only by `next/dist/shared/lib/match-local-pattern.js` and `match-remote-pattern.js`, to match `images.localPatterns` and `images.remotePatterns`.
   - `frontend/next.config.ts` sets neither. With `localPatterns` undefined, Next.js allows local images without calling picomatch. With `remotePatterns` set to `[]`, there is nothing to match.
4. **No untrusted input.** The slow regular expressions come from the glob **pattern**. Every pattern on these paths is written by a developer in tooling configuration (Vite, Vitest, ESLint) or in `next.config.ts`; none comes from a request.
5. **No alternative.** No fixed picomatch exists. Removing it would mean removing Vite, Vitest and the Next.js lint rules.

All other advisories still fail the gate, and nothing else about the gate changed: the full audit (dev dependencies included, moderate and above) runs on every pull request.
