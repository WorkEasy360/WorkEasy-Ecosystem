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

## Not verified — a real gap, stated plainly

**No automated accessibility audit was run** (axe-core, Lighthouse
accessibility score, or equivalent) and **no manual screen-reader pass**
(VoiceOver/NVDA/JAWS) was performed. Everything above is correct by
inspection against WCAG 2.1 AA criteria and by following each pattern's
documented ARIA usage, not verified by a tool or a human using assistive
technology. This is the single largest gap in this phase's accessibility
work and should be the first thing a follow-up pass does before this ships
to real users - see
[phase-4-status.md](phase-4-status.md) §12.

## Responsive (Task 27)

CSS Grid with `repeat(auto-fill, minmax(240px, 1fr))` for the app grid - no
JavaScript-driven breakpoint logic, no responsive framework. The shell
header wraps its navigation below the brand/org-switcher row under 720px
(`Shell.module.css`). Verified only by resizing a desktop browser viewport
during development, not on physical mobile/tablet devices.
