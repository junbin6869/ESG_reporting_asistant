import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Search
} from "lucide-react";
import { CategoryBadge } from "@/components/ui/CategoryBadge";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { InvoiceRow } from "@/lib/api";

export type ClassificationSortColumn =
  | "invoice"
  | "description"
  | "category"
  | "amount"
  | "status"
  | "date";

export type ClassificationSortState = {
  column: ClassificationSortColumn;
  direction: "asc" | "desc";
};

export function RecentClassificationsTable({
  invoices,
  query,
  sortState,
  onQueryChange,
  onSort
}: {
  invoices: InvoiceRow[];
  query: string;
  sortState: ClassificationSortState;
  onQueryChange: (query: string) => void;
  onSort: (column: ClassificationSortColumn) => void;
}) {
  const columns: Array<{ key: ClassificationSortColumn; label: string }> = [
    { key: "invoice", label: "Invoice" },
    { key: "description", label: "Description" },
    { key: "category", label: "Category" },
    { key: "amount", label: "Amount" },
    { key: "status", label: "Status" },
    { key: "date", label: "Date" }
  ];

  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-soft">
      <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-950">
            Recent classifications
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            Latest AI-generated classifications for the selected year.
          </p>
        </div>
        <div className="relative w-full lg:max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            className="h-10 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-700 outline-none ring-blue-100 placeholder:text-slate-400 focus:ring-4"
            placeholder="Search classification records"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
          />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200">
          <thead className="bg-slate-50">
            <tr>
              {columns.map((column) => (
                <SortableHeader
                  key={column.key}
                  column={column.key}
                  label={column.label}
                  sortState={sortState}
                  onSort={onSort}
                />
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {invoices.length === 0 ? (
              <tr>
                <td
                  className="px-5 py-10 text-center text-sm text-slate-500"
                  colSpan={6}
                >
                  No classification records match the selected filters.
                </td>
              </tr>
            ) : (
              invoices.map((invoice) => (
                <tr key={invoice.id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-5 py-4">
                    <p className="text-sm font-medium text-slate-950">
                      {invoice.invoice_number}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {invoice.supplier_name}
                    </p>
                  </td>
                  <td className="max-w-md px-5 py-4">
                    <p className="line-clamp-2 text-sm text-slate-600">
                      {invoice.description}
                    </p>
                  </td>
                  <td className="whitespace-nowrap px-5 py-4">
                    {invoice.classification ? (
                      <CategoryBadge category={invoice.classification.category} />
                    ) : (
                      <span className="text-sm text-slate-400">Processing</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 text-sm font-medium text-slate-700">
                    {formatCurrency(invoice.amount, invoice.currency)}
                  </td>
                  <td className="whitespace-nowrap px-5 py-4">
                    <StatusBadge status={invoice.classification?.status ?? "Pending"} />
                  </td>
                  <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-600">
                    {formatDate(invoice.invoice_date)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SortableHeader({
  column,
  label,
  sortState,
  onSort
}: {
  column: ClassificationSortColumn;
  label: string;
  sortState: ClassificationSortState;
  onSort: (column: ClassificationSortColumn) => void;
}) {
  const isActive = sortState.column === column;
  const Icon = isActive
    ? sortState.direction === "asc"
      ? ArrowUp
      : ArrowDown
    : ArrowUpDown;

  return (
    <th className="px-5 py-3 text-left">
      <button
        className={`inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider transition ${
          isActive ? "text-slate-950" : "text-slate-500 hover:text-slate-950"
        }`}
        type="button"
        onClick={() => onSort(column)}
        aria-sort={
          isActive
            ? sortState.direction === "asc"
              ? "ascending"
              : "descending"
            : "none"
        }
      >
        {label}
        <Icon className="h-3.5 w-3.5" />
      </button>
    </th>
  );
}

function formatCurrency(amount: number, currency: string) {
  return new Intl.NumberFormat("en-MY", {
    style: "currency",
    currency,
    maximumFractionDigits: 2
  }).format(amount);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-MY", {
    year: "numeric",
    month: "short",
    day: "2-digit"
  }).format(new Date(value));
}
