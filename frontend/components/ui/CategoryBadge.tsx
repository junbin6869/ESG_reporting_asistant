import type { EsgCategory } from "@/lib/api";

const categoryStyles: Record<EsgCategory, string> = {
  Environmental: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  Social: "bg-blue-50 text-blue-700 ring-blue-200",
  Governance: "bg-violet-50 text-violet-700 ring-violet-200",
  "Non-ESG": "bg-slate-100 text-slate-600 ring-slate-200"
};

export function CategoryBadge({ category }: { category: EsgCategory }) {
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${categoryStyles[category]}`}
    >
      {category}
    </span>
  );
}
