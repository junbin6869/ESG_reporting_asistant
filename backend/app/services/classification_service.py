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


class ClassificationQueryPlan(BaseModel):
    """LLM-generated ESG search queries for one invoice.

    The original invoice description is always retained separately as the
    primary query, so query rewriting can improve recall without becoming a
    single point of failure.
    """

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1, max_length=2)


QUERY_REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """Generate up to two short semantic search queries for retrieving
ESG reporting-guideline evidence about an invoice. Use only facts stated in the
invoice. Do not classify the invoice, assign an ESG category, or invent ESG
claims. Prefer concise English phrases that connect the purchased product or
service to possible guideline terminology. If the invoice is vague or not ESG
specific, return a neutral query based on its description.""",
        ),
        (
            "human",
            """Invoice:
{invoice_json}""",
        ),
    ]
)


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


def _invoice_query_value(invoice: dict) -> dict[str, str]:
    return {"invoice_json": json.dumps(invoice, ensure_ascii=False, indent=2)}


def build_query_rewrite_prompt(invoice: dict) -> str:
    """Render the query-rewrite prompt for audit/debug compatibility."""
    messages = QUERY_REWRITE_PROMPT.format_messages(**_invoice_query_value(invoice))
    return "\n\n".join(str(message.content) for message in messages)


@lru_cache(maxsize=4)
def get_query_rewrite_chain(model_name: str | None = None):
    model = get_chat_model(model_name or settings.llm_model, temperature=0.0)
    structured_model = model.with_structured_output(
        ClassificationQueryPlan,
        method="json_schema",
        strict=True,
    )
    return QUERY_REWRITE_PROMPT | structured_model


@lru_cache(maxsize=4)
def get_classification_chain(model_name: str | None = None):
    model = get_chat_model(model_name or settings.llm_model, temperature=0.0)
    structured_model = model.with_structured_output(
        ClassificationDecision,
        method="json_schema",
        strict=True,
    )
    return CLASSIFICATION_PROMPT | structured_model


def build_retrieval_queries(invoice: dict) -> list[str]:
    """Combine the original description with LLM-expanded ESG search queries.

    A failed rewrite deliberately falls back to the original description. This
    keeps classification available when a transient LLM error occurs.
    """
    original_query = str(invoice.get("description") or "").strip()
    if not original_query:
        return []

    queries = [original_query]
    try:
        raw_plan = get_query_rewrite_chain(settings.llm_model).invoke(
            _invoice_query_value(invoice)
        )
        plan = (
            raw_plan
            if isinstance(raw_plan, ClassificationQueryPlan)
            else ClassificationQueryPlan.model_validate(raw_plan)
        )
    except Exception:
        return queries

    seen = {original_query.casefold()}
    for query in plan.queries:
        cleaned_query = query.strip()
        normalized_query = cleaned_query.casefold()
        if cleaned_query and normalized_query not in seen:
            queries.append(cleaned_query)
            seen.add(normalized_query)
    return queries


def retrieve_classification_evidence(invoice: dict) -> list[EvidenceItem]:
    """Retrieve and de-duplicate evidence from original and rewritten queries."""
    evidence: list[EvidenceItem] = []
    seen_chunk_ids: set[str] = set()
    for query in build_retrieval_queries(invoice):
        for item in retrieve_guidelines(query, top_k=3):
            if item.chunk_id not in seen_chunk_ids:
                evidence.append(item)
                seen_chunk_ids.add(item.chunk_id)
    return evidence


def classify_invoice(invoice_id: str) -> InvoiceClassification:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    invoice_payload = invoice.model_dump(exclude={"classification"})
    evidence = retrieve_classification_evidence(invoice_payload)

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
        prompt_version="classification_langchain_v3_query_rewrite",
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
    get_query_rewrite_chain.cache_clear()
