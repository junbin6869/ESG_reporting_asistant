import type { ClassificationStatus } from "@/lib/api";

const statusStyles: Record<ClassificationStatus, string> = {
  Reviewed: "bg-emerald-500",
  Pending: "bg-amber-500"
};

export function StatusBadge({ status }: { status: ClassificationStatus }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-700">
      <span className={`h-2 w-2 rounded-full ${statusStyles[status]}`} />
      {status}
    </span>
  );
}
