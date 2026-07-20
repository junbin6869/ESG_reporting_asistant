from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas import (
    InvoiceBatchCreate,
    InvoiceBatchResponse,
    InvoiceCreate,
    InvoiceExtractionPreview,
    InvoiceRow,
)
from app.services.invoice_extraction_service import preview_invoice_upload
from app.services.invoice_service import (
    create_invoice,
    create_invoice_batch,
    delete_invoice,
    get_invoice,
    get_invoice_batch,
    list_invoices,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])
batch_router = APIRouter(prefix="/invoice-batches", tags=["invoice batches"])


@router.get("", response_model=list[InvoiceRow])
def get_invoices() -> list[InvoiceRow]:
    return list_invoices()


@router.post("", response_model=InvoiceRow)
def post_invoice(payload: InvoiceCreate) -> InvoiceRow:
    return create_invoice(payload)


@batch_router.post("", response_model=InvoiceBatchResponse, status_code=202)
def post_invoice_batch(payload: InvoiceBatchCreate) -> InvoiceBatchResponse:
    return create_invoice_batch(payload)


@batch_router.get("/{batch_id}", response_model=InvoiceBatchResponse)
def get_invoice_batch_by_id(batch_id: str) -> InvoiceBatchResponse:
    batch = get_invoice_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Invoice batch not found")
    return batch


@router.post("/extract-preview", response_model=InvoiceExtractionPreview)
async def post_invoice_extract_preview(
    file: UploadFile = File(...)
) -> InvoiceExtractionPreview:
    return await preview_invoice_upload(file)


@router.get("/{invoice_id}", response_model=InvoiceRow)
def get_invoice_by_id(invoice_id: str) -> InvoiceRow:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.delete("/{invoice_id}")
def delete_invoice_by_id(invoice_id: str) -> dict[str, bool]:
    deleted = delete_invoice(invoice_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"deleted": True}
