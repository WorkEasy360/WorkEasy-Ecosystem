// Summarize one audit report, or compare two (before -> after).
//   node scripts/partner_ui_check/summarize.js <dir>/before/report.json [<dir>/after/report.json]

const fs = require("node:fs");

function stats(file) {
  const r = JSON.parse(fs.readFileSync(file, "utf8"));
  const s = {
    pages: r.pages.length,
    failures: r.failures,
    consoleErrors: r.consoleErrors.length,
    notOneH1: [],
    h1Tops: {},
    h1Lefts: {},
    h1Sizes: {},
    h2Sizes: {},
    buttonHeights: {},
    inputHeights: {},
    fontSizes: {},
    overflow: {},
    offscreen: {},
    clipped: [],
    axe: {},
    axePages: 0,
    smallTargets: 0,
  };
  const bump = (o, k) => (o[k] = (o[k] || 0) + 1);
  for (const p of r.pages) {
    const d = p.widths[1440];
    if (!d) continue;
    const where = `${p.persona} ${p.finalPath}`;
    if (d.h1.length !== 1) s.notOneH1.push(`${where} ${JSON.stringify(d.h1)}`);
    bump(s.h1Tops, d.h1Top);
    bump(s.h1Lefts, d.h1Left);
    bump(s.h1Sizes, d.h1Size);
    for (const h of d.h2Sizes) bump(s.h2Sizes, h);
    for (const h of d.buttonHeights) bump(s.buttonHeights, h);
    for (const h of d.inputHeights) bump(s.inputHeights, h);
    for (const f of d.fontSizes) bump(s.fontSizes, f);
    if (d.clipped.length) s.clipped.push(`${where}: ${d.clipped.join(" | ")}`);
    s.smallTargets += d.smallTargets;
    if (p.axe.length) s.axePages++;
    for (const v of p.axe) bump(s.axe, `${v.split(" ")[0]}`);
    for (const [w, m] of Object.entries(p.widths)) {
      if (m.overflowX) (s.overflow[w] = s.overflow[w] || []).push(where);
      if (m.offscreen.length) (s.offscreen[w] = s.offscreen[w] || []).push(`${where}: ${m.offscreen.join(", ")}`);
    }
  }
  return s;
}

const sortKeys = (o) => Object.fromEntries(Object.entries(o).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0])));
function print(label, s) {
  console.log(`\n===== ${label}: ${s.pages} pages, ${s.failures.length} failures, ${s.consoleErrors} console errors`);
  console.log("pages without exactly one h1:", s.notOneH1.length, s.notOneH1.slice(0, 8));
  console.log("h1 top (px -> pages):", sortKeys(s.h1Tops));
  console.log("h1 left (px -> pages):", sortKeys(s.h1Lefts));
  console.log("h1 sizes:", s.h1Sizes, " h2 sizes:", sortKeys(s.h2Sizes));
  console.log("button heights (px -> pages using it):", sortKeys(s.buttonHeights));
  console.log("input heights:", sortKeys(s.inputHeights));
  console.log("font sizes (distinct):", Object.keys(s.fontSizes).length, sortKeys(s.fontSizes));
  console.log("page-level sideways overflow by width:", Object.fromEntries(Object.entries(s.overflow).map(([w, l]) => [w, l.length])));
  for (const [w, l] of Object.entries(s.overflow)) console.log(`  ${w}: ${l.slice(0, 6).join(" ; ")}`);
  console.log("pushed off screen by width:", Object.fromEntries(Object.entries(s.offscreen).map(([w, l]) => [w, l.length])));
  for (const [w, l] of Object.entries(s.offscreen)) console.log(`  ${w}: ${l.slice(0, 4).join(" ; ")}`);
  console.log("clipped text:", s.clipped.length, s.clipped.slice(0, 5));
  console.log("small touch targets (<24px):", s.smallTargets);
  console.log("axe: pages with violations", s.axePages, s.axe);
  if (s.failures.length) console.log("failures:", s.failures.slice(0, 10));
}

const [a, b] = process.argv.slice(2);
print("A", stats(a));
if (b) print("B", stats(b));
