"use client";

import { CalendarDays, Download, Eye, FileClock, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { ReportContent } from "@/components/reports/ReportContent";
import { EmptyState, ErrorPanel, LoadingPanel } from "@/components/ui/StateViews";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  deleteReport,
  downloadReportPdf,
  getReports,
  type GeneratedReport
} from "@/lib/api";

export default function ReportHistoryPage() {
  const [reports, setReports] = useState<GeneratedReport[]>([]);
  const [selectedReport, setSelectedReport] = useState<GeneratedReport | null>(
    null
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function loadReports() {
    setIsLoading(true);
    setError(null);

    try {
      setReports(await getReports());
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load report history."
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadReports();
  }, []);

  async function handleDeleteReport(reportId: string) {
    if (!window.confirm("Delete this report from history?")) {
      return;
    }

    setDeletingId(reportId);
    setError(null);
    try {
      await deleteReport(reportId);
      setReports((current) => current.filter((report) => report.id !== reportId));
      setSelectedReport((current) => (current?.id === reportId ? null : current));
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Unable to delete report."
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Reports"
        title="Report History"
        actions={
          <button
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-soft"
            type="button"
          >
            <CalendarDays className="h-4 w-4" />
            All years
          </button>
        }
      />

      <div className="grid gap-6 px-5 py-6 xl:grid-cols-[1fr_520px] lg:px-8">
        <div>
          {isLoading ? (
            <LoadingPanel rows={6} />
          ) : error ? (
            <ErrorPanel message={error} onRetry={loadReports} />
          ) : reports.length === 0 ? (
            <EmptyState
              title="No reports saved"
              description="Generated ESG draft reports will appear here once the backend saves report history."
            />
          ) : (
            <section className="rounded-lg border border-slate-200 bg-white shadow-soft">
              <div className="border-b border-slate-200 px-5 py-4">
                <h3 className="text-base font-semibold text-slate-950">
                  Saved reports
                </h3>
                <p className="mt-1 text-sm text-slate-500">
                  Preview or download generated report versions.
                </p>
              </div>

              <div className="divide-y divide-slate-100">
                {reports.map((report) => (
                  <article
                    key={report.id}
                    className="flex items-center justify-between gap-4 px-5 py-4 hover:bg-slate-50"
                  >
                    <div className="flex min-w-0 items-center gap-4">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700">
                        <FileClock className="h-5 w-5" />
                      </div>
                      <div className="min-w-0">
                        <h4 className="truncate text-sm font-semibold text-slate-950">
                          {report.title}
                        </h4>
                        <p className="mt-1 text-sm text-slate-500">
                          {report.period_start} to {report.period_end} -{" "}
                          {report.version_count ?? 1} version
                        </p>
                      </div>
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
                        {report.status}
                      </span>
                      <button
                        className="inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-700 hover:bg-slate-100"
                        type="button"
                        onClick={() => setSelectedReport(report)}
                      >
                        <Eye className="h-4 w-4" />
                        Preview
                      </button>
                      <button
                        className="inline-flex h-9 items-center gap-2 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white hover:bg-slate-800"
                        type="button"
                        onClick={() => void downloadReportPdf(report)}
                      >
                        <Download className="h-4 w-4" />
                        Download
                      </button>
                      <button
                        className="inline-flex h-9 items-center gap-2 rounded-lg border border-red-200 px-3 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-slate-300"
                        disabled={deletingId === report.id}
                        type="button"
                        onClick={() => void handleDeleteReport(report.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                        Delete
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
        </div>

        <ReportPreview
          report={selectedReport}
          onClose={() => setSelectedReport(null)}
        />
      </div>
    </AppShell>
  );
}

function ReportPreview({
  report,
  onClose
}: {
  report: GeneratedReport | null;
  onClose: () => void;
}) {
  return (
    <aside className="rounded-lg border border-slate-200 bg-white shadow-soft">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
        <div>
          <h3 className="text-base font-semibold text-slate-950">Preview</h3>
          <p className="mt-1 text-sm text-slate-500">
            {report ? report.title : "Select a report"}
          </p>
        </div>
        {report ? (
          <button
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-100"
            type="button"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      <div className="max-h-[calc(100vh-220px)] overflow-auto p-5">
        {report ? (
          <div className="rounded-lg bg-slate-50 p-4">
            <ReportContent content={report.content} />
          </div>
        ) : (
          <EmptyState
            title="No report selected"
            description="Choose Preview from a saved report to inspect the generated content."
          />
        )}
      </div>
    </aside>
  );
}
