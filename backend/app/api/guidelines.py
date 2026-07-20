from fastapi import APIRouter

from app.schemas import EvidenceItem
from app.services.rag_service import retrieve_guidelines

router = APIRouter(prefix="/guidelines", tags=["guidelines"])


@router.get("/search", response_model=list[EvidenceItem])
def search_guidelines(query: str, top_k: int = 3) -> list[EvidenceItem]:
    return retrieve_guidelines(query=query, top_k=top_k)
