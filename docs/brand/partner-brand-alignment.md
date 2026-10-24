# Partner panel brand alignment (WorkEasy360 website palette)

Status: **PASS**, 2026-10-17. Frontend only: no route, permission, API, model or migration changed.

The partner panel (and, through the shared tokens and shell, Super Admin and the
platform sign-in) now uses the palette of https://workeasy360.com. Every colour
lives in `frontend/src/app/globals.css`; components use the semantic tokens, never
a hex. `frontend/src/app/theme-tokens.test.ts` checks every pairing below in both
themes and fails if a hard-coded colour appears in the panel's stylesheets.

## Decisions

- **Primary buttons: `#02C065` fill with deep-navy `#161D24` text (7.07:1).** The
  website puts white on this green, which is 2.41:1 and fails WCAG AA at any text
  size. The fill is the website's green; only the label colour differs. Hover is
  `#04A05B` (navy on it: 5.01:1).
- **Green text is `#047A44`**, a slightly deeper shade of the brand's dark green.
  `#04884C` reads at only 4.09:1 on the mint `#EAF6EF` (current sidebar item,
  selected tab), so it is kept for lines that need 3:1: the focus ring, the
  current-page bar, the selected-tab underline and the chart bar edges.
- **Muted text is `#5E6C67`**, the same grey-green as `#6B7A75`, which falls
  just short of 4.5:1 on white (4.50) and fails on the mint backgrounds.
- **Form controls have a `#7F918A` edge** (3.32:1 on white, WCAG 1.4.11).
- **Typography stays on the platform's system font stack.** The website uses
  Plus Jakarta Sans from Google Fonts. Adopting it would add a font dependency
  to the whole platform (self-hosted through `next/font` to fit the CSP), so it
  is left as an owner decision.
- **Logo:** the official PNGs from workeasy360.com (`/Images/WorkEasy360-Logo.png`
  and `-Light.png`, 23129×4500), cropped to their artwork and scaled to 80px tall
  with their proportions kept. The mark-only file is the same artwork's circle-W,
  used below 480px so the header fits at 320px. The files are imported statically,
  so they are served from `/_next/static/media/`. The proxy never redirects that
  path, so the logo also loads on the signed-out sign-in page. A white-label
  customer's console still shows the partner's own brand.
- **Destructive confirmations are red** (`.w360-btn-danger`): Suspend and Remove
  (`ReasonAction danger`), and the Rotate and Revoke confirmations for API keys.
- **Trial status has its own colour** (info blue, `.w360-badge-info`), distinct
  from active (green) and suspended (amber).
- **Brand Settings** shows the fixed *WorkEasy360 platform brand* beside the
  editable *partner white-label brand*. The live preview (a simulated sign-in
  card) applies the partner's accent inside its own section only, through
  `applyAccent`. The panel around it stays WorkEasy360's.
- `useBranding` now derives a partner's link colour against `#EAF6EF`, the darkest
  light surface such a link sits on (the sign-in canvas). That is stricter than
  before, never looser.

## Token table

| Token | Light | Dark | Use |
|---|---|---|---|
| `--w360-cta` | `#02C065` | `#02C065` | primary button fill, brand accents |
| `--w360-cta-hover` | `#04A05B` | `#2ED583` | primary hover |
| `--w360-cta-contrast` | `#161D24` | `#0F171C` | primary button label |
| `--w360-accent` | `#02C065` | `#02C065` | decorative icons (KPI, active nav icon, empty states) |
| `--w360-brand` | `#047A44` | `#02C065` | links, current page / selected tab text, green outlines |
| `--w360-brand-hover` | `#036B3B` | `#2ED583` | |
| `--w360-brand-contrast` | `#FFFFFF` | `#0F171C` | text on a `--w360-brand` fill |
| `--w360-focus-ring` | `#04884C` | `#2ED583` | focus outline |
| `--w360-indicator` | `#04884C` | `#02C065` | current-page bar, selected-tab underline |
| `--w360-text` | `#161D24` | `#EEF3F1` | headings, KPI values, table headers |
| `--w360-text-secondary` | `#2D2E2F` | `#D5DDD9` | body text, sidebar items |
| `--w360-text-muted` | `#5E6C67` | `#A9B7B1` | labels, hints, notes |
| `--w360-bg` | `#EAF6EF` | `#0F171C` | page background (sign-in, public) |
| `--w360-bg-soft` | `#F4FAF7` | `#121A20` | console canvas, table header, hover |
| `--w360-surface` | `#FFFFFF` | `#161D24` | cards, sidebar, header |
| `--w360-surface-raised` | `#FFFFFF` | `#19222A` | menus |
| `--w360-brand-soft` | `#EAF6EF` | `#13342D` | current/selected background, secondary hover |
| `--w360-surface-mint` | `#DBEFE6` | `#124132` | success badge, illustration ring |
| `--w360-border` | `#D8E5DE` | `#2A3640` | card and table borders |
| `--w360-border-strong` | `#B5C8BE` | `#3A4853` | secondary button edge |
| `--w360-input-border` | `#7F918A` | `#62737C` | form control edge |
| `--w360-neutral-bg` | `#E8F0EC` | `#1F2A32` | neutral badge, disabled button |
| `--w360-success` / `-bg` | `#03703E` / `#DBEFE6` | `#4ADE80` / `#13372E` | active, paid, enabled |
| `--w360-info` / `-bg` | `#2549B8` / `#E3EBFD` | `#8FB0FF` / `#14264F` | trial |
| `--w360-warning` / `-bg` | `#8A5A00` / `#FDF0C8` | `#FBBF24` / `#332806` | suspended, needs setup (unchanged) |
| `--w360-danger` / `-bg` | `#B3261E` / `#FBEAEA` | `#F87171` / `#341313` | terminated, errors, destructive (unchanged) |
| `--w360-danger-hover` / `-contrast` | `#931F19` / `#FFFFFF` | `#FA9191` / `#0F171C` | destructive button |
| `--w360-chart-1` / `-1-edge` | `#02C065` / `#04884C` | `#02C065` / `#02C065` | primary series |
| `--w360-chart-2..4` | `#161D24`, `#2549B8`, `#C2410C` | `#C9D3CF`, `#8FB0FF`, `#FB923C` | further series |

