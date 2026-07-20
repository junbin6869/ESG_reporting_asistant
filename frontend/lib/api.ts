export type EsgCategory =
  | "Environmental"
  | "Social"
  | "Governance"
  | "Non-ESG";

export type ClassificationStatus = "Pending" | "Reviewed";

export type DashboardSummary = {
  period: {
    label: string;
    start: string;
    end: string;
  };
  total_invoices: {
    count: number;
    amount: number;
    currency: string;
    amounts_by_currency?: Record<string, number>;
  };
  categories: Record<
    EsgCategory,
    {
      count: number;
      amount: number;
      currency: string;
      amounts_by_currency?: Record<string, number>;
    }
  >;
};

export type InvoiceClassification = {
  category: EsgCategory;
  status: ClassificationStatus;
  confidence?: "high" | "medium" | "low";
  reason?: string;
  evidence?: EvidenceItem[];
};

export type InvoiceRow = {
  id: string;
  invoice_number: string;
  supplier_name: string;
  buyer_name?: string;
  invoice_date: string;
  amount: number;
  currency: string;
  description: string;
  processed_by_llm: boolean;
  classification: InvoiceClassification | null;
};

export type EvidenceItem = {
  chunk_id: string;
  source: string;
  section: string;
  topic: string;
  page?: string | number;
  supporting_text: string;
};

export type InvoiceCreatePayload = {
  invoice_number: string;
  supplier_name: string;
  buyer_name?: string;
  invoice_date: string;
  amount: number;
  currency: string;
  description: string;
};

export type InvoiceBatchStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors";

export type InvoiceBatchItem = {
  id?: string;
  invoice_number?: string;
  status?: string;
  error?: string | null;
};

export type InvoiceBatch = {
  id: string | null;
  status: InvoiceBatchStatus;
  total: number;
  created: number;
  updated: number;
  unchanged: number;
  importFailed: number;
  classificationFailed: number;
  failed: number;
  processed: number;
  completed: number;
  queued: number;
  running: number;
  items: InvoiceBatchItem[];
};

export type InvoiceExtractionPreview = {
  method: "pdf_text" | "ocr" | "unavailable";
  raw_text: string;
  invoice: {
    invoice_number: string;
    supplier_name: string;
    buyer_name?: string | null;
    invoice_date: string;
    amount: number;
    currency: string;
    description: string;
  };
  warnings: string[];
};

export type GenerateReportPayload = {
  period_start: string;
  period_end: string;
  title: string;
};

export type ReviewClassificationPayload = {
  category: EsgCategory;
  reason?: string;
};

export type GeneratedReport = {
  id: string;
  title: string;
  period_start: string;
  period_end: string;
  status: "Draft" | "Final" | "Archived";
  content: string;
  created_at: string;
  version_count?: number;
};

export type AgentStep = {
  id: string;
  label: string;
  status: "queued" | "running" | "completed" | "failed";
  detail?: string | null;
};

export type AgentRun = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  request: GenerateReportPayload;
  steps: AgentStep[];
  report: GeneratedReport | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "application/json"
    },
    cache: "no-store"
  });

  if (!response.ok) {
    throw await buildApiError(response);
  }

  return response.json() as Promise<T>;
}

async function sendJson<TResponse, TBody extends object>(
  path: string,
  body: TBody
): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    throw await buildApiError(response);
  }

  return response.json() as Promise<TResponse>;
}

export async function getDashboardSummary(year = "2026") {
  return fetchJson<DashboardSummary>(
    `/summaries?period_start=${year}-01-01&period_end=${year}-12-31`
  );
}

export async function getRecentInvoices() {
  return fetchJson<InvoiceRow[]>("/invoices");
}

export async function createInvoice(payload: InvoiceCreatePayload) {
  return sendJson<InvoiceRow, InvoiceCreatePayload>("/invoices", payload);
}

export async function createInvoiceBatch(payloads: InvoiceCreatePayload[]) {
  const response = await sendJson<unknown, { invoices: InvoiceCreatePayload[] }>(
    "/invoice-batches",
    { invoices: payloads }
  );

  return normalizeInvoiceBatch(response, payloads.length);
}

