# 11 — Accessibility

## Built in

- **Skip link** (`layout.tsx`) to `#w360-main`, present on every route.
- **Semantic structure**: `<nav aria-label="Platform">`, `<main>`,
  `<table>`/`<caption>`/`<th scope="col">` for members and seats,
  `<dl>`/`<dt>`/`<dd>` for profile/organization display data, real
  `<button>`/`<form>` elements rather than clickable `<div>`s throughout.
- **Labels**: every form input has an associated `<label htmlFor>`
  (`useId()`-generated, collision-safe); icon-only controls (the org
  switcher, the user-menu button) carry `aria-label`.
- **Focus**: one consistent `:focus-visible` treatment
  (`globals.css`), never suppressed anywhere in this codebase.
- **State is never color/icon alone** (Task 8/28): every `AppAccessState`
  renders as a text badge (`AppCard.tsx`'s `STATE_COPY`), not a colored dot;
  seat-held/unheld is a text label ("✓ Assigned" / "— Assign") with
  `aria-pressed`, not a bare checkbox glyph.
- **Live regions**: loading states use `aria-live="polite"`; errors use
  `role="alert"` (`StatusScreen`, the launch-error banner, form errors).
- **Menus**: the user menu uses `aria-haspopup="menu"` /
  `aria-expanded` / `role="menu"` / `role="menuitem"`.
- **Contrast**: the design-token palette (`globals.css`) targets WCAG AA
  contrast for text-on-background and badge combinations by construction
  (dark, saturated text on light tinted backgrounds and vice versa in dark
  mode) - not independently measured against a contrast-checker tool (see
  below).

## Verified — automated (Phase 4 audit)

Two tools, run against the real production build, and what each one can
actually see:

**axe-core 4.13 in jsdom** (`frontend/src/accessibility.test.tsx`, part of
`npm test`, 9 assertions): WCAG 2.1 A + AA rules over the rendered
accessibility tree for the app card in all five `AppAccessState` values, the
sign-in form, the seat-assignment table, and the loading and error status
screens. **0 violations.**

**Lighthouse 12.8.2** against `next start` on the production build,
`/login`, Chrome headless, mobile form factor, simulated throttling:
**accessibility 100**.

**What neither tool checked, and why.** jsdom computes no styles and paints
nothing, so axe cannot evaluate colour contrast, focus visibility, reflow at
320px or motion; Lighthouse's automated accessibility category covers a
subset of WCAG and explicitly does not substitute for manual review. The
contrast claim above therefore remains "by construction, not measured", and
**no manual screen-reader pass** (VoiceOver/NVDA/JAWS) has been performed.
That is now the single remaining accessibility gap rather than the whole of
it.

**What the automated pass found.** No axe violations - but writing it
surfaced a real defect the tool could not have: `AppCard` rendered
`{app.description || copy.hint}`, so the "ask an administrator for a seat"
and "contact your account team to activate" hints appeared **only** for a
product whose catalog entry had no description. Every real product has one,
so in practice a person looking at a card they could not open was never told
what to do about it. The hint is now its own line. The two assertions under
"accessibility properties axe cannot see" in that test file exist for
exactly this class of problem: a perfectly labelled control that says
nothing useful passes every automated rule.

## Responsive (Task 27)

CSS Grid with `repeat(auto-fill, minmax(240px, 1fr))` for the app grid - no
JavaScript-driven breakpoint logic, no responsive framework. The shell
header wraps its navigation below the brand/org-switcher row under 720px
(`Shell.module.css`). Verified only by resizing a desktop browser viewport
during development, not on physical mobile/tablet devices.
