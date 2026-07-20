import json
from functools import lru_cache
from typing import Literal

from fastapi import HTTPException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.llm import get_chat_model
from app.schemas import EvidenceItem, InvoiceClassification
from app.services.invoice_service import get_invoice, save_classification
from app.services.rag_service import retrieve_guidelines


class ClassificationDecision(BaseModel):
    """Provider-enforced structured response for an invoice classification."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["Environmental", "Social", "Governance", "Non-ESG"]
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(min_length=1, max_length=300)
    evidence_chunk_ids: list[str]


CLASSIFICATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are an ESG reporting assistant specialized in e-invoice analysis.
You are not a general chatbot.

Classify the invoice into exactly one category: Environmental, Social,
Governance, or Non-ESG.

Use only the retrieved ESG guideline evidence. If the invoice is not clearly
supported by that evidence, classify it as Non-ESG. Keep the reason to one
short sentence. Only cite chunk IDs present in the retrieved evidence.""",
        ),
        (
            "human",
            """Invoice:
{invoice_json}

Retrieved ESG guideline evidence:
{evidence_json}""",
        ),
    ]
)


def _prompt_values(
    invoice: dict,
    evidence: list[EvidenceItem],
) -> dict[str, str]:
    return {
        "invoice_json": json.dumps(invoice, ensure_ascii=False, indent=2),
        "evidence_json": json.dumps(
            [item.model_dump() for item in evidence],
            ensure_ascii=False,
            indent=2,
        ),
    }


def build_classification_prompt(invoice: dict, evidence: list[EvidenceItem]) -> str:
    """Render the chat prompt for audit/debug compatibility."""
    messages = CLASSIFICATION_PROMPT.format_messages(**_prompt_values(invoice, evidence))
    return "\n\n".join(str(message.content) for message in messages)


@lru_cache(maxsize=4)
def get_classification_chain(model_name: str | None = None):
    model = get_chat_model(model_name or settings.llm_model, temperature=0.0)
    structured_model = model.with_structured_output(
        ClassificationDecision,
        method="json_schema",
        strict=True,
    )
    return CLASSIFICATION_PROMPT | structured_model


def classify_invoice(invoice_id: str) -> InvoiceClassification:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    evidence = retrieve_guidelines(invoice.description, top_k=3)
    invoice_payload = invoice.model_dump(exclude={"classification"})

    try:
        raw_decision = get_classification_chain(settings.llm_model).invoke(
            _prompt_values(invoice_payload, evidence)
        )
        decision = (
            raw_decision
            if isinstance(raw_decision, ClassificationDecision)
            else ClassificationDecision.model_validate(raw_decision)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="LLM classification failed or returned invalid structured output",
        ) from exc

    selected_ids = set(decision.evidence_chunk_ids)
    selected_evidence = [
        item for item in evidence if item.chunk_id in selected_ids
    ]
    classification = InvoiceClassification(
        category=decision.category,
        status="Pending",
        confidence=decision.confidence,
        reason=decision.reason,
        evidence=selected_evidence,
    )
    return save_classification(
        invoice_id=invoice.id,
        classification=classification,
        model_name=settings.llm_model,
        prompt_version="classification_langchain_v2",
    )


def classify_unprocessed_invoices() -> None:
    from app.services.invoice_service import list_unprocessed_invoice_ids

    for invoice_id in list_unprocessed_invoice_ids():
        try:
            classify_invoice(invoice_id)
        except Exception as exc:
            print(f"[WARN] Failed to auto-classify invoice {invoice_id}: {exc}")


def clear_classification_chain_cache() -> None:
    get_classification_chain.cache_clear()