export async function getInvoiceBatch(batchId: string, fallbackTotal = 0) {
  const response = await fetchJson<unknown>(
    `/invoice-batches/${encodeURIComponent(batchId)}`
  );

  return normalizeInvoiceBatch(response, fallbackTotal, batchId);
}

export async function extractInvoicePreview(file: File) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/invoices/extract-preview`, {
    method: "POST",
    headers: {
      Accept: "application/json"
    },
    body: formData
  });

  if (!response.ok) {
    throw await buildApiError(response);
  }

  return response.json() as Promise<InvoiceExtractionPreview>;
}

export async function runInvoiceClassification(invoiceId: string) {
  return sendJson<InvoiceClassification, Record<string, never>>(
    `/classifications/run/${invoiceId}`,
    {}
  );
}

export async function reviewClassification(
  invoiceId: string,
  payload: ReviewClassificationPayload
) {
  return sendJson<InvoiceClassification, ReviewClassificationPayload>(
    `/classifications/review/${invoiceId}`,
    payload
  );
}

export async function startAgenticReport(payload: GenerateReportPayload) {
  return sendJson<AgentRun, GenerateReportPayload>("/reports/generate-agentic", payload);
}

export async function getAgentRun(runId: string) {
  return fetchJson<AgentRun>(`/reports/agent-runs/${runId}`);
}

export async function getReports() {
  return fetchJson<GeneratedReport[]>("/reports");
}

export async function deleteInvoice(invoiceId: string) {
  const response = await fetch(`${API_BASE_URL}/invoices/${invoiceId}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    throw await buildApiError(response);
  }
}

