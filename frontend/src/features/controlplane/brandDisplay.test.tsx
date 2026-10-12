import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { BrandFooter, DashboardWelcome, SignInSupport, SignInWelcome, SupportDetails, WebsiteLink, type BrandSource } from "./brandDisplay";
import { httpsHref, mailtoHref, telHref } from "./brandLinks";
import { normalizePortal } from "./useBranding";
import { expectNoViolations } from "./testSupport";

/**
 * A partner's words and support details as a customer reads them. What matters
 * here is that they are only ever text and three kinds of safe link, and that the
 * two sign-in choices are respected.
 */

const brand = (over: Partial<BrandSource> = {}, portal: Partial<BrandSource["portal"]> = {}): BrandSource => ({
  support_email: "help@acme.example",
  support_url: "https://help.acme.example/contact",
  portal: {
    short_name: "",
    support_name: "Sam from Acme",
    support_phone: "+44 20 7946 0000",
    website_url: "https://acme.example",
    welcome_message: "Welcome to Acme",
    footer_text: "Acme Ltd, 1 High Street",
    login_show_welcome: true,
    login_show_support: true,
    ...portal,
  },
  ...over,
});

const EVIL = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>`;

describe("the links a brand can become", () => {
  it("builds a mailto link only from a plain address", () => {
    expect(mailtoHref("help@acme.example")).toBe("mailto:help@acme.example");
    expect(mailtoHref("  help@acme.example ")).toBe("mailto:help@acme.example");
    for (const bad of ["", "nope", "a@b", "a b@c.example", "a@c.example?subject=x", "a@c.example,b@d.example", "javascript:alert(1)@x.example", "<a@c.example>", "a@c.example\nBcc: x@y.example"]) {
      expect(mailtoHref(bad), bad).toBeNull();
    }
  });

  it("builds a tel link from digits only", () => {
    expect(telHref("+44 20 7946 0000")).toBe("tel:+442079460000");
    expect(telHref("020 7946-0000")).toBe("tel:02079460000");
    for (const bad of ["", "12", "abc", "(020) 7946 0000", "+44 20 7946 0000;ext=1", "javascript:1", "1234 <b>"]) expect(telHref(bad), bad).toBeNull();
  });

  it("builds an https link only from a plain https address", () => {
    expect(httpsHref("https://acme.example/help")).toBe("https://acme.example/help");
    for (const bad of ["", "http://acme.example", "javascript:alert(1)", "data:text/html,<script>1</script>", "https://acme", "https://u:p@acme.example", "https://a b.example", "//acme.example"]) {
      expect(httpsHref(bad), bad).toBeNull();
    }
  });
});

describe("normalizePortal", () => {
  it("fills a missing or malformed portal with empty words and both sign-in choices on", () => {
    for (const raw of [undefined, null, {}, { welcome_message: 5, login_show_welcome: "yes" }] as never[]) {
      const portal = normalizePortal(raw);
      expect(portal.welcome_message).toBe("");
      expect(portal.short_name).toBe("");
      expect(portal.login_show_welcome).toBe(true);
      expect(portal.login_show_support).toBe(true);
    }
  });

  it("keeps what is there, and an explicit off", () => {
    const portal = normalizePortal({ short_name: "A", welcome_message: "Hi", login_show_welcome: false, login_show_support: false });
    expect(portal).toMatchObject({ short_name: "A", welcome_message: "Hi", login_show_welcome: false, login_show_support: false });
  });
});

describe("SignInWelcome", () => {
  it("shows the welcome message as text when the partner chose to", () => {
    render(<SignInWelcome brand={brand()} />);
    expect(screen.getByText("Welcome to Acme")).toBeInTheDocument();
  });

  it("shows nothing when the choice is off, when there is no message, or when there is no brand", () => {
    const { container, rerender } = render(<SignInWelcome brand={brand({}, { login_show_welcome: false })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SignInWelcome brand={brand({}, { welcome_message: "   " })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SignInWelcome brand={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws hostile text as text", () => {
    const { container } = render(<SignInWelcome brand={brand({}, { welcome_message: EVIL })} />);
    expect(container.querySelector("img, script, [onerror]")).toBeNull();
    expect(container.textContent).toBe(EVIL);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});

describe("SupportDetails", () => {
  it("shows the name, a mailto link, a tel link and the support page, safely", async () => {
    const { container } = render(<SupportDetails brand={brand()} />);
    expect(screen.getByText("Need help?")).toBeInTheDocument();
    expect(screen.getByText("Sam from Acme")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "help@acme.example" })).toHaveAttribute("href", "mailto:help@acme.example");
    expect(screen.getByRole("link", { name: "+44 20 7946 0000" })).toHaveAttribute("href", "tel:+442079460000");
    const page = screen.getByRole("link", { name: "Support page" });
    expect(page).toHaveAttribute("href", "https://help.acme.example/contact");
    expect(page).toHaveAttribute("rel", "noopener noreferrer");
    await expectNoViolations(container);
  });

  it("shows only what is set", () => {
    render(<SupportDetails brand={brand({ support_url: "" }, { support_name: "", support_phone: "" })} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "help@acme.example" })).toBeInTheDocument();
  });

  it("shows nothing at all when nothing is set", () => {
    const { container } = render(<SupportDetails brand={brand({ support_email: "", support_url: "" }, { support_name: "", support_phone: "" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("never turns an unsafe value into a link: a bad address is text, a bad link is left out", () => {
    const { container } = render(
      <SupportDetails brand={brand({ support_email: "x@y.example?subject=<b>", support_url: "javascript:alert(1)" }, { support_phone: "call me" })} />,
    );
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.textContent).toContain("x@y.example?subject=<b>"); // shown as text
    expect(container.textContent).not.toContain("javascript:"); // not shown at all
  });

  it("draws hostile text as text", () => {
    const { container } = render(<SupportDetails brand={brand({}, { support_name: EVIL })} />);
    expect(container.querySelector("img, script, [onerror]")).toBeNull();
    expect(container.textContent).toContain("<img");
  });
});

describe("SignInSupport", () => {
  it("shows the support details and the website when the choice is on", () => {
    render(<SignInSupport brand={brand()} />);
    expect(screen.getByText("Need help?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("href", "https://acme.example/");
    expect(screen.getByRole("link", { name: "Visit our website" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("hides the support details but keeps the website when the choice is off", () => {
    render(<SignInSupport brand={brand({}, { login_show_support: false })} />);
    expect(screen.queryByText("Need help?")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "help@acme.example" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Visit our website" })).toBeInTheDocument();
  });

  it("shows nothing when both are absent, and no brand shows nothing", () => {
    const { container, rerender } = render(<SignInSupport brand={brand({}, { login_show_support: false, website_url: "" })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SignInSupport brand={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("WebsiteLink", () => {
  it("is a link only for a plain https address, otherwise plain text", () => {
    const { rerender } = render(<WebsiteLink brand={brand()} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "https://acme.example/");
    rerender(<WebsiteLink brand={brand({}, { website_url: "javascript:alert(1)" })} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("DashboardWelcome and BrandFooter", () => {
  it("greets the customer with the welcome message, whatever the sign-in choice", () => {
    render(<DashboardWelcome brand={brand({}, { login_show_welcome: false })} />);
    expect(screen.getByText("Welcome to Acme")).toBeInTheDocument();
  });

  it("shows nothing for an empty welcome or no brand", () => {
    const { container, rerender } = render(<DashboardWelcome brand={brand({}, { welcome_message: "" })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<DashboardWelcome brand={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws the footer text, the support details and the website", async () => {
    const { container } = render(<BrandFooter brand={brand()} />);
    const footer = container.querySelector("footer")!;
    expect(within(footer).getByText("Acme Ltd, 1 High Street")).toBeInTheDocument();
    expect(within(footer).getByRole("link", { name: "help@acme.example" })).toBeInTheDocument();
    expect(within(footer).getByRole("link", { name: "Visit our website" })).toBeInTheDocument();
    await expectNoViolations(container);
  });

  it("is drawn for a partner that set only a footer, and not at all for one that set nothing", () => {
    const { container, rerender } = render(<BrandFooter brand={brand({ support_email: "", support_url: "" }, { support_name: "", support_phone: "", website_url: "" })} />);
    expect(screen.getByText("Acme Ltd, 1 High Street")).toBeInTheDocument();
    rerender(<BrandFooter brand={brand({ support_email: "", support_url: "" }, { support_name: "", support_phone: "", website_url: "", footer_text: "" })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<BrandFooter brand={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws a hostile footer as text", () => {
    const { container } = render(<BrandFooter brand={brand({}, { footer_text: EVIL })} />);
    expect(container.querySelector("img, script, [onerror]")).toBeNull();
    expect(container.textContent).toContain("<img");
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
});
