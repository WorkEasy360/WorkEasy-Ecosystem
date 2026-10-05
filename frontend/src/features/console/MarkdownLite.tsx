import type { ReactNode } from "react";

/**
 * A deliberately tiny, safe subset of Markdown for text the catalog team
 * writes (plan and bundle descriptions):
 *
 * - blank-line separated paragraphs;
 * - lines starting with `- ` as a bulleted list;
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

export function MarkdownLite({ text, className }: { text: string | null | undefined; className?: string }) {
  const source = (text ?? "").slice(0, MAX_SOURCE).replace(/\r\n?/g, "\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={`p${blocks.length}`}>{inline(paragraph.join(" "))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (items.length) {
      blocks.push(
        <ul key={`l${blocks.length}`}>
          {items.map((item, i) => (
            <li key={i}>{inline(item)}</li>
          ))}
        </ul>,
      );
    }
    items = [];
  };

  for (const raw of source.split("\n")) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      flushList();
    } else if (line.startsWith("- ")) {
      flushParagraph();
      items.push(line.slice(2).trim());
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
