/**
 * `npm run dev`: `next dev`, restarted automatically if it silently stops
 * serving routes that exist.
 *
 * `next dev` learns the app's routes from a Watchpack scan of `src/app`. If
 * reading a folder fails with EPERM/EBUSY/EACCES during that scan - OneDrive
 * re-syncing the project after the PC wakes up, or an antivirus scan, holding
 * it for a moment - Watchpack treats the folder as deleted and never looks at
 * it again. Every page under it then answers "Page not found" until the
 * server restarts, and nothing is printed. (Seen 2026-09-28 on every page
 * three folders below `src/app`; reproduced by failing `fs.readdir` for those
 * folders during the first ten seconds only: `/o/x` kept working while
 * `/o/x/dashboard` stayed 404 long after. `WATCHPACK_POLLING` does not bring
 * the folders back, so a fresh server is the only cure.)
 *
 * The server rewrites the list of routes it knows into
 * `.next/dev/types/routes.d.ts` after every scan. This compares that list
 * with the `page.*` files on disk every few seconds. A route can be missing
 * briefly while a slow first scan catches up; one missing for longer than
 * that is the failure above, and the server is restarted.
 *
 * Arguments pass through to `next dev` (`npm run dev -- --port 3001`).
 * `npx next dev` runs the server without this guard.
 */

import { spawn, spawnSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { missingRoutes, pageRoutesOnDisk, parseAppRoutes } from "./route-guard.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const APP_DIR = join(ROOT, "src", "app");
const ROUTES_DTS = join(ROOT, ".next", "dev", "types", "routes.d.ts");
const NEXT_BIN = createRequire(import.meta.url).resolve("next/dist/bin/next");

const CHECK_EVERY_MS = 3_000;
const STUCK_AFTER_MS = 20_000;
const NO_ROUTE_LIST_AFTER_MS = 120_000;
const MAX_RESTARTS = 3;

let server;
let startedAt;
let missingSince;
let restarts = 0;
let restarting = false;
let stopping = false;
let guardOff = false;
let gaveUp = false;

function log(message) {
  console.error(`\n[dev] ${message}\n`);
}

function sample(routes) {
  const shown = routes.slice(0, 5).join(", ");
  return routes.length > 5 ? `${shown} and ${routes.length - 5} more` : shown;
}

function start() {
  if (stopping) process.exit(0);
  startedAt = Date.now();
  missingSince = undefined;
  restarting = false;
  server = spawn(process.execPath, [NEXT_BIN, "dev", ...process.argv.slice(2)], {
    cwd: ROOT,
    stdio: "inherit",
  });
  server.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
}

/** `next dev` serves from a child process of its own; stop both. */
function stop(child) {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

function restart(missing) {
  restarting = true;
  restarts += 1;
  // Whatever held the folders may still be busy (OneDrive can take minutes
  // after a wake-up), so later attempts wait longer. The first second is for
  // the port to be released, so the new server gets the same one.
  const delayMs = 1_000 + (restarts - 1) * 20_000;
  const shown = sample(missing);
  log(
    `The dev server lost ${missing.length} route(s) that exist on disk (${shown}) - a folder ` +
      `was locked while it scanned src/app (OneDrive sync or antivirus). ` +
      `Restarting it in ${delayMs / 1000}s (${restarts}/${MAX_RESTARTS}).`,
  );
  const old = server;
  old.removeAllListeners("exit");
  const force = setTimeout(() => old.kill("SIGKILL"), 10_000);
  old.once("exit", () => {
    clearTimeout(force);
    setTimeout(start, delayMs);
  });
  stop(old);
}

/** The routes this server knows, or undefined until it has written them. */
function knownRoutes() {
  try {
    if (statSync(ROUTES_DTS).mtimeMs < startedAt) return undefined;
    return parseAppRoutes(readFileSync(ROUTES_DTS, "utf8"));
  } catch {
    return undefined;
  }
}

function check() {
  if (stopping || restarting || guardOff) return;
  const known = knownRoutes();
  if (known === undefined || known === null) {
    if (known === null || Date.now() - startedAt > NO_ROUTE_LIST_AFTER_MS) {
      guardOff = true;
      log(`Cannot read the dev server's route list (${ROUTES_DTS}); the missing-route guard is off.`);
    }
    return;
  }
  let onDisk;
  try {
    onDisk = pageRoutesOnDisk(APP_DIR);
  } catch {
    return; // the same lock can hit this walk; look again next time
  }
  const missing = missingRoutes(onDisk, known);
  if (missing.length === 0) {
    missingSince = undefined;
    restarts = 0;
    gaveUp = false;
    return;
  }
  missingSince ??= Date.now();
  if (Date.now() - missingSince < STUCK_AFTER_MS) return;
  if (restarts < MAX_RESTARTS) {
    restart(missing);
  } else if (!gaveUp) {
    gaveUp = true;
    log(
      `Still missing after ${MAX_RESTARTS} restarts: ${sample(missing)}. Something keeps ` +
        `src/app locked - let OneDrive finish syncing, then stop this and run it again.`,
    );
  }
}

// Ctrl+C reaches `next dev` directly (same console / process group); other
// signals are passed on. Either way, exit once it has.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    stopping = true;
    if (signal !== "SIGINT" && server) stop(server);
  });
}

start();
setInterval(check, CHECK_EVERY_MS);
