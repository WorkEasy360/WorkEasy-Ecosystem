import type { PublicPortalFields } from "@/lib/api/controlPlane";
import { httpsHref, mailtoHref, telHref } from "./brandLinks";
import css from "./BrandDisplay.module.css";

/**
 * A partner's brand as a customer reads it (sign-in page, custom-domain landing
 * page, the portal's header, dashboard and footer).
 *
 * Everything is drawn as a React text node: there is no markup to inject. A
 * link is only ever ``mailto:``, ``tel:`` or ``https`` and is built from a value
 * that was checked first (``brandLinks``); an https link carries
 * ``rel="noopener noreferrer"`` and a value that fails its check is shown as text
 * or left out. Nothing here is a form or a field, so it adds no way to enter a
 * credential on a partner's custom domain (ADR-039).
 */

/** What both the host brand and the organization's own brand carry. */
export interface BrandSource {
  support_email: string;
  support_url: string;
  portal: PublicPortalFields;
}

/** The welcome message shown on the sign-in page, when the partner chose to show it. */
export function SignInWelcome({ brand }: { brand: BrandSource | null }) {
  const message = brand?.portal.welcome_message.trim();
  if (!brand || !brand.portal.login_show_welcome || !message) return null;
  return <p className={css.welcome}>{message}</p>;
}

/** Support name, email, phone and page: one line of plain text and safe links. */
export function SupportDetails({ brand, centered = false }: { brand: BrandSource; centered?: boolean }) {
  const { portal } = brand;
  const name = portal.support_name.trim();
  const email = brand.support_email.trim();
  const phone = portal.support_phone.trim();
  const page = httpsHref(brand.support_url);
  const emailHref = mailtoHref(email);
  const phoneHref = telHref(phone);

  const items: { key: string; node: React.ReactNode }[] = [];
  if (name) items.push({ key: "name", node: name });
  if (email) items.push({ key: "email", node: emailHref ? <a href={emailHref}>{email}</a> : email });
  if (phone) items.push({ key: "phone", node: phoneHref ? <a href={phoneHref}>{phone}</a> : phone });
  if (page) {
    items.push({
      key: "page",
      node: (
        <a href={page} rel="noopener noreferrer">
          Support page
        </a>
      ),
    });
  }
  if (items.length === 0) return null;
  return (
    <div className={`${css.support} ${centered ? css.supportCentered : ""}`}>
      <span className={css.supportHeading}>Need help?</span>
      <ul className={css.supportList}>
        {items.map((item) => (
          <li key={item.key}>{item.node}</li>
        ))}
      </ul>
    </div>
  );
}

/** The partner's company website, as a link only when it is a plain https address. */
export function WebsiteLink({ brand }: { brand: BrandSource }) {
  const url = brand.portal.website_url.trim();
  if (!url) return null;
  const href = httpsHref(url);
  return (
    <p className={css.website}>
      {href ? (
        <a href={href} rel="noopener noreferrer">
          Visit our website
        </a>
      ) : (
        url
      )}
    </p>
  );
}

/** Support details on the sign-in page, when the partner chose to show them, and the website. */
export function SignInSupport({ brand, centered = false }: { brand: BrandSource | null; centered?: boolean }) {
  if (!brand) return null;
  const support = brand.portal.login_show_support ? <SupportDetails brand={brand} centered={centered} /> : null;
  const website = brand.portal.website_url.trim() ? <WebsiteLink brand={brand} /> : null;
  if (!support && !website) return null;
  return (
    <div className={css.signInSupport}>
      {support}
      {website}
    </div>
  );
}

/** The welcome message at the top of a customer's dashboard. */
export function DashboardWelcome({ brand }: { brand: BrandSource | null }) {
  const message = brand?.portal.welcome_message.trim();
  if (!message) return null;
  return <p className={css.welcomeBanner}>{message}</p>;
}

/** The portal's footer: the partner's footer text, support details and website. */
export function BrandFooter({ brand }: { brand: BrandSource | null }) {
  if (!brand) return null;
  const text = brand.portal.footer_text.trim();
  const hasSupport = Boolean(
    brand.portal.support_name.trim() || brand.support_email.trim() || brand.portal.support_phone.trim() || httpsHref(brand.support_url),
  );
  const hasWebsite = Boolean(brand.portal.website_url.trim());
  if (!text && !hasSupport && !hasWebsite) return null;
  return (
    <footer className={css.footer}>
      {text && <p>{text}</p>}
      {hasSupport && <SupportDetails brand={brand} />}
      {hasWebsite && <WebsiteLink brand={brand} />}
    </footer>
  );
}