Primitives (`--w360-green-500..900`, `--w360-ink-*`, `--w360-mint-*`,
`--w360-line-200`) hold the website's values; only the semantic tokens above are
used by components.

## Verification (2026-10-17)

- Frontend: typecheck, eslint, vitest (44 files, 903 tests, including the new
  theme test), `next build`, `assert-dynamic-rendering.mjs` all pass. Production
  `npm audit` is clean. The full audit shows only the accepted dev-only
  GHSA-vfj7-8cjw-p6xm.
- Real browser (production build, scratch DB `w360_ppanel_fresh`): the 20 pages
  named in the brief. axe WCAG 2.1 A/AA showed 0 violations in light and in dark,
  with 0 CSP violations and one `h1` per page. Reflow at 1440/1024/768/390/320 px:
  100 checks, no horizontal scroll. Computed styles were checked for the sidebar,
  header, buttons (rest, hover, disabled, danger), table, badge, tab and input
  focus, in both themes. The sign-in page logo loads signed-out, with the light
  variant in dark mode.
- Four existing XSS tests asserted "no `<img>` anywhere in the shell". They now
  exclude only the platform logo (`img[data-w360-logo]`), so an image drawn from
  hostile text still fails them.

## Whole-platform alignment (2026-10-25)

The owner asked for the website's colour combination across the whole software.

- **Typeface: Plus Jakarta Sans everywhere**, as on the website (this settles the
  open font decision above). `app/layout.tsx` loads it through `next/font/google`,
  which downloads it at build time and serves it from `/_next/static/media`, so
  the CSP's `font-src 'self'` is unchanged and no visitor contacts Google. The
  system stack stays as the fallback. `next build` (and so the Docker image and
  CI) needs internet access to fetch the font.
- **New section tokens**, from the website's bands:

  | Token | Light | Dark | Use |
  |---|---|---|---|
  | `--w360-band` | `#047A44` | `#0E3B28` | deep-green band and footer (the website's `#008F49` holds white at only 4.2:1) |
  | `--w360-band-text` / `-muted` | `#FFFFFF` / `#D9F0E3` | `#EEF3F1` / `#BCD3C8` | text on the band, also its focus ring |
  | `--w360-sun` / `-hover` | `#FCC63C` / `#F5B70F` | `#FCC63C` / `#FDD56B` | yellow button and step numbers, navy text |
  | `--w360-sun-contrast` | `#161D24` | `#0F171C` | |
  | `--w360-cream` | `#FFF8E8` | `#1E1B13` | warm section background |

- **Public site** (`features/site`), the counterpart of the marketing website:
  the official logo in the header, a flat mint hero with a pill eyebrow and
  the key word in green, then a white products band, a cream "How it works"
  band with yellow step numbers, a deep-green call to action with a yellow
  button, and a deep-green footer with the light logo (`<Logo onBand />`).
- Links drawn as buttons (`.w360-btn`) are no longer underlined.
- Consoles, sign-in and partner portal already used the palette; they change
  only by the typeface.
- Kept: primary buttons still carry navy text on the green (white is 2.41:1).
- `theme-tokens.test.ts` checks the new pairings in both themes, the font
  wiring, and that `Site.module.css` has no hard-coded colour (the six product
  monogram accents excepted).

## Remaining

- The organization console uses the same shell (`SidebarShell`), so it shows the
  logo too when the organization has no white-label partner. It and the customer
  launcher share the tokens, so their colours moved with this change. Neither
  layout was redesigned, and neither was in this review's page list.
- The six white-label preset swatches (`Account.tsx` presets) are partner data
  and keep their own values.
