from __future__ import annotations

import hashlib
from functools import lru_cache
from threading import RLock
from typing import Literal

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.core.config import settings
from app.infrastructure.vector_store import (
    create_chroma_vector_store,
    create_embeddings,
)
from app.schemas import EvidenceItem


SearchType = Literal["similarity", "mmr", "similarity_score_threshold"]
VALID_SEARCH_TYPES: set[str] = {
    "similarity",
    "mmr",
    "similarity_score_threshold",
}
_rag_init_lock = RLock()


@lru_cache(maxsize=1)
def _get_embeddings_cached():
    return create_embeddings(
        settings.embedding_model,
        device=settings.embedding_device,
    )


@lru_cache(maxsize=1)
def _get_vector_store_cached():
    return create_chroma_vector_store(
        db_dir=settings.chroma_db_dir,
        collection_name=settings.chroma_collection,
        embeddings=get_embeddings(),
    )


def get_embeddings():
    """Initialize the embedding model once, even under concurrent first use."""
    with _rag_init_lock:
        return _get_embeddings_cached()


def get_vector_store():
    """Initialize Chroma once, even when several workers start together."""
    with _rag_init_lock:
        return _get_vector_store_cached()


def _effective_top_k(top_k: int) -> int:
    if top_k < 1:
        return 0
    return min(top_k, max(1, settings.rag_max_top_k))


def get_guideline_retriever(
    *,
    top_k: int = 3,
    search_type: SearchType | str | None = None,
    score_threshold: float | None = None,
) -> BaseRetriever:
    """Build a standard LangChain retriever over the ESG guideline store."""
    effective_top_k = _effective_top_k(top_k)
    if effective_top_k == 0:
        raise ValueError("top_k must be at least 1")

    effective_search_type = (search_type or settings.rag_search_type).strip().lower()
    if effective_search_type not in VALID_SEARCH_TYPES:
        valid = ", ".join(sorted(VALID_SEARCH_TYPES))
        raise ValueError(f"Unsupported search_type '{effective_search_type}'. Use: {valid}")

    search_kwargs: dict[str, float | int] = {"k": effective_top_k}
    if effective_search_type == "mmr":
        search_kwargs.update(
            {
                "fetch_k": max(settings.rag_mmr_fetch_k, effective_top_k * 4),
                "lambda_mult": min(1.0, max(0.0, settings.rag_mmr_lambda)),
            }
        )
    elif effective_search_type == "similarity_score_threshold":
        threshold = (
            settings.rag_score_threshold
            if score_threshold is None
            else score_threshold
        )
        search_kwargs["score_threshold"] = min(1.0, max(0.0, threshold))

    return get_vector_store().as_retriever(
        search_type=effective_search_type,
        search_kwargs=search_kwargs,
    )


def _chunk_id(document: Document) -> str:
    metadata = document.metadata
    if metadata.get("chunk_id"):
        return str(metadata["chunk_id"])
    if document.id:
        return str(document.id)
    if metadata.get("source") and metadata.get("chunk_index") is not None:
        return f"{metadata['source']}_c{metadata['chunk_index']}"
    digest = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"


def _page_reference(metadata: dict) -> str | int | None:
    page_start = metadata.get("page_start", metadata.get("page"))
    page_end = metadata.get("page_end", page_start)
    if page_start in (None, "", "N/A"):
        return None
    if page_end in (None, "", "N/A") or page_end == page_start:
        return page_start
    return f"{page_start}-{page_end}"


def document_to_evidence(document: Document) -> EvidenceItem:
    """Map LangChain's generic Document into the public API contract."""
    metadata = document.metadata
    return EvidenceItem(
        chunk_id=_chunk_id(document),
        source=str(metadata.get("source") or ""),
        section=str(metadata.get("section_title") or metadata.get("section") or ""),
        topic=str(metadata.get("topic") or ""),
        page=_page_reference(metadata),
        supporting_text=document.page_content,
    )


def retrieve_documents(
    query: str,
    top_k: int = 3,
    *,
    search_type: SearchType | str | None = None,
    score_threshold: float | None = None,
) -> list[Document]:
    cleaned_query = query.strip()
    if not cleaned_query or _effective_top_k(top_k) == 0:
        return []
    retriever = get_guideline_retriever(
        top_k=top_k,
        search_type=search_type,
        score_threshold=score_threshold,
    )
    return list(retriever.invoke(cleaned_query))


def retrieve_guidelines(
    query: str,
    top_k: int = 3,
    *,
    search_type: SearchType | str | None = None,
    score_threshold: float | None = None,
) -> list[EvidenceItem]:
    """Retrieve ESG evidence while preserving the existing tool/API shape."""
    return [
        document_to_evidence(document)
        for document in retrieve_documents(
            query,
            top_k=top_k,
            search_type=search_type,
            score_threshold=score_threshold,
        )
    ]


def get_guideline_document_by_id(chunk_id: str) -> Document | None:
    """Fetch one guideline chunk by its exact Chroma ID without vector search."""
    cleaned_chunk_id = chunk_id.strip()
    if not cleaned_chunk_id:
        return None

    result = get_vector_store().get(
        ids=[cleaned_chunk_id],
        include=["documents", "metadatas"],
    )
    ids = [str(item) for item in result.get("ids") or []]
    if cleaned_chunk_id not in ids:
        return None

    index = ids.index(cleaned_chunk_id)
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    page_content = documents[index] if index < len(documents) else ""
    metadata = metadatas[index] if index < len(metadatas) else {}
    return Document(
        id=cleaned_chunk_id,
        page_content=str(page_content or ""),
        metadata=dict(metadata or {}),
    )


async def aretrieve_guidelines(
    query: str,
    top_k: int = 3,
    *,
    search_type: SearchType | str | None = None,
    score_threshold: float | None = None,
) -> list[EvidenceItem]:
    cleaned_query = query.strip()
    if not cleaned_query or _effective_top_k(top_k) == 0:
        return []
    retriever = get_guideline_retriever(
        top_k=top_k,
        search_type=search_type,
        score_threshold=score_threshold,
    )
    documents = await retriever.ainvoke(cleaned_query)
    return [document_to_evidence(document) for document in documents]


def clear_rag_caches() -> None:
    with _rag_init_lock:
        _get_vector_store_cached.cache_clear()
        _get_embeddings_cached.cache_clear()
