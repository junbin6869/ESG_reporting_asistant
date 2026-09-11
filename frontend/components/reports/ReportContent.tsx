import type { ReactNode } from "react";

type ReportBlock =
  | { type: "heading"; text: string }
  | { type: "table"; rows: string[][] }
  | { type: "list"; items: Array<{ text: string; level: number }> }
  | { type: "paragraph"; text: string };

export function ReportContent({ content }: { content: string }) {
  const blocks = parseReport(content);

  return (
    <article className="space-y-4 text-sm leading-6 text-slate-700">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return (
            <h3
              key={`${block.type}-${index}`}
              className="border-b border-emerald-100 pb-2 pt-3 text-lg font-semibold text-emerald-800"
            >
              {cleanInlineMarkdown(block.text)}
            </h3>
          );
        }

        if (block.type === "table") {
          return (
            <div
              key={`${block.type}-${index}`}
              className="overflow-x-auto rounded-lg border border-slate-200"
            >
              <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
                <thead className="bg-emerald-50 text-emerald-900">
                  <tr>
                    {block.rows[0]?.map((cell, cellIndex) => (
                      <th
                        key={cellIndex}
                        className="whitespace-nowrap px-3 py-2 font-semibold"
                      >
                        {cleanInlineMarkdown(cell)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 bg-white">
                  {block.rows.slice(1).map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="px-3 py-2 align-top">
                          {cleanInlineMarkdown(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        if (block.type === "list") {
          const containsLimitation = block.items.some((item) =>
            item.text.includes("Not available from invoice evidence")
          );
          return (
            <ul
              key={`${block.type}-${index}`}
              className={
                containsLimitation
                  ? "list-disc space-y-1 rounded-lg border border-amber-200 bg-amber-50 py-3 pl-9 pr-4 text-amber-900"
                  : "list-disc space-y-1 pl-5"
              }
            >
              {block.items.map((item, itemIndex) => (
                <li
                  key={itemIndex}
                  className={item.level > 0 ? "ml-5 list-[circle] text-slate-600" : ""}
                >
                  {cleanInlineMarkdown(item.text)}
                </li>
              ))}
            </ul>
          );
        }

        const isLimitation =
          block.text.includes("Not available from invoice evidence") ||
          block.text.toLowerCase().includes("invoice-based esg draft disclosure") ||
          block.text.toLowerCase().includes("does not prove achieved esg impact");
        const normalizedText = block.text.toLowerCase();
        const isEvidence =
          normalizedText.includes("source:") &&
          normalizedText.includes("topic:") &&
          normalizedText.includes("section:") &&
          normalizedText.includes("page:");

        return (
          <p
            key={`${block.type}-${index}`}
            className={
              isLimitation
                ? "rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-amber-900"
                : isEvidence
                  ? "rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-blue-900"
                  : ""
            }
          >
            {cleanInlineMarkdown(block.text)}
          </p>
        );
      })}
    </article>
  );
}

function parseReport(content: string): ReportBlock[] {
  const lines = content.split(/\r?\n/);
  const blocks: ReportBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }

    if (isHeading(line)) {
      blocks.push({
        type: "heading",
        text: line.startsWith("#") ? line.replace(/^#+\s*/, "") : line
      });
      index += 1;
      continue;
    }

    if (line.startsWith("|")) {
      const tableLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableLines.push(lines[index].trim());
        index += 1;
      }
      const rows = tableLines
        .map((tableLine) =>
          tableLine
            .replace(/^\||\|$/g, "")
            .split("|")
            .map((cell) => cell.trim())
        )
        .filter((row) => !row.every((cell) => /^:?-+:?$/.test(cell)));
      blocks.push({ type: "table", rows });
      continue;
    }

    if (/^\s*-\s+/.test(lines[index])) {
      const items: Array<{ text: string; level: number }> = [];
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^(\s*)-\s+(.*)$/);
        if (!itemMatch) break;
        items.push({
          text: itemMatch[2],
          level: Math.floor(itemMatch[1].length / 2)
        });
        index += 1;
      }
      blocks.push({ type: "list", items });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isHeading(lines[index].trim()) &&
      !lines[index].trim().startsWith("|") &&
      !lines[index].trim().startsWith("- ")
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join(" ") });
  }

  return blocks;
}

function isHeading(line: string) {
  return line.startsWith("#") || /^\d+\.\s+/.test(line);
}

function cleanInlineMarkdown(text: string): ReactNode {
  return text.replace(/\*\*/g, "").replace(/`/g, "");
}