export async function deleteReport(reportId: string) {
  const response = await fetch(`${API_BASE_URL}/reports/${reportId}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    throw await buildApiError(response);
  }
}

export async function downloadReportPdf(report: GeneratedReport) {
  const response = await fetch(`${API_BASE_URL}/reports/${report.id}/pdf`, {
    headers: {
      Accept: "application/pdf"
    }
  });

  if (!response.ok) {
    throw await buildApiError(response);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${report.title.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.pdf`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function normalizeInvoiceBatch(
  value: unknown,
  fallbackTotal: number,
  fallbackId: string | null = null
): InvoiceBatch {
  const source = isRecord(value) ? value : {};
  const classification = isRecord(source.classification)
    ? source.classification
    : source;
  const rawItems = Array.isArray(source.items) ? source.items : [];
  const items = rawItems.map(normalizeInvoiceBatchItem);
  const itemFailureCount = items.filter(
    (item) => item.status?.toLowerCase() === "failed" || Boolean(item.error)
  ).length;

  const id = readOptionalString(source, ["id", "batch_id", "batchId"]) ?? fallbackId;
  const total = readNonNegativeNumber(source, ["total", "total_count"], fallbackTotal);
  const created = readNonNegativeNumber(source, ["created", "created_count"], 0);
  const updated = readNonNegativeNumber(source, ["updated", "updated_count"], 0);
  const unchanged = readNonNegativeNumber(
    source,
    ["unchanged", "unchanged_count", "skipped"],
    0
  );
  const importFailed = readNonNegativeNumber(
    source,
    ["failed", "failed_count"],
    0
  );
  const classificationProgressFailed = readNonNegativeNumber(
    classification,
    ["failed", "failed_count"],
    0
  );
  const classificationFailed = Math.max(
    classificationProgressFailed - importFailed,
    0
  );
  const failed = Math.max(
    importFailed + classificationFailed,
    itemFailureCount
  );
  const completed = readNonNegativeNumber(
    classification,
    ["completed", "completed_count", "succeeded"],
    0
  );
  const explicitProcessed =
    readOptionalNonNegativeNumber(source, ["processed", "processed_count"]) ??
    readOptionalNonNegativeNumber(classification, ["processed", "processed_count"]);
  const running = readNonNegativeNumber(
    classification,
    ["running", "running_count"],
    0
  );
  const explicitQueued = readOptionalNonNegativeNumber(classification, [
    "queued",
    "queued_count",
    "pending"
  ]);

  const provisionalProcessed = Math.min(
    total || Number.MAX_SAFE_INTEGER,
    explicitProcessed ?? completed + classificationProgressFailed
  );
  const rawStatus = readOptionalString(source, ["status"]);
  const status = normalizeInvoiceBatchStatus(
    rawStatus,
    total,
    provisionalProcessed,
    running,
    failed
  );
  const processed = isTerminalInvoiceBatchStatus(status)
    ? Math.max(provisionalProcessed, total)
    : provisionalProcessed;
  const queued =
    explicitQueued ?? Math.max(total - Math.min(total, processed + running), 0);

  return {
    id,
    status,
    total,
    created,
    updated,
    unchanged,
    importFailed,
    classificationFailed,
    failed,
    processed,
    completed,
    queued,
    running,
    items
  };
}

function normalizeInvoiceBatchItem(value: unknown): InvoiceBatchItem {
  if (!isRecord(value)) return {};

  return {
    id: readOptionalString(value, ["id", "invoice_id", "job_id"]),
    invoice_number: readOptionalString(value, [
      "invoice_number",
      "invoice_no",
      "invoice_id"
    ]),
    status: readOptionalString(value, [
      "status",
      "classification_status",
      "state",
      "upsert_status"
    ]),
    error: readOptionalString(value, ["error", "message", "detail"])
  };
}

function normalizeInvoiceBatchStatus(
  value: string | undefined,
  total: number,
  processed: number,
  running: number,
  failed: number
): InvoiceBatchStatus {
  const status = value?.trim().toLowerCase();
  if (status === "completed" || status === "complete" || status === "succeeded") {
    return failed > 0 ? "completed_with_errors" : "completed";
  }
  if (
    status === "completed_with_errors" ||
    status === "partial" ||
    status === "failed"
  ) {
    return "completed_with_errors";
  }
  if (status === "running" || status === "processing" || status === "in_progress") {
    return "running";
  }
  if (status === "queued" || status === "pending") return "queued";

  if (total > 0 && processed >= total) {
    return failed > 0 ? "completed_with_errors" : "completed";
  }
  return running > 0 || processed > 0 ? "running" : "queued";
}

function isTerminalInvoiceBatchStatus(status: InvoiceBatchStatus) {
  return status === "completed" || status === "completed_with_errors";
}

async function buildApiError(response: Response) {
  let detail = "";
  try {
    const rawBody = await response.text();
    if (rawBody.trim()) {
      try {
        const body = JSON.parse(rawBody) as unknown;
        detail = describeApiDetail(
          isRecord(body) && "detail" in body ? body.detail : body
        );
      } catch {
        detail = rawBody;
      }
    }
  } catch {
    // The status code remains useful when the response body cannot be read.
  }

  const compactDetail = detail.replace(/\s+/g, " ").trim().slice(0, 800);
  return new Error(
    `API request failed with ${response.status}${
      compactDetail ? `: ${compactDetail}` : ""
    }`
  );
}

function describeApiDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map(describeApiDetail).filter(Boolean).join("; ");
  }
  if (isRecord(value)) {
    const message = value.msg ?? value.message ?? value.detail;
    const location = Array.isArray(value.loc) ? value.loc.join(".") : "";
    const description = describeApiDetail(message);
    if (description) return location ? `${location}: ${description}` : description;
    try {
      return JSON.stringify(value);
    } catch {
      return "";
    }
  }
  return value == null ? "" : String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readOptionalString(source: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return undefined;
}

function readOptionalNonNegativeNumber(
  source: Record<string, unknown>,
  keys: string[]
) {
  for (const key of keys) {
    const value = source[key];
    const parsed = typeof value === "number" ? value : Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) return Math.floor(parsed);
  }
  return undefined;
}

function readNonNegativeNumber(
  source: Record<string, unknown>,
  keys: string[],
  fallback: number
) {
  return readOptionalNonNegativeNumber(source, keys) ?? fallback;
}
