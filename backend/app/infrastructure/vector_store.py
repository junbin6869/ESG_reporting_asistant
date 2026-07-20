import os
from pathlib import Path

_LOCAL_EMBEDDINGS_ONLY = os.getenv(
    "EMBEDDING_LOCAL_FILES_ONLY",
    "true",
).strip().lower() not in {"0", "false", "no"}
if _LOCAL_EMBEDDINGS_ONLY:
    # Hugging Face reads these flags while its modules are imported. Setting
    # them here avoids long network retries in offline/local deployments.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from chromadb.config import Settings as ChromaClientSettings
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


def create_embeddings(
    model_name: str,
    *,
    device: str = "",
    local_files_only: bool | None = None,
) -> HuggingFaceEmbeddings:
    """Create the local embedding adapter used by indexing and retrieval."""
    model_kwargs = {"device": device} if device else {}
    if local_files_only is None:
        local_files_only = _LOCAL_EMBEDDINGS_ONLY
    model_kwargs["local_files_only"] = local_files_only
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs=model_kwargs,
        encode_kwargs={"normalize_embeddings": False},
    )


def create_chroma_vector_store(
    *,
    db_dir: str | Path,
    collection_name: str,
    embeddings: HuggingFaceEmbeddings,
) -> Chroma:
    """Open a persistent LangChain Chroma store without changing its metric.

    The existing project collection was created with Chroma's default metric
    and unnormalised MiniLM vectors. Not supplying collection metadata keeps
    old vectors query-compatible while new documents use the same embedding
    behaviour.
    """
    persist_directory = Path(db_dir)
    persist_directory.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=collection_name,
        persist_directory=str(persist_directory),
        embedding_function=embeddings,
        client_settings=ChromaClientSettings(anonymized_telemetry=False),
    )
