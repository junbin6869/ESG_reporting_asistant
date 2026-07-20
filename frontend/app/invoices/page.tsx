"use client";

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  Filter,
  Search,
  Trash2,
  Upload,
  X
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/dashboard/AppShell";
import { CategoryBadge } from "@/components/ui/CategoryBadge";
import { EmptyState, ErrorPanel, LoadingPanel } from "@/components/ui/StateViews";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  deleteInvoice,
  createInvoice,
  createInvoiceBatch,
  extractInvoicePreview,
  getInvoiceBatch,
  getRecentInvoices,
  reviewClassification,
  type EsgCategory,
  type InvoiceBatch,
  type InvoiceExtractionPreview,
  type InvoiceCreatePayload,
  type InvoiceRow
} from "@/lib/api";

type QueryProperty = "invoice_number" | "supplier_name" | "description" | "category" | "status" | "amount" | "invoice_date";
type QueryCondition = "contains" | "equals" | "gt" | "lt";
type QueryJoin = "AND" | "OR";
type SortColumn = "invoice" | "date" | "description" | "category" | "amount" | "status";
type SortDirection = "asc" | "desc";
type SortState = {
  column: SortColumn;
  direction: SortDirection;
};
type StatusFilter = "all" | "pending" | "reviewed";

type QueryRule = {
  id: string;
  join: QueryJoin;
  property: QueryProperty;
  condition: QueryCondition;
  value: string;
};

type ExtractionPreviewItem = {
  preview: InvoiceExtractionPreview;
  fileName: string;
  position: number;
  total: number;
};

type ExtractionPreviewState = {
  active: ExtractionPreviewItem | null;
  queue: ExtractionPreviewItem[];
};

type PdfExtractionProgress = {
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  failures: string[];
};

type UploadFeedback = {
  message: string;
  tone: "success" | "warning";
};

type StoredInvoiceBatch = {
  id: string;
  total: number;
};

const PDF_EXTRACTION_CONCURRENCY = 3;
const MAX_PDF_FILES_PER_UPLOAD = 100;
const MAX_PDF_FILE_SIZE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_PDF_SIZE_BYTES = 100 * 1024 * 1024;
const MAX_JSON_FILE_SIZE_BYTES = 10 * 1024 * 1024;
const MAX_RAW_TEXT_PREVIEW_CHARACTERS = 20_000;
const BATCH_POLL_DELAYS_MS = [1_000, 2_000, 4_000] as const;
const MAX_BATCH_POLL_ATTEMPTS = 150;
const MAX_CONSECUTIVE_BATCH_POLL_ERRORS = 5;
const ACTIVE_BATCH_STORAGE_KEY = "esg-assistant-active-invoice-batch";

const propertyLabels: Record<QueryProperty, string> = {
  invoice_number: "Invoice ID",
  supplier_name: "Supplier",
  description: "Description",
  category: "Category",
  status: "Status",
  amount: "Amount",
  invoice_date: "Invoice date"
};

