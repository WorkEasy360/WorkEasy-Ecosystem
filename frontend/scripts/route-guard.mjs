/**
 * Helpers for `scripts/dev.mjs`: which app routes exist on disk, and which of
 * them the running `next dev` server says it knows about.
 */

import { readdirSync } from "node:fs";
import { join } from "node:path";

const PAGE_FILE = /^page\.(tsx|ts|jsx|js)$/;

/**
 * The URL path that a `page.*` file in the folder `segments` (relative to
 * `src/app`) serves, spelled the way Next.js writes it in `AppRoutes`, or
 * null when this should not second-guess Next.js about it: private folders
 * (`_x`), parallel-route slots (`@x`) and intercepting routes (`(.)x`) are
 * left out rather than modelled. Route groups (`(x)`) add no URL segment.
 */
export function routeForSegments(segments) {
  const kept = [];
  for (const segment of segments) {
    if (segment.startsWith("_") || segment.startsWith("@") || segment.startsWith("(.")) {
      return null;
    }
    if (segment.startsWith("(") && segment.endsWith(")")) continue;
    kept.push(segment);
  }
  return `/${kept.join("/")}`;
}

/** Every page route under `appDir`. Throws if a folder cannot be read. */
export function pageRoutesOnDisk(appDir) {
  const routes = new Set();
  const walk = (dir, segments) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), [...segments, entry.name]);
      } else if (PAGE_FILE.test(entry.name)) {
        const route = routeForSegments(segments);
        if (route !== null) routes.add(route);
      }
    }
  };
  walk(appDir, []);
  return routes;
}

/**
 * The members of the `AppRoutes` union in a `routes.d.ts` that `next dev`
 * generated, or null when the file does not have the shape this expects - a
 * Next.js upgrade may change it, and the caller then stands down rather than
 * acting on a misread.
 */
export function parseAppRoutes(routesDts) {
  const line = routesDts.split(/\r?\n/).find((l) => l.startsWith("type AppRoutes = "));
  if (line === undefined) return null;
  return new Set([...line.matchAll(/"([^"]*)"/g)].map((match) => match[1]));
}

/** Routes on disk that the server does not know, sorted. */
export function missingRoutes(onDisk, known) {
  return [...onDisk].filter((route) => !known.has(route)).sort();
}
