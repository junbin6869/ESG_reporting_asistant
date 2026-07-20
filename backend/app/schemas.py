from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EsgCategory = Literal["Environmental", "Social", "Governance", "Non-ESG"]
ClassificationStatus = Literal["Pending", "Reviewed"]
Confidence = Literal["high", "medium", "low"]
InvoiceUpsertStatus = Literal["created", "updated", "unchanged", "failed"]
ClassificationJobStatus = Literal["queued", "running", "completed", "failed"]
InvoiceBatchStatus = Literal["processing", "completed", "completed_with_errors"]


class EvidenceItem(BaseModel):
    chunk_id: str
    source: str
    section: str
    topic: str
    page: str | int | None = None
    supporting_text: str


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    invoice_number: str = Field(min_length=1)
    supplier_name: str = Field(min_length=1)
    buyer_name: str | None = None
    invoice_date: str = Field(min_length=1)
    amount: float = Field(ge=0)
    currency: str = "MYR"
    description: str = Field(min_length=1)

    @field_validator("invoice_date")
    @classmethod
    def validate_invoice_date(cls, value: str) -> str:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("invoice_date must use YYYY-MM-DD format") from exc

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ISO code")
        return normalized


class InvoiceExtractionDraft(BaseModel):
    invoice_number: str = ""
    supplier_name: str = ""
    buyer_name: str | None = None
    invoice_date: str = ""
    amount: float = 0
    currency: str = "MYR"
    description: str = ""


class InvoiceExtractionPreview(BaseModel):
    method: Literal["pdf_text", "ocr", "unavailable"]
    raw_text: str
    invoice: InvoiceExtractionDraft
    warnings: list[str] = Field(default_factory=list)


class InvoiceClassification(BaseModel):
    category: EsgCategory
    status: ClassificationStatus
    confidence: Confidence = "medium"
    reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class InvoiceRow(BaseModel):
    id: str
    invoice_number: str
    supplier_name: str
    buyer_name: str | None = None
    invoice_date: str
    amount: float
    currency: str
    description: str
    version: int = 1
    updated_at: str | None = None
    processed_by_llm: bool = False
    classification_status: ClassificationJobStatus | None = None
    classification: InvoiceClassification | None = None


class InvoiceBatchCreate(BaseModel):
    # Validate each item in the service so one malformed invoice can be reported
    # without rejecting the other records in a large batch.
    invoices: list[Any] = Field(min_length=1, max_length=1000)


class InvoiceBatchItemResult(BaseModel):
    position: int
    invoice_number: str
    invoice_id: str | None = None
    invoice_version: int | None = None
    upsert_status: InvoiceUpsertStatus
    classification_status: ClassificationJobStatus
    error: str | None = None


class BatchClassificationProgress(BaseModel):
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0


class InvoiceBatchResponse(BaseModel):
    batch_id: str
    status: InvoiceBatchStatus
    total_count: int
    created_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    failed_count: int = 0
    classification: BatchClassificationProgress
    items: list[InvoiceBatchItemResult]
    created_at: str
    updated_at: str


class ReviewClassificationRequest(BaseModel):
    category: EsgCategory
    reason: str | None = None


class SummaryBucket(BaseModel):
    count: int
    amount: float
    currency: str
    amounts_by_currency: dict[str, float] = Field(default_factory=dict)


class DashboardSummary(BaseModel):
    period: dict[str, str]
    total_invoices: SummaryBucket
    categories: dict[EsgCategory, SummaryBucket]
    unclassified_count: int = 0


class GenerateReportRequest(BaseModel):
    period_start: str
    period_end: str
    title: str

    @field_validator("period_start", "period_end")
    @classmethod
    def validate_period_date(cls, value: str) -> str:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("reporting periods must use YYYY-MM-DD format") from exc

    @model_validator(mode="after")
    def validate_period_order(self):
        if self.period_start > self.period_end:
            raise ValueError("period_start must not be after period_end")
        return self


class GeneratedReport(BaseModel):
    id: str
    title: str
    period_start: str
    period_end: str
    status: Literal["Draft", "Final", "Archived"]
    content: str
    created_at: str
    version_count: int = 1


class AgentStep(BaseModel):
    id: str
    label: str
    status: Literal["queued", "running", "completed", "failed"]
    detail: str | None = None


class AgentRun(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    request: GenerateReportRequest
    steps: list[AgentStep]
    report: GeneratedReport | None = None
    error: str | None = None
    created_at: str
    updated_at: str