export default function InvoicesPage() {
  const [invoices, setInvoices] = useState<InvoiceRow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortState, setSortState] = useState<SortState>({
    column: "date",
    direction: "desc"
  });
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [rules, setRules] = useState<QueryRule[]>([]);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [selectedInvoice, setSelectedInvoice] = useState<InvoiceRow | null>(null);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isSavingExtraction, setIsSavingExtraction] = useState(false);
  const [uploadFeedback, setUploadFeedback] = useState<UploadFeedback | null>(null);
  const [batchProgress, setBatchProgress] = useState<InvoiceBatch | null>(null);
  const [pdfProgress, setPdfProgress] = useState<PdfExtractionProgress | null>(null);
  const [extractionPreviews, setExtractionPreviews] =
    useState<ExtractionPreviewState>({ active: null, queue: [] });
  const didInitialize = useRef(false);

  async function loadInvoices() {
    setIsLoading(true);
    setError(null);

    try {
      setInvoices(await getRecentInvoices());
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "Unable to load invoices."
      );
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    if (didInitialize.current) return;
    didInitialize.current = true;
    void loadInvoices();
    const storedBatch = readStoredInvoiceBatch();
    if (storedBatch) {
      void resumeInvoiceBatch(storedBatch.id, storedBatch.total);
    }
  }, []);

  const filteredInvoices = useMemo(() => {
    const activeRules = rules.filter((rule) => rule.value.trim());
    const rows = invoices.filter(
      (invoice) =>
        matchesStatusFilter(invoice, statusFilter) &&
        matchesRules(invoice, activeRules)
    );

    rows.sort((a, b) => compareInvoices(a, b, sortState));

    return rows;
  }, [invoices, rules, sortState, statusFilter]);

  function handleSort(column: SortColumn) {
    setSortState((current) => ({
      column,
      direction:
        current.column === column && current.direction === "asc" ? "desc" : "asc"
    }));
  }

  async function handleReview(invoiceId: string, category: EsgCategory) {
    setReviewingId(invoiceId);
    setError(null);

    try {
      await reviewClassification(invoiceId, { category });
      await loadInvoices();
      setSelectedInvoice(null);
    } catch (reviewError) {
      setError(
        reviewError instanceof Error
          ? reviewError.message
          : "Unable to review classification."
      );
    } finally {
      setReviewingId(null);
    }
  }

  async function handleDeleteInvoice(invoiceId: string) {
    if (!window.confirm("Delete this invoice and its classification history?")) {
      return;
    }

    setDeletingId(invoiceId);
    setError(null);
    try {
      await deleteInvoice(invoiceId);
      setSelectedInvoice(null);
      await loadInvoices();
    } catch (deleteError) {
      setError(
        deleteError instanceof Error
          ? deleteError.message
          : "Unable to delete invoice."
      );
    } finally {
      setDeletingId(null);
    }
  }

  async function handleUploadInvoices(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;

    setIsUploading(true);
    setError(null);
    setUploadFeedback(null);
    setBatchProgress(null);
    setPdfProgress(null);
    setExtractionPreviews({ active: null, queue: [] });

    try {
      const payloads: InvoiceCreatePayload[] = [];
      const rejectedJsonRecords: string[] = [];
      const pdfFiles = files.filter(isPdfFile);
      const jsonFiles = files.filter(isJsonFile);
      const unsupportedFiles = files.filter(
        (file) => !isPdfFile(file) && !isJsonFile(file)
      );

      if (unsupportedFiles.length > 0) {
        throw new Error(
          `Upload only JSON or PDF invoice files. Unsupported: ${unsupportedFiles
            .map((file) => file.name)
            .join(", ")}.`
        );
      }

      validateUploadSizes(jsonFiles, pdfFiles);

      for (const file of jsonFiles) {
        try {
          const text = await file.text();
          const data = JSON.parse(text) as unknown;
          const parsed = parseInvoiceUpload(data);
          payloads.push(...parsed.payloads);
          rejectedJsonRecords.push(
            ...parsed.failures.map((failure) => `${file.name}: ${failure}`)
          );
        } catch (parseError) {
          const detail =
            parseError instanceof Error ? parseError.message : "Invalid JSON file.";
          throw new Error(`${file.name}: ${detail}`);
        }
      }

      if (rejectedJsonRecords.length > 0) {
        setUploadFeedback({
          message: `${rejectedJsonRecords.length} invalid JSON invoice record(s) were skipped. ${rejectedJsonRecords
            .slice(0, 3)
            .join(" ")}`,
          tone: "warning"
        });
      }

      const tasks: Promise<void>[] = [];
      if (payloads.length > 0) {
        tasks.push(importInvoicePayloads(payloads));
      }
      if (pdfFiles.length > 0) {
        tasks.push(extractPdfPreviews(pdfFiles));
      }

      const results = await Promise.allSettled(tasks);
      const failures = results
        .filter(
          (result): result is PromiseRejectedResult => result.status === "rejected"
        )
        .map((result) =>
          result.reason instanceof Error ? result.reason.message : String(result.reason)
        );

      if (failures.length > 0) {
        throw new Error(failures.join(" "));
      }
      if (rejectedJsonRecords.length > 0 && payloads.length > 0) {
        setUploadFeedback((current) => ({
          message: `${current?.message ?? "Upload finished."} ${
            rejectedJsonRecords.length
          } invalid JSON record(s) were skipped.`,
          tone: "warning"
        }));
      }
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Unable to upload invoice."
      );
    } finally {
      setIsUploading(false);
    }
  }

  async function handleConfirmExtraction(payload: InvoiceCreatePayload) {
    setIsSavingExtraction(true);
    setError(null);
    setUploadFeedback(null);

    try {
      const isUpdate = invoices.some(
        (invoice) => invoice.invoice_number === payload.invoice_number
      );
      await createInvoice(payload);
      await loadInvoices();
      setUploadFeedback({
        message: isUpdate
          ? `Updated existing invoice ${payload.invoice_number}.`
          : `Uploaded invoice ${payload.invoice_number}.`,
        tone: "success"
      });
      showNextExtractionPreview();
    } catch (importError) {
      setError(
        importError instanceof Error
          ? importError.message
          : "Unable to import extracted invoice."
      );
    } finally {
      setIsSavingExtraction(false);
    }
  }

  function handleCloseExtraction() {
    showNextExtractionPreview();
  }

  function showNextExtractionPreview() {
    setExtractionPreviews((current) => {
      const [next, ...remaining] = current.queue;
      return { active: next ?? null, queue: remaining };
    });
  }

  async function resumeInvoiceBatch(batchId: string, fallbackTotal: number) {
    setIsUploading(true);
    setError(null);
    const seed = emptyInvoiceBatch(batchId, fallbackTotal);
    setBatchProgress(seed);
    setUploadFeedback({
      message: `Resuming progress tracking for invoice batch ${batchId}.`,
      tone: "warning"
    });

    try {
      const result = await pollInvoiceBatch(seed, fallbackTotal);
      await applyBatchPollingResult(result.batch, fallbackTotal, result.lastError);
    } finally {
      setIsUploading(false);
    }
  }

  async function pollInvoiceBatch(initialBatch: InvoiceBatch, fallbackTotal: number) {
    let batch = initialBatch;
    let lastError: Error | null = null;
    let consecutiveErrors = 0;

    for (let attempt = 0; attempt < MAX_BATCH_POLL_ATTEMPTS; attempt += 1) {
      const delay = BATCH_POLL_DELAYS_MS[Math.min(attempt, 2)];
      await wait(delay);
      try {
        batch = await getInvoiceBatch(
          batch.id ?? "",
          batch.total || fallbackTotal
        );
        setBatchProgress(batch);
        lastError = null;
        consecutiveErrors = 0;
        if (isTerminalBatch(batch)) break;
      } catch (pollError) {
        lastError =
          pollError instanceof Error ? pollError : new Error(String(pollError));
        consecutiveErrors += 1;
        if (consecutiveErrors >= MAX_CONSECUTIVE_BATCH_POLL_ERRORS) break;
      }
    }

    return { batch, lastError };
  }

  async function applyBatchPollingResult(
    batch: InvoiceBatch,
    fallbackTotal: number,
    lastError: Error | null
  ) {
    setBatchProgress(batch);
    if (isTerminalBatch(batch)) {
      clearStoredInvoiceBatch(batch.id);
      await loadInvoices();
      setUploadFeedback({
        message: buildBatchUploadFeedback(batch, fallbackTotal),
        tone: batch.failed > 0 ? "warning" : "success"
      });
      return;
    }

    setUploadFeedback({
      message: `Batch ${batch.id ?? ""} is still processing on the server. Use Resume tracking to continue.${
        lastError ? ` Last check: ${lastError.message}` : ""
      }`,
      tone: "warning"
    });
  }

  async function importInvoicePayloads(payloads: InvoiceCreatePayload[]) {
    const initialBatch = await createInvoiceBatch(payloads);
    setBatchProgress(initialBatch);
    if (initialBatch.id) {
      storeInvoiceBatch(initialBatch.id, initialBatch.total || payloads.length);
    }

    if (!isTerminalBatch(initialBatch) && !initialBatch.id) {
      throw new Error(
        "The invoice batch was accepted, but the API did not return a batch id."
      );
    }

    const result = isTerminalBatch(initialBatch)
      ? { batch: initialBatch, lastError: null }
      : await pollInvoiceBatch(initialBatch, payloads.length);
    await applyBatchPollingResult(result.batch, payloads.length, result.lastError);
  }

  async function extractPdfPreviews(files: File[]) {
    setPdfProgress({
      total: files.length,
      processed: 0,
      succeeded: 0,
      failed: 0,
      failures: []
    });

    let nextIndex = 0;
    async function worker() {
      while (nextIndex < files.length) {
        const index = nextIndex;
        nextIndex += 1;
        const file = files[index];

        try {
          const preview = limitRawTextPreview(await extractInvoicePreview(file));
          const item: ExtractionPreviewItem = {
            preview,
            fileName: file.name,
            position: index + 1,
            total: files.length
          };
          setExtractionPreviews((current) =>
            current.active
              ? { ...current, queue: [...current.queue, item] }
              : { active: item, queue: current.queue }
          );
          setPdfProgress((current) =>
            current
              ? {
                  ...current,
                  processed: current.processed + 1,
                  succeeded: current.succeeded + 1
                }
              : current
          );
        } catch (extractionError) {
          const detail =
            extractionError instanceof Error
              ? extractionError.message
              : "Unable to extract invoice.";
          setPdfProgress((current) =>
            current
              ? {
                  ...current,
                  processed: current.processed + 1,
                  failed: current.failed + 1,
                  failures: [...current.failures, `${file.name}: ${detail}`]
                }
              : current
          );
        }
      }
    }

    await Promise.all(
      Array.from(
        { length: Math.min(PDF_EXTRACTION_CONCURRENCY, files.length) },
        () => worker()
      )
    );
  }

  const hasPendingPdfReviews = Boolean(extractionPreviews.active) ||
    extractionPreviews.queue.length > 0;
  const uploadDisabled = isUploading || isSavingExtraction || hasPendingPdfReviews;

  return (
    <AppShell>
      <PageHeader eyebrow="Invoice workflow" title="Invoices" />

      <div className="space-y-6 px-4 py-6 sm:px-5 lg:px-8">
        <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-soft md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="text-sm font-semibold text-slate-950">
              Invoice records
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              {filteredInvoices.length} of {invoices.length} records shown
            </p>
            {uploadFeedback ? (
              <div
                className={`mt-2 rounded-md px-3 py-2 text-sm font-medium ${
                  uploadFeedback.tone === "warning"
                    ? "bg-amber-50 text-amber-800"
                    : "bg-emerald-50 text-emerald-700"
                }`}
              >
                <span>{uploadFeedback.message}</span>
                {batchProgress?.id &&
                !isTerminalBatch(batchProgress) &&
                !isUploading ? (
                  <button
                    className="ml-2 underline underline-offset-2"
                    type="button"
                    onClick={() =>
                      void resumeInvoiceBatch(
                        batchProgress.id as string,
                        batchProgress.total
                      )
                    }
                  >
                    Resume tracking
                  </button>
                ) : null}
              </div>
            ) : null}
            <p className="mt-2 max-w-3xl text-xs leading-5 text-slate-400">
              JSON arrays and {`{ "invoices": [...] }`} are sent as one batch and
              support 100+ records. PDFs are limited to 100 files, 10 MB each and
              100 MB total; extraction runs three files at a time and each result is
              reviewed separately.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusFilterControl
              value={statusFilter}
              invoices={invoices}
              onChange={setStatusFilter}
            />
            <label
              className={`inline-flex h-10 cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 ${
                uploadDisabled ? "pointer-events-none opacity-60" : ""
              }`}
            >
              <Upload className="h-4 w-4" />
              {isUploading
                ? "Processing files"
                : hasPendingPdfReviews
                  ? "Review PDFs first"
                  : "Upload invoice"}
              <input
                className="sr-only"
                type="file"
                accept="application/json,.json,application/pdf,.pdf"
                multiple
                disabled={uploadDisabled}
                onChange={(event) => {
                  void handleUploadInvoices(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
            </label>
            <button
              className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
              type="button"
              onClick={() => setIsSearchOpen(true)}
            >
              <Search className="h-4 w-4" />
              Advanced filter
              {rules.length > 0 ? (
                <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                  {rules.length}
                </span>
              ) : null}
            </button>
          </div>
        </div>

        {batchProgress ? <BatchProgressPanel batch={batchProgress} /> : null}
        {pdfProgress ? (
          <PdfExtractionProgressPanel
            progress={pdfProgress}
            waitingForReview={
              (extractionPreviews.active ? 1 : 0) + extractionPreviews.queue.length
            }
          />
        ) : null}

        {isLoading ? (
          <LoadingPanel rows={8} />
        ) : error ? (
          <ErrorPanel message={error} onRetry={loadInvoices} />
        ) : filteredInvoices.length === 0 ? (
          <EmptyState
            title="No invoices found"
            description="Adjust the status filter or advanced search conditions."
          />
        ) : (
          <InvoicesTable
            invoices={filteredInvoices}
            deletingId={deletingId}
            sortState={sortState}
            onSelectInvoice={setSelectedInvoice}
            onSort={handleSort}
            onDeleteInvoice={handleDeleteInvoice}
          />
        )}
      </div>

      <AdvancedSearchModal
        isOpen={isSearchOpen}
        rules={rules}
        onClose={() => setIsSearchOpen(false)}
        onApply={setRules}
      />
      <InvoiceDetailModal
        invoice={selectedInvoice}
        isReviewing={reviewingId === selectedInvoice?.id}
        onClose={() => setSelectedInvoice(null)}
        onReview={handleReview}
        onDelete={handleDeleteInvoice}
      />
      <InvoiceExtractionModal
        item={extractionPreviews.active}
        isSaving={isSavingExtraction}
        onClose={handleCloseExtraction}
        onConfirm={handleConfirmExtraction}
      />
    </AppShell>
  );
}

function StatusFilterControl({
  value,
  invoices,
  onChange
}: {
  value: StatusFilter;
  invoices: InvoiceRow[];
  onChange: (value: StatusFilter) => void;
}) {
  const pendingCount = invoices.filter(
    (invoice) => (invoice.classification?.status ?? "Pending") === "Pending"
  ).length;
  const reviewedCount = invoices.filter(
    (invoice) => invoice.classification?.status === "Reviewed"
  ).length;

  const options: Array<{ value: StatusFilter; label: string; count: number }> = [
    { value: "all", label: "All", count: invoices.length },
    { value: "pending", label: "Pending", count: pendingCount },
    { value: "reviewed", label: "Reviewed", count: reviewedCount }
  ];

  return (
    <div className="inline-flex h-10 rounded-lg border border-slate-200 bg-slate-50 p-1">
      {options.map((option) => (
        <button
          key={option.value}
          className={`inline-flex items-center gap-1 rounded-md px-3 text-sm font-medium transition ${
            value === option.value
              ? "bg-white text-slate-950 shadow-sm"
              : "text-slate-600 hover:text-slate-950"
          }`}
          type="button"
          onClick={() => onChange(option.value)}
        >
          {option.label}
          <span className="text-xs text-slate-400">{option.count}</span>
        </button>
      ))}
    </div>
  );
}

function BatchProgressPanel({ batch }: { batch: InvoiceBatch }) {
  const total = Math.max(batch.total, 0);
  const processed = Math.min(Math.max(batch.processed, 0), total || batch.processed);
  const percentage = total > 0 ? Math.round((processed / total) * 100) : 0;
  const terminal = isTerminalBatch(batch);
  const failures = batch.items.filter(
    (item) => item.status?.toLowerCase() === "failed" || Boolean(item.error)
  );

  return (
    <section
      className={`rounded-lg border p-4 shadow-soft ${
        batch.failed > 0
          ? "border-amber-200 bg-amber-50"
          : terminal
            ? "border-emerald-200 bg-emerald-50"
            : "border-blue-200 bg-blue-50"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">
            {terminal ? "Invoice batch finished" : "Importing and classifying invoices"}
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            {processed} of {total || "?"} processed
            {batch.running > 0 ? ` | ${batch.running} running` : ""}
            {batch.queued > 0 ? ` | ${batch.queued} queued` : ""}
            {batch.failed > 0 ? ` | ${batch.failed} failed` : ""}
          </p>
        </div>
        <span className="rounded-full bg-white/80 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
          {batch.status.replaceAll("_", " ")}
        </span>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/80">
        <div
          className={`h-full rounded-full transition-all ${
            batch.failed > 0 ? "bg-amber-500" : terminal ? "bg-emerald-500" : "bg-blue-500"
          }`}
          style={{ width: `${terminal && total === 0 ? 100 : percentage}%` }}
        />
      </div>

      {batch.failed > 0 ? (
        <div className="mt-3 text-sm text-amber-900">
          <p className="font-medium">Failure summary</p>
          {failures.length > 0 ? (
            <ul className="mt-1 space-y-1">
              {failures.slice(0, 5).map((item, index) => (
                <li key={`${item.id ?? item.invoice_number ?? "failure"}-${index}`}>
                  {item.invoice_number ?? item.id ?? `Invoice ${index + 1}`}: {item.error ?? "Processing failed."}
                </li>
              ))}
              {failures.length > 5 ? (
                <li>And {failures.length - 5} more failure(s).</li>
              ) : null}
            </ul>
          ) : (
            <p className="mt-1">
              {batch.failed} invoice(s) failed; the API did not return item-level details.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function PdfExtractionProgressPanel({
  progress,
  waitingForReview
}: {
  progress: PdfExtractionProgress;
  waitingForReview: number;
}) {
  const percentage =
    progress.total > 0
      ? Math.round((progress.processed / progress.total) * 100)
      : 0;
  const finished = progress.processed >= progress.total;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-soft">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">
            {finished ? "PDF extraction finished" : "Extracting PDF previews"}
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            {progress.processed} of {progress.total} extracted | {waitingForReview} waiting
            for review
            {progress.failed > 0 ? ` | ${progress.failed} failed` : ""}
          </p>
        </div>
        <span className="text-xs font-medium text-slate-500">
          Up to {PDF_EXTRACTION_CONCURRENCY} concurrent
        </span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-blue-500 transition-all"
          style={{ width: `${percentage}%` }}
        />
      </div>
      {progress.failures.length > 0 ? (
        <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          <p className="font-medium">PDF failures</p>
          <ul className="mt-1 space-y-1">
            {progress.failures.slice(0, 5).map((failure) => (
              <li key={failure}>{failure}</li>
            ))}
            {progress.failures.length > 5 ? (
              <li>And {progress.failures.length - 5} more failure(s).</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function AdvancedSearchModal({
  isOpen,
  rules,
  onClose,
  onApply
}: {
  isOpen: boolean;
  rules: QueryRule[];
  onClose: () => void;
  onApply: (rules: QueryRule[]) => void;
}) {
  const [draftRules, setDraftRules] = useState<QueryRule[]>(rules);

  useEffect(() => {
    if (isOpen) {
      setDraftRules(
        rules.length > 0
          ? rules
          : [createRule({ join: "AND", property: "invoice_number", condition: "equals" })]
      );
    }
  }, [isOpen, rules]);

  if (!isOpen) return null;

  function updateRule(id: string, patch: Partial<QueryRule>) {
    setDraftRules((current) =>
      current.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule))
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4">
      <section className="max-h-[90vh] w-full max-w-4xl overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-slate-950">
              Advanced search
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Build multi-condition filters using AND / OR.
            </p>
          </div>
          <button
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50"
            type="button"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[60vh] space-y-3 overflow-auto p-5">
          {draftRules.map((rule, index) => (
            <div
              key={rule.id}
              className="grid gap-3 rounded-lg border border-slate-200 p-3 md:grid-cols-[90px_1fr_1fr_1.4fr_40px]"
            >
              <select
                className="h-10 rounded-lg border border-slate-200 px-2 text-sm text-slate-700 disabled:bg-slate-50"
                disabled={index === 0}
                value={rule.join}
                onChange={(event) =>
                  updateRule(rule.id, { join: event.target.value as QueryJoin })
                }
              >
                <option value="AND">AND</option>
                <option value="OR">OR</option>
              </select>
              <select
                className="h-10 rounded-lg border border-slate-200 px-2 text-sm text-slate-700"
                value={rule.property}
                onChange={(event) =>
                  updateRule(rule.id, {
                    property: event.target.value as QueryProperty,
                    condition:
                      event.target.value === "amount" ? "lt" : "contains"
                  })
                }
              >
                {Object.entries(propertyLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                className="h-10 rounded-lg border border-slate-200 px-2 text-sm text-slate-700"
                value={rule.condition}
                onChange={(event) =>
                  updateRule(rule.id, {
                    condition: event.target.value as QueryCondition
                  })
                }
              >
                <option value="contains">contains</option>
                <option value="equals">=</option>
                <option value="gt">&gt;</option>
                <option value="lt">&lt;</option>
              </select>
              <input
                className="h-10 rounded-lg border border-slate-200 px-3 text-sm text-slate-700 outline-none ring-blue-100 placeholder:text-slate-400 focus:ring-4"
                placeholder="Value"
                value={rule.value}
                onChange={(event) => updateRule(rule.id, { value: event.target.value })}
              />
              <button
                className="inline-flex h-10 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50"
                type="button"
                onClick={() =>
                  setDraftRules((current) =>
                    current.filter((item) => item.id !== rule.id)
                  )
                }
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="flex flex-col gap-3 border-t border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <button
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
            type="button"
            onClick={() =>
              setDraftRules((current) => [
                ...current,
                createRule({ join: "AND", property: "invoice_number", condition: "contains" })
              ])
            }
          >
            <Filter className="h-4 w-4" />
            Add condition
          </button>
          <div className="flex items-center gap-2">
            <button
              className="h-10 rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
              type="button"
              onClick={() => {
                onApply([]);
                onClose();
              }}
            >
              Clear
            </button>
            <button
              className="h-10 rounded-lg bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-800"
              type="button"
              onClick={() => {
                onApply(draftRules);
                onClose();
              }}
            >
              Apply search
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function createRule(
  seed: Pick<QueryRule, "join" | "property" | "condition">
): QueryRule {
  return {
    id: crypto.randomUUID(),
    value: "",
    ...seed
  };
}

function isJsonFile(file: File) {
  return (
    file.type === "application/json" ||
    file.name.toLowerCase().endsWith(".json")
  );
}

function isPdfFile(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function parseInvoiceUpload(data: unknown): {
  payloads: InvoiceCreatePayload[];
  failures: string[];
} {
  const records = Array.isArray(data)
    ? data
    : data && typeof data === "object" && !Array.isArray(data) && "invoices" in data
      ? (data as { invoices?: unknown }).invoices
      : [data];
  if (!Array.isArray(records)) {
    throw new Error('The "invoices" property must be a JSON array.');
  }
  if (records.length === 0) {
    throw new Error("The JSON file does not contain any invoice records.");
  }

  const payloads: InvoiceCreatePayload[] = [];
  const failures: string[] = [];
  records.forEach((record, index) => {
    try {
      payloads.push(normalizeInvoiceRecord(record, index));
    } catch (error) {
      failures.push(
        error instanceof Error ? error.message : `Invoice record ${index + 1} is invalid.`
      );
    }
  });
  return { payloads, failures };
}

function emptyInvoiceBatch(id: string, total: number): InvoiceBatch {
  return {
    id,
    status: "queued",
    total,
    created: 0,
    updated: 0,
    unchanged: 0,
    importFailed: 0,
    classificationFailed: 0,
    failed: 0,
    processed: 0,
    completed: 0,
    queued: total,
    running: 0,
    items: []
  };
}

function storeInvoiceBatch(id: string, total: number) {
  try {
    window.localStorage.setItem(
      ACTIVE_BATCH_STORAGE_KEY,
      JSON.stringify({ id, total } satisfies StoredInvoiceBatch)
    );
  } catch {
    // Progress still works for the current page if storage is unavailable.
  }
}

function readStoredInvoiceBatch(): StoredInvoiceBatch | null {
  try {
    const raw = window.localStorage.getItem(ACTIVE_BATCH_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredInvoiceBatch>;
    if (typeof value.id !== "string" || !value.id.trim()) return null;
    return {
      id: value.id,
      total:
        typeof value.total === "number" && Number.isFinite(value.total)
          ? Math.max(0, value.total)
          : 0
    };
  } catch {
    return null;
  }
}

function clearStoredInvoiceBatch(batchId: string | null) {
  try {
    const stored = readStoredInvoiceBatch();
    if (!stored || stored.id === batchId) {
      window.localStorage.removeItem(ACTIVE_BATCH_STORAGE_KEY);
    }
  } catch {
    // Stale storage is harmless when localStorage cannot be accessed.
  }
}

function validateUploadSizes(jsonFiles: File[], pdfFiles: File[]) {
  const oversizedJson = jsonFiles.find((file) => file.size > MAX_JSON_FILE_SIZE_BYTES);
  if (oversizedJson) {
    throw new Error(`${oversizedJson.name} exceeds the 10 MB JSON file limit.`);
  }

  if (pdfFiles.length > MAX_PDF_FILES_PER_UPLOAD) {
    throw new Error(
      `Select at most ${MAX_PDF_FILES_PER_UPLOAD} PDF files per upload.`
    );
  }

  const oversizedPdf = pdfFiles.find((file) => file.size > MAX_PDF_FILE_SIZE_BYTES);
  if (oversizedPdf) {
    throw new Error(`${oversizedPdf.name} exceeds the 10 MB PDF file limit.`);
  }

  const totalPdfSize = pdfFiles.reduce((total, file) => total + file.size, 0);
  if (totalPdfSize > MAX_TOTAL_PDF_SIZE_BYTES) {
    throw new Error("The selected PDF files exceed the 100 MB total upload limit.");
  }
}

function limitRawTextPreview(
  preview: InvoiceExtractionPreview
): InvoiceExtractionPreview {
  if (preview.raw_text.length <= MAX_RAW_TEXT_PREVIEW_CHARACTERS) return preview;

  return {
    ...preview,
    raw_text: `${preview.raw_text.slice(
      0,
      MAX_RAW_TEXT_PREVIEW_CHARACTERS
    )}\n\n[Extracted text preview truncated in the browser.]`,
    warnings: [
      ...preview.warnings,
      "The extracted text preview was shortened to keep large PDF batches responsive."
    ]
  };
}

function buildBatchUploadFeedback(batch: InvoiceBatch, fallbackTotal: number) {
  const total = batch.total || fallbackTotal;
  const importParts = [
    batch.created > 0 ? `${batch.created} created` : null,
    batch.updated > 0 ? `${batch.updated} updated` : null,
    batch.unchanged > 0 ? `${batch.unchanged} unchanged` : null,
    batch.importFailed > 0 ? `${batch.importFailed} import failed` : null
  ].filter((part): part is string => Boolean(part));
  const classificationParts = [
    batch.completed > 0 ? `${batch.completed} completed` : null,
    batch.classificationFailed > 0
      ? `${batch.classificationFailed} failed`
      : null
  ].filter((part): part is string => Boolean(part));

  if (importParts.length === 0 && classificationParts.length === 0) {
    return `Processed ${total} invoice${total === 1 ? "" : "s"}.`;
  }

  return `Imported ${total} invoice${total === 1 ? "" : "s"}: ${
    importParts.join(", ") || "no import changes"
  }. Classification: ${classificationParts.join(", ") || "not required"}.`;
}

function isTerminalBatch(batch: InvoiceBatch) {
  return batch.status === "completed" || batch.status === "completed_with_errors";
}

function wait(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}

function normalizeInvoiceRecord(
  record: unknown,
  index: number
): InvoiceCreatePayload {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw new Error(`Invoice record ${index + 1} must be a JSON object.`);
  }

  const source = record as Record<string, unknown>;
  const invoiceNumber = readString(source, ["invoice_number", "invoice_id", "id"]);
  const supplierName = readString(source, ["supplier_name", "supplier", "vendor_name"]);
  const invoiceDate = readString(source, ["invoice_date", "date"]);
  const description = readString(source, ["description", "details", "item_description"]);
  const amount = readNumber(source, ["amount", "total_amount", "invoice_amount"]);

  const missingFields = [
    ["invoice_number", invoiceNumber],
    ["supplier_name", supplierName],
    ["invoice_date", invoiceDate],
    ["description", description]
  ]
    .filter(([, value]) => !value)
    .map(([field]) => field);

  if (missingFields.length > 0 || amount === null) {
    throw new Error(
      `Invoice record ${index + 1} is missing required field(s): ${[
        ...missingFields,
        ...(amount === null ? ["amount"] : [])
      ].join(", ")}.`
    );
  }

  return {
    invoice_number: invoiceNumber,
    supplier_name: supplierName,
    buyer_name: readString(source, ["buyer_name", "buyer", "customer_name"]) || undefined,
    invoice_date: invoiceDate,
    amount,
    currency: readString(source, ["currency"]) || "MYR",
    description
  };
}

function readString(source: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return "";
}

function readNumber(source: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value.replace(/,/g, ""));
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return null;
}

function InvoiceExtractionModal({
  item,
  isSaving,
  onClose,
  onConfirm
}: {
  item: ExtractionPreviewItem | null;
  isSaving: boolean;
  onClose: () => void;
  onConfirm: (payload: InvoiceCreatePayload) => void;
}) {
  const [draft, setDraft] = useState<InvoiceCreatePayload>({
    invoice_number: "",
    supplier_name: "",
    buyer_name: "",
    invoice_date: "",
    amount: 0,
    currency: "MYR",
    description: ""
  });

  useEffect(() => {
    if (item) {
      setDraft({
        ...item.preview.invoice,
        buyer_name: item.preview.invoice.buyer_name ?? undefined,
        currency: item.preview.invoice.currency || "MYR"
      });
    }
  }, [item]);

  if (!item) return null;

  const { preview } = item;

  const canImport =
    Boolean(draft.invoice_number.trim()) &&
    Boolean(draft.supplier_name.trim()) &&
    Boolean(draft.invoice_date.trim()) &&
    Boolean(draft.description.trim()) &&
    Number.isFinite(draft.amount) &&
    draft.amount > 0;

  function updateDraft(patch: Partial<InvoiceCreatePayload>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4">
      <section className="max-h-[92vh] w-full max-w-5xl overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-slate-950">
              Confirm extracted invoice
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Extraction method: {preview.method === "pdf_text" ? "PDF text" : preview.method.toUpperCase()}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {item.fileName}
              {item.total > 1 ? ` (${item.position} of ${item.total})` : ""}
            </p>
          </div>
          <button
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={isSaving}
            type="button"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid max-h-[70vh] gap-5 overflow-auto p-5 lg:grid-cols-[1fr_1fr]">
          <div className="space-y-4">
            {preview.warnings.length > 0 ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                <p className="text-sm font-semibold text-amber-900">Review needed</p>
                <ul className="mt-2 space-y-1 text-sm text-amber-800">
                  {preview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-2">
              <InvoiceInput
                label="Invoice number"
                value={draft.invoice_number}
                onChange={(value) => updateDraft({ invoice_number: value })}
              />
              <InvoiceInput
                label="Invoice date"
                type="date"
                value={draft.invoice_date}
                onChange={(value) => updateDraft({ invoice_date: value })}
              />
              <InvoiceInput
                label="Supplier"
                value={draft.supplier_name}
                onChange={(value) => updateDraft({ supplier_name: value })}
              />
              <InvoiceInput
                label="Buyer"
                value={draft.buyer_name ?? ""}
                onChange={(value) => updateDraft({ buyer_name: value || undefined })}
              />
              <InvoiceInput
                label="Amount"
                type="number"
                value={String(draft.amount)}
                onChange={(value) => updateDraft({ amount: Number(value) || 0 })}
              />
              <InvoiceInput
                label="Currency"
                value={draft.currency}
                onChange={(value) => updateDraft({ currency: value || "MYR" })}
              />
            </div>

            <label className="block">
              <span className="text-sm font-medium text-slate-700">Description</span>
              <textarea
                className="mt-1 min-h-24 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none ring-blue-100 focus:ring-4"
                value={draft.description}
                onChange={(event) => updateDraft({ description: event.target.value })}
              />
            </label>
          </div>

          <div>
            <p className="text-sm font-medium text-slate-700">Extracted text</p>
            <textarea
              className="mt-1 h-full min-h-[420px] w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600 outline-none"
              readOnly
              value={preview.raw_text}
            />
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-end">
          <button
            className="h-10 rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
            disabled={isSaving}
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            disabled={isSaving || !canImport}
            type="button"
            onClick={() => onConfirm(draft)}
          >
            <Check className="h-4 w-4" />
            {isSaving ? "Importing" : "Confirm import"}
          </button>
        </div>
      </section>
    </div>
  );
}

function InvoiceInput({
  label,
  value,
  type = "text",
  onChange
}: {
  label: string;
  value: string;
  type?: "text" | "date" | "number";
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <input
        className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700 outline-none ring-blue-100 focus:ring-4"
        type={type}
        step={type === "number" ? "0.01" : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function InvoicesTable({
  invoices,
  deletingId,
  sortState,
  onSelectInvoice,
  onSort,
  onDeleteInvoice
}: {
  invoices: InvoiceRow[];
  deletingId: string | null;
  sortState: SortState;
  onSelectInvoice: (invoice: InvoiceRow) => void;
  onSort: (column: SortColumn) => void;
  onDeleteInvoice: (invoiceId: string) => void;
}) {
  const columns: Array<{ key: SortColumn; label: string }> = [
    { key: "invoice", label: "Invoice" },
    { key: "date", label: "Date" },
    { key: "description", label: "Description" },
    { key: "category", label: "Category" },
    { key: "amount", label: "Amount" },
    { key: "status", label: "Status" }
  ];

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-soft">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px] divide-y divide-slate-200">
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
              <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {invoices.map((invoice) => (
              <tr
                key={invoice.id}
                className="cursor-pointer hover:bg-slate-50"
                onClick={() => onSelectInvoice(invoice)}
              >
                <td className="whitespace-nowrap px-5 py-4">
                  <p className="text-sm font-medium text-slate-950">
                    {invoice.invoice_number}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">{invoice.supplier_name}</p>
                </td>
                <td className="whitespace-nowrap px-5 py-4 text-sm text-slate-600">
                  {formatDate(invoice.invoice_date)}
                </td>
                <td className="max-w-md px-5 py-4 text-sm text-slate-600">
                  <p className="line-clamp-2">{invoice.description}</p>
                </td>
                <td className="whitespace-nowrap px-5 py-4">
                  {invoice.classification ? (
                    <CategoryBadge category={invoice.classification.category} />
                  ) : (
                    <span className="text-sm text-slate-400">Awaiting AI</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-5 py-4 text-sm font-medium text-slate-700">
                  {formatCurrency(invoice.amount, invoice.currency)}
                </td>
                <td className="whitespace-nowrap px-5 py-4">
                  <StatusBadge status={invoice.classification?.status ?? "Pending"} />
                </td>
                <td className="whitespace-nowrap px-5 py-4 text-right">
                  <button
                    className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-red-200 text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:text-slate-300"
                    disabled={deletingId === invoice.id}
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteInvoice(invoice.id);
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </td>
              </tr>
            ))}
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
  column: SortColumn;
  label: string;
  sortState: SortState;
  onSort: (column: SortColumn) => void;
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

function InvoiceDetailModal({
  invoice,
  isReviewing,
  onClose,
  onReview,
  onDelete
}: {
  invoice: InvoiceRow | null;
  isReviewing: boolean;
  onClose: () => void;
  onReview: (invoiceId: string, category: EsgCategory) => void;
  onDelete: (invoiceId: string) => void;
}) {
  const [category, setCategory] = useState<EsgCategory>("Non-ESG");

  useEffect(() => {
    setCategory(invoice?.classification?.category ?? "Non-ESG");
  }, [invoice]);

  if (!invoice) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4">
      <section className="max-h-[92vh] w-full max-w-4xl overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h3 className="text-base font-semibold text-slate-950">
              {invoice.invoice_number}
            </h3>
            <p className="mt-1 text-sm text-slate-500">{invoice.supplier_name}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="inline-flex h-9 items-center gap-2 rounded-lg border border-red-200 px-3 text-sm font-medium text-red-600 hover:bg-red-50"
              type="button"
              onClick={() => onDelete(invoice.id)}
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </button>
            <button
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50"
              type="button"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="grid max-h-[70vh] gap-5 overflow-auto p-5 lg:grid-cols-[1fr_320px]">
          <div className="space-y-4">
            <DetailBlock label="Description" value={invoice.description} />
            <div className="grid gap-3 sm:grid-cols-2">
              <DetailBlock label="Invoice date" value={formatDate(invoice.invoice_date)} />
              <DetailBlock label="Amount" value={formatCurrency(invoice.amount, invoice.currency)} />
              <DetailBlock label="Buyer" value={invoice.buyer_name ?? "-"} />
              <DetailBlock
                label="Processed by LLM"
                value={invoice.processed_by_llm ? "Yes" : "No"}
              />
            </div>

            <div className="rounded-lg border border-slate-200 p-4">
              <h4 className="text-sm font-semibold text-slate-950">
                Evidence and traceability
              </h4>
              <div className="mt-3 space-y-3">
                {invoice.classification?.evidence?.length ? (
                  invoice.classification.evidence.map((item) => (
                    <div key={item.chunk_id} className="rounded-lg bg-slate-50 p-3">
                      <p className="text-xs font-medium text-slate-500">
                        {item.source} - {item.section} - {item.topic}
                      </p>
                      <p className="mt-2 text-sm leading-6 text-slate-700">
                        {item.supporting_text}
                      </p>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">No evidence available.</p>
                )}
              </div>
            </div>
          </div>

          <aside className="rounded-lg border border-slate-200 p-4">
            <h4 className="text-sm font-semibold text-slate-950">Review option</h4>
            <p className="mt-2 text-sm text-slate-500">
              Confirm or correct the AI-generated category before reporting.
            </p>

            <div className="mt-4 space-y-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                  AI category
                </p>
                <div className="mt-2">
                  {invoice.classification ? (
                    <CategoryBadge category={invoice.classification.category} />
                  ) : (
                    <span className="text-sm text-slate-400">Awaiting AI</span>
                  )}
                </div>
              </div>
              <DetailBlock
                label="AI reason"
                value={invoice.classification?.reason ?? "No reason available."}
              />
              <label className="block">
                <span className="text-sm font-medium text-slate-700">
                  Final category
                </span>
                <select
                  className="mt-1 h-10 w-full rounded-lg border border-slate-200 px-3 text-sm text-slate-700 outline-none ring-blue-100 focus:ring-4"
                  value={category}
                  onChange={(event) => setCategory(event.target.value as EsgCategory)}
                >
                  <option value="Environmental">Environmental</option>
                  <option value="Social">Social</option>
                  <option value="Governance">Governance</option>
                  <option value="Non-ESG">Non-ESG</option>
                </select>
              </label>
              <button
                className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                disabled={isReviewing || !invoice.classification}
                type="button"
                onClick={() => onReview(invoice.id, category)}
              >
                <Check className="h-4 w-4" />
                {isReviewing ? "Saving review" : "Save review"}
              </button>
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}

function DetailBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </p>
      <p className="mt-1 text-sm leading-6 text-slate-700">{value}</p>
    </div>
  );
}

function matchesStatusFilter(invoice: InvoiceRow, filter: StatusFilter) {
  if (filter === "all") return true;
  const status = invoice.classification?.status ?? "Pending";
  if (filter === "pending") return status === "Pending";
  return status === "Reviewed";
}

function compareInvoices(
  first: InvoiceRow,
  second: InvoiceRow,
  sortState: SortState
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

function getSortableInvoiceValue(invoice: InvoiceRow, column: SortColumn) {
  if (column === "invoice") return invoice.invoice_number;
  if (column === "date") return invoice.invoice_date;
  if (column === "description") return invoice.description;
  if (column === "category") return invoice.classification?.category ?? "";
  if (column === "status") return invoice.classification?.status ?? "Pending";
  return String(invoice.amount);
}

function matchesRules(invoice: InvoiceRow, rules: QueryRule[]) {
  if (rules.length === 0) return true;

  return rules.reduce((result, rule, index) => {
    const current = matchesRule(invoice, rule);
    if (index === 0) return current;
    return rule.join === "AND" ? result && current : result || current;
  }, true);
}

function matchesRule(invoice: InvoiceRow, rule: QueryRule) {
  const rawValue = getInvoiceValue(invoice, rule.property);
  const needle = rule.value.trim().toLowerCase();

  if (rule.property === "amount") {
    const numberValue = Number(rawValue);
    const compareValue = Number(rule.value);
    if (!Number.isFinite(compareValue)) return true;
    if (rule.condition === "gt") return numberValue > compareValue;
    if (rule.condition === "lt") return numberValue < compareValue;
    return numberValue === compareValue;
  }

  const value = String(rawValue).toLowerCase();
  if (rule.condition === "equals") return value === needle;
  if (rule.condition === "gt") return value > needle;
  if (rule.condition === "lt") return value < needle;
  return value.includes(needle);
}

function getInvoiceValue(invoice: InvoiceRow, property: QueryProperty) {
  if (property === "category") return invoice.classification?.category ?? "";
  if (property === "status") return invoice.classification?.status ?? "Pending";
  return invoice[property];
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
