from fastapi import APIRouter, HTTPException

from app.schemas import InvoiceClassification, ReviewClassificationRequest
from app.services.classification_service import classify_invoice
from app.services.invoice_service import review_classification

router = APIRouter(prefix="/classifications", tags=["classifications"])


@router.post("/run/{invoice_id}", response_model=InvoiceClassification)
def run_classification(invoice_id: str) -> InvoiceClassification:
    return classify_invoice(invoice_id)


@router.post("/review/{invoice_id}", response_model=InvoiceClassification)
def review(invoice_id: str, payload: ReviewClassificationRequest) -> InvoiceClassification:
    try:
        return review_classification(invoice_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
