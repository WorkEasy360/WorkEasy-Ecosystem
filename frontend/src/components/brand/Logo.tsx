import type { StaticImageData } from "next/image";
import onLight from "./workeasy360-logo.png";
import onDark from "./workeasy360-logo-light.png";
import markImage from "./workeasy360-mark.png";
import styles from "./Logo.module.css";

/** Next hands a static import over as `StaticImageData`; the test runner (Vite)
 * as a plain URL. Either way the `src` is a same-origin `/_next/static` file,
 * which the proxy never redirects and the CSP's `img-src 'self'` allows. */
const srcOf = (image: StaticImageData | string) => (typeof image === "string" ? image : image.src);

/**
 * The official WorkEasy360 logo (from workeasy360.com, scaled down with its
 * proportions kept). The dark wordmark is drawn on light themes and the light
 * one on dark themes; `compact` swaps in the mark alone on narrow screens so
 * the header still fits at 320px. The images are decorative: the name is in
 * text, for assistive technology and for search. `data-w360-logo` marks them as
 * the platform's own static files (the XSS tests look for any other image).
 *
 * Workeasy360's own surfaces only - Super Admin, the partner portal and the
 * platform sign-in. A white-label customer's console shows that partner's brand.
 * `onBand` always draws the light wordmark, for the deep-green band (the public
 * site's footer), which is dark in both themes.
 */
export function Logo({
  size = "md",
  compact = false,
  onBand = false,
}: {
  size?: "md" | "lg";
  compact?: boolean;
  onBand?: boolean;
}) {
  return (
    <span className={styles.logo} data-size={size} data-compact={compact || undefined} data-on-band={onBand || undefined}>
      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin static file; next/image is unused elsewhere in this app. */}
      <img className={`${styles.full} ${styles.onLight}`} src={srcOf(onLight)} alt="" data-w360-logo="" width={499} height={80} />
      {/* eslint-disable-next-line @next/next/no-img-element -- as above */}
      <img className={`${styles.full} ${styles.onDark}`} src={srcOf(onDark)} alt="" data-w360-logo="" width={499} height={80} />
      {compact && (
        // eslint-disable-next-line @next/next/no-img-element -- as above
        <img className={styles.mark} src={srcOf(markImage)} alt="" data-w360-logo="" width={90} height={80} />
      )}
      <span className="w360-visually-hidden">Workeasy360</span>
    </span>
  );
}
