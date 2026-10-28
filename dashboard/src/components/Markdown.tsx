import type { ReactNode } from "react";

/**
 * Enough Markdown for runbooks: headings, lists, paragraphs, **bold** and
 * `code`. Builds React elements (never innerHTML), so file content can't
 * inject markup.
 */
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    return part;
  });
}

export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((item, i) => <li key={i}>{inline(item)}</li>);
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    list = null;
  };
  for (const line of source.split("\n")) {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.*)$/);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length + 2, 5);
      const Tag = `h${level}` as "h3" | "h4" | "h5";
      blocks.push(<Tag key={blocks.length}>{inline(heading[2])}</Tag>);
    } else if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (list && list.ordered !== ordered) flush();
      list ??= { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
    } else if (line.trim()) {
      flush();
      blocks.push(<p key={blocks.length}>{inline(line)}</p>);
    } else {
      flush();
    }
  }
  flush();
  return <div className="markdown">{blocks}</div>;
}
