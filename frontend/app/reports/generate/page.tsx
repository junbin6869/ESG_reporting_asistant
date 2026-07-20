"use client";

import {
  CalendarDays,
  CheckCircle2,
  Circle,
  FileText,
  Loader2,
  Sparkles,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { EmptyState, ErrorPanel } from "@/components/ui/StateViews";
import { PageHeader } from "@/components/ui/PageHeader";
import { ReportContent } from "@/components/reports/ReportContent";
import {
  getAgentRun,
  getRecentInvoices,
  startAgenticReport,
  type AgentRun,
  type GeneratedReport,
  type InvoiceRow
} from "@/lib/api";

export default function GenerateReportPage() {
  const [invoices, setInvoices] = useState<InvoiceRow[]>([]);
  const [selectedYear, setSelectedYear] = useState("2026");
  const [report, setReport] = useState<GeneratedReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);

  async function loadReportContext() {
    setIsLoading(true);
    setError(null);

    try {
      setInvoices(await getRecentInvoices());
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load reviewed invoices."
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadReportContext();
  }, []);

  const years = useMemo(() => {
    const discovered = invoices
      .map((invoice) => invoice.invoice_date.slice(0, 4))
      .filter(Boolean);
    return Array.from(new Set(["2026", ...discovered])).sort().reverse();
  }, [invoices]);

  const reviewedInvoiceCount = useMemo(
    () =>
      invoices.filter(
        (invoice) =>
          invoice.invoice_date.startsWith(selectedYear) &&
          invoice.classification?.status === "Reviewed"
      ).length,
    [invoices, selectedYear]
  );

  async function handleGenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);

    setIsGenerating(true);
    setError(null);

    try {
      const run = await startAgenticReport({
        title: String(formData.get("title") ?? "ESG Draft Report"),
        period_start: `${selectedYear}-01-01`,
        period_end: `${selectedYear}-12-31`
      });
      setAgentRun(run);
    } catch (generateError) {
      setError(
        generateError instanceof Error
          ? generateError.message
          : "Unable to generate report."
      );
    }
  }

  useEffect(() => {
    if (!agentRun || ["completed", "failed"].includes(agentRun.status)) {
      setIsGenerating(false);
      if (agentRun?.report) {
        setReport(agentRun.report);
      }
      return;
    }

    const timer = window.setTimeout(async () => {
      try {
        const nextRun = await getAgentRun(agentRun.id);
        setAgentRun(nextRun);
      } catch (pollError) {
        setError(
          pollError instanceof Error
            ? pollError.message
            : "Unable to fetch agent status."
        );
        setIsGenerating(false);
      }
    }, 1000);

    return () => window.clearTimeout(timer);
  }, [agentRun]);

  return (
    <AppShell>
      <PageHeader
        eyebrow="Reports"
        title="Generate Report"
      />

      <div className="space-y-6 px-5 py-6 lg:px-8">

        <div className="grid gap-6 xl:grid-cols-[380px_1fr]">
          <form
            className="rounded-lg border border-slate-200 bg-white p-5 shadow-soft"
            onSubmit={handleGenerate}
          >
            <div className="mb-5 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-violet-50 text-violet-700">
                <Sparkles className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-slate-950">
                  Report settings
                </h3>
                <p className="text-sm text-slate-500">
                  Agent workflow uses reviewed invoices only.
                </p>
              </div>
            </div>

            <label className="block">
              <span className="text-sm font-medium text-slate-700">Title</span>
              <input
                className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700 outline-none ring-blue-100 focus:ring-4"
                defaultValue="ESG Draft Report - 2026"
                name="title"
                required
              />
            </label>
            <div className="mt-4">
              <label className="block">
                <span className="text-sm font-medium text-slate-700">
                  Reporting year
                </span>
                <span className="mt-1 flex items-center gap-3">
                  <span className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm text-slate-700 ring-blue-100 focus-within:ring-4">
                    <CalendarDays className="h-4 w-4 text-slate-400" />
                    <select
                      className="min-w-0 flex-1 bg-transparent outline-none"
                      name="year"
                      value={selectedYear}
                      onChange={(event) => setSelectedYear(event.target.value)}
                    >
                      {years.map((year) => (
                        <option key={year} value={year}>
                          {year}
                        </option>
                      ))}
                    </select>
                  </span>
                  <span className="text-xs font-medium leading-5 text-emerald-700">
                    {isLoading
                      ? "Loading reviewed invoices..."
                      : `${reviewedInvoiceCount} reviewed ${
                          reviewedInvoiceCount === 1 ? "invoice" : "invoices"
                        } selected`}
                  </span>
                </span>
              </label>
            </div>

            <button
              className="mt-5 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              disabled={isGenerating}
              type="submit"
            >
              <Sparkles className="h-4 w-4" />
              {isGenerating ? "Generating..." : "Generate draft report"}
            </button>
          </form>

          <section className="rounded-lg border border-slate-200 bg-white shadow-soft">
            <div className="border-b border-slate-200 px-5 py-4">
              <h3 className="text-base font-semibold text-slate-950">
                Agent state
              </h3>
              <p className="mt-1 text-sm text-slate-500">
                Live progress from the report generation agent.
              </p>
            </div>

            <div className="p-5">
              {error ? (
                <ErrorPanel message={error} onRetry={loadReportContext} />
              ) : agentRun ? (
                <AgentRunPanel run={agentRun} />
              ) : (
                <EmptyState
                  title="Agent has not started"
                  description="Choose a reporting year and start the agentic report workflow."
                />
              )}
            </div>
          </section>
        </div>

        <section className="rounded-lg border border-slate-200 bg-white shadow-soft">
          <div className="border-b border-slate-200 px-5 py-4">
            <h3 className="text-base font-semibold text-slate-950">
              Report preview
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Generated report appears here when the agent completes.
            </p>
          </div>

          <div className="p-5">
            {report ? (
              <article className="prose prose-slate max-w-none">
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
                    <FileText className="h-5 w-5" />
                  </div>
                  <div>
                    <h4 className="m-0 text-base font-semibold text-slate-950">
                      {report.title}
                    </h4>
                    <p className="m-0 text-sm text-slate-500">
                      {report.period_start} to {report.period_end}
                    </p>
                  </div>
                </div>
                <div className="rounded-lg bg-slate-50 p-4">
                  <ReportContent content={report.content} />
                </div>
              </article>
            ) : (
              <EmptyState
                title="No report generated yet"
                description="The report preview will appear after the agent finishes saving the report."
              />
            )}
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function AgentRunPanel({ run }: { run: AgentRun }) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg bg-slate-50 p-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Run status
        </p>
        <p className="mt-1 text-sm font-medium text-slate-900">{run.status}</p>
        {run.error ? <p className="mt-2 text-sm text-red-700">{run.error}</p> : null}
      </div>
      <div className="space-y-3">
        {run.steps.map((step) => (
          <div key={step.id} className="flex gap-3 rounded-lg border border-slate-200 p-3">
            <StepIcon status={step.status} />
            <div>
              <p className="text-sm font-medium text-slate-900">{step.label}</p>
              <p className="mt-1 text-sm text-slate-500">
                {step.detail ?? step.status}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: AgentRun["steps"][number]["status"] }) {
  if (status === "completed") {
    return <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-600" />;
  }
  if (status === "running") {
    return <Loader2 className="mt-0.5 h-5 w-5 animate-spin text-blue-600" />;
  }
  if (status === "failed") {
    return <XCircle className="mt-0.5 h-5 w-5 text-red-600" />;
  }
  return <Circle className="mt-0.5 h-5 w-5 text-slate-300" />;
}
