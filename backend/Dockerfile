# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ARG TESSERACT_LANGUAGE_PACKAGES="tesseract-ocr-eng"
ARG PRELOAD_EMBEDDING_MODEL="true"
ARG EMBEDDING_MODEL_ID="sentence-transformers/all-MiniLM-L6-v2"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/home/appuser/.cache/huggingface \
    EMBEDDING_MODEL=/opt/models/default-embedding \
    EMBEDDING_LOCAL_FILES_ONLY=true \
    OCR_LANGUAGES=eng \
    DATABASE_PATH=/app/data/esg_assistant.db \
    LANGGRAPH_CHECKPOINT_PATH=/app/data/langgraph_checkpoints.db \
    CHROMA_DB_DIR=/app/chroma_db \
    GUIDELINE_PDF_DIR=/app/PDF

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        libgomp1 \
        tesseract-ocr \
        ${TESSERACT_LANGUAGE_PACKAGES} \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app/backend

# Keeping dependency installation before the source copy lets Docker reuse this
# expensive layer until requirements change. Install and constrain CPU-only
# PyTorch separately so sentence-transformers cannot pull multi-gigabyte CUDA
# libraries into this CPU deployment. The cache mount is not retained in the
# final image.
COPY requirements.txt requirements-cpu.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        --requirement requirements-cpu.txt \
    && python -m pip install \
        --constraint requirements-cpu.txt \
        --requirement requirements.txt \
    && python -m pip check \
    && python -c "import torch; assert torch.__version__ == '2.13.0+cpu', torch.__version__; assert torch.version.cuda is None, torch.version.cuda"

# The default image contains its embedding model outside HF_HOME. Consequently a
# runtime cache volume can be mounted at HF_HOME without hiding the baked model.
RUN mkdir -p /opt/models/default-embedding \
    && if [ "${PRELOAD_EMBEDDING_MODEL}" = "true" ]; then \
        HF_HOME=/tmp/huggingface-cache EMBEDDING_MODEL_ID="${EMBEDDING_MODEL_ID}" \
        python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL_ID']).save('/opt/models/default-embedding')"; \
       fi \
    && rm -rf /tmp/huggingface-cache

COPY --chown=appuser:appuser . /app/backend

RUN mkdir -p \
        /app/data \
        /app/chroma_db \
        /app/logs \
        /app/PDF \
        /home/appuser/.cache/huggingface \
    && chown -R appuser:appuser \
        /app/data \
        /app/chroma_db \
        /app/logs \
        /app/PDF \
        /opt/models \
        /home/appuser/.cache \
    && chmod -R a+rX /opt/models \
    && chmod 0755 /app/backend/docker-entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
