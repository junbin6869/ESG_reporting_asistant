"use client";

import { AlertCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { DashboardHeader } from "@/components/dashboard/DashboardHeader";
import {
  RecentClassificationsTable,
  type ClassificationSortColumn,
  type ClassificationSortState
} from "@/components/dashboard/RecentClassificationsTable";
import { SummaryCards } from "@/components/dashboard/SummaryCards";
import {
  getDashboardSummary,
  getRecentInvoices,
  type DashboardSummary,
  type InvoiceRow
} from "@/lib/api";

type DashboardState = {
  summary: DashboardSummary | null;
  invoices: InvoiceRow[];
  isLoading: boolean;
  error: string | null;
};

export default function DashboardPage() {
  const [selectedYear, setSelectedYear] = useState("2026");
  const [query, setQuery] = useState("");
  const [sortState, setSortState] = useState<ClassificationSortState>({
    column: "date",
    direction: "desc"
  });
  const [state, setState] = useState<DashboardState>({
    summary: null,
    invoices: [],
    isLoading: true,
    error: null
  });

  async function loadDashboard(year = selectedYear) {
    setState((current) => ({
      ...current,
      isLoading: true,
      error: null
    }));

    try {
      const [summary, invoices] = await Promise.all([
        getDashboardSummary(year),
        getRecentInvoices()
      ]);

      setState({
        summary,
        invoices,
        isLoading: false,
        error: null
      });
    } catch (error) {
      setState({
        summary: null,
        invoices: [],
        isLoading: false,
        error:
          error instanceof Error
            ? error.message
            : "Unable to load dashboard data."
      });
    }
  }

  useEffect(() => {
    void loadDashboard(selectedYear);
  }, [selectedYear]);

  const years = useMemo(() => {
    const discovered = state.invoices
      .map((invoice) => invoice.invoice_date.slice(0, 4))
      .filter(Boolean);
    return Array.from(new Set(["2026", ...discovered])).sort().reverse();
  }, [state.invoices]);

  const visibleInvoices = useMemo(() => {
    const search = query.trim().toLowerCase();
    const rows = state.invoices
      .filter((invoice) => invoice.invoice_date.startsWith(selectedYear))
      .filter((invoice) => {
        if (!search) {
          return true;
        }
        return [
          invoice.invoice_number,
          invoice.supplier_name,
          invoice.description,
          invoice.classification?.category ?? "",
          invoice.classification?.status ?? "Pending"
        ]
          .join(" ")
          .toLowerCase()
          .includes(search);
      });

    rows.sort((a, b) => compareInvoices(a, b, sortState));

    return rows;
  }, [query, selectedYear, sortState, state.invoices]);

  function handleSort(column: ClassificationSortColumn) {
    setSortState((current) => ({
      column,
      direction:
        current.column === column && current.direction === "asc" ? "desc" : "asc"
    }));
  }

  return (
    <AppShell>
      <DashboardHeader
        selectedYear={selectedYear}
        years={years}
        onYearChange={setSelectedYear}
      />

      <div className="px-5 py-6 lg:px-8">
        {state.isLoading ? (
          <DashboardLoading />
        ) : state.error ? (
          <DashboardError
            message={state.error}
            onRetry={() => loadDashboard(selectedYear)}
          />
        ) : state.summary ? (
          <div className="space-y-6">
            <SummaryCards summary={state.summary} />

            <RecentClassificationsTable
              invoices={visibleInvoices}
              query={query}
              sortState={sortState}
              onQueryChange={setQuery}
              onSort={handleSort}
            />
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}

function compareInvoices(
  first: InvoiceRow,
  second: InvoiceRow,
  sortState: ClassificationSortState
) {
  const direction = sortState.direction === "asc" ? 1 : -1;

  if (sortState.column === "amount") {
    return (first.amount - second.amount) * direction;
  }

  const firstValue = getSortableInvoiceValue(first, sortState.column);
  const secondValue = getSortableInvoiceValue(second, sortState.column);

  return firstValue.localeCompare(secondValue, undefined, {
    numeric: true,
    sensitivity: "base"
  }) * direction;
}

function getSortableInvoiceValue(
  invoice: InvoiceRow,
  column: ClassificationSortColumn
) {
  if (column === "invoice") return invoice.invoice_number;
  if (column === "description") return invoice.description;
  if (column === "category") return invoice.classification?.category ?? "";
  if (column === "status") return invoice.classification?.status ?? "Pending";
  if (column === "date") return invoice.invoice_date;
  return String(invoice.amount);
}

function DashboardLoading() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div
            key={index}
            className="h-40 animate-pulse rounded-lg border border-slate-200 bg-white p-5 shadow-soft"
          />
        ))}
      </div>
      <div className="h-96 animate-pulse rounded-lg border border-slate-200 bg-white shadow-soft" />
    </div>
  );
}

function DashboardError({
  message,
  onRetry
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-red-200 bg-white p-6 shadow-soft">
      <div className="flex items-start gap-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-red-50 text-red-600">
          <AlertCircle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold text-slate-950">
            Could not load dashboard data
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            Check that the FastAPI backend is running and that
            `NEXT_PUBLIC_API_BASE_URL` points to the correct server.
          </p>
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {message}
          </p>
          <button
            type="button"
            className="mt-4 inline-flex h-10 items-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-800"
            onClick={onRetry}
          >
            <RefreshCw className="h-4 w-4" />
            Retry
          </button>
        </div>
      </div>
    </div>
  );
}
