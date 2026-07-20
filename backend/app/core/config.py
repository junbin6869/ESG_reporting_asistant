import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_ROOT / ".env")


def _path_setting(name: str, default: Path, *, relative_to: Path) -> Path:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    path = Path(raw_value).expanduser()
    return path if path.is_absolute() else relative_to / path


def _int_setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_setting(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _language_setting(name: str, default: str) -> str:
    """Normalize Tesseract's ``lang_a+lang_b`` environment format."""
    raw_value = os.getenv(name, default)
    languages = [value.strip() for value in raw_value.split("+") if value.strip()]
    return "+".join(languages) or default


class Settings:
    app_name = "ESG Assistant API"
    database_path = _path_setting(
        "DATABASE_PATH",
        BACKEND_ROOT / "data" / "esg_assistant.db",
        relative_to=BACKEND_ROOT,
    )
    langgraph_checkpoint_path = _path_setting(
        "LANGGRAPH_CHECKPOINT_PATH",
        BACKEND_ROOT / "data" / "langgraph_checkpoints.db",
        relative_to=BACKEND_ROOT,
    )
    chroma_db_dir = _path_setting(
        "CHROMA_DB_DIR",
        PROJECT_ROOT / "chroma_db",
        relative_to=PROJECT_ROOT,
    )
    guideline_pdf_dir = _path_setting(
        "GUIDELINE_PDF_DIR",
        PROJECT_ROOT / "PDF",
        relative_to=PROJECT_ROOT,
    )
    chroma_collection = os.getenv("CHROMA_COLLECTION", "esg_docs")
    embedding_model = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    embedding_device = os.getenv("EMBEDDING_DEVICE", "").strip()
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds = _float_setting("LLM_TIMEOUT_SECONDS", 60.0)
    llm_max_retries = _int_setting("LLM_MAX_RETRIES", 2)
    classification_worker_count = max(
        1,
        min(_int_setting("CLASSIFICATION_WORKER_COUNT", 3), 8),
    )
    rag_search_type = os.getenv("RAG_SEARCH_TYPE", "similarity").strip().lower()
    rag_score_threshold = _float_setting("RAG_SCORE_THRESHOLD", 0.25)
    rag_max_top_k = _int_setting("RAG_MAX_TOP_K", 20)
    rag_mmr_fetch_k = _int_setting("RAG_MMR_FETCH_K", 12)
    rag_mmr_lambda = _float_setting("RAG_MMR_LAMBDA", 0.5)
    frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
    tesseract_cmd = os.getenv("TESSERACT_CMD", "")
    ocr_languages = _language_setting("OCR_LANGUAGES", "eng")


settings = Settings()
