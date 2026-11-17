import type { ReactNode } from "react";

/**
 * A deliberately tiny, safe subset of Markdown for text the catalog team
 * writes (plan and bundle descriptions):
 *
 * - blank-line separated paragraphs;
 * - lines starting with `- ` as a bulleted list, `1. ` as a numbered one;
 * - `## ` and `### ` lines as headings (2026-11-16, the Product Publisher's
 *   description) - drawn one level below the page's own section heading
 *   (`headingLevel`, h3 by default), so a description never adds an h1 or h2;
 * - `**bold**`, `*italic*`;
 * - `[text](https://...)` links - HTTPS only, `rel="noopener noreferrer"`.
 *
 * Everything else is plain text. It builds React elements - never an HTML
 * string - so markup in the source (`<img onerror=...>`, `<script>`) is shown
 * as the characters it is and cannot run (nothing is ever rendered as raw HTML).
 */

const MAX_SOURCE = 10_000;
const INLINE = /\*\*([^*\n]+?)\*\*|\*([^*\n]+?)\*|\[([^\]\n]+)\]\(([^()\s]+)\)/g;

function safeHref(raw: string): string | null {
  if (!/^https:\/\//i.test(raw)) return null;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function inline(text: string, depth = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = new RegExp(INLINE.source, "g");
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${depth}-${match.index}`;
    if (match[1] !== undefined) {
      out.push(<strong key={key}>{depth < 2 ? inline(match[1], depth + 1) : match[1]}</strong>);
    } else if (match[2] !== undefined) {
      out.push(<em key={key}>{depth < 2 ? inline(match[2], depth + 1) : match[2]}</em>);
    } else {
      const href = safeHref(match[4]);
      out.push(
        href ? (
          <a key={key} href={href} rel="noopener noreferrer">
            {match[3]}
          </a>
        ) : (
          match[0]
        ),
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const NUMBERED = /^\d{1,3}[.)]\s+/;

export function MarkdownLite({
  text,
  className,
  headingLevel = 3,
}: {
  text: string | null | undefined;
  className?: string;
  /** The level `## ` draws at (`### ` one below); 3 under a page's h2 section. */
  headingLevel?: 3 | 4 | 5;
}) {
  const source = (text ?? "").slice(0, MAX_SOURCE).replace(/\r\n?/g, "\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];
  let ordered = false;

  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={`p${blocks.length}`}>{inline(paragraph.join(" "))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (items.length) {
      const children = items.map((item, i) => <li key={i}>{inline(item)}</li>);
      blocks.push(ordered ? <ol key={`l${blocks.length}`}>{children}</ol> : <ul key={`l${blocks.length}`}>{children}</ul>);
    }
    items = [];
  };
  const listItem = (item: string, isOrdered: boolean) => {
    flushParagraph();
    if (items.length && ordered !== isOrdered) flushList();
    ordered = isOrdered;
    items.push(item);
  };

  for (const raw of source.split("\n")) {
    const line = raw.trim();
    const heading = /^(#{2,3})\s+(.+)$/.exec(line);
    if (!line) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(6, headingLevel + (heading[1].length - 2));
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      blocks.push(<Tag key={`h${blocks.length}`}>{inline(heading[2].trim())}</Tag>);
    } else if (line.startsWith("- ")) {
      listItem(line.slice(2).trim(), false);
    } else if (NUMBERED.test(line)) {
      listItem(line.replace(NUMBERED, "").trim(), true);
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();

  if (blocks.length === 0) return null;
  return <div className={className}>{blocks}</div>;
}
