from __future__ import annotations

import argparse
from pathlib import Path

try:
    from app.core.config import settings
    from app.infrastructure.guideline_index import VectorDBManager
except ModuleNotFoundError:
    # Root compatibility entry point imports this as backend.scripts.*.
    from ..app.core.config import settings
    from ..app.infrastructure.guideline_index import VectorDBManager


def _default_pdf_paths(pdf_dir: Path) -> list[Path]:
    manifest = pdf_dir / "index_manifest.txt"
    if not manifest.exists():
        return sorted(pdf_dir.glob("*.pdf"))

    paths: list[Path] = []
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path = Path(line)
        paths.append(path if path.is_absolute() else pdf_dir / path)
    return paths


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the local ESG guideline index.")
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        help=(
            "PDF paths. Defaults to PDF/index_manifest.txt, or every PDF in "
            "GUIDELINE_PDF_DIR when no manifest exists."
        ),
    )
    parser.add_argument("--reset", action="store_true", help="Recreate the collection first.")
    parser.add_argument("--force", action="store_true", help="Re-embed unchanged sources.")
    parser.add_argument("--max-bullets", type=int, default=3)
    parser.add_argument("--min-words", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    pdf_paths = args.pdfs or _default_pdf_paths(settings.guideline_pdf_dir)
    if not pdf_paths:
        raise FileNotFoundError(
            f"No PDF files found in guideline directory: {settings.guideline_pdf_dir}"
        )
    missing = [str(path) for path in pdf_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Guideline manifest references missing PDF(s): " + ", ".join(missing)
        )

    manager = VectorDBManager(
        db_dir=str(settings.chroma_db_dir),
        collection_name=settings.chroma_collection,
        model_name=settings.embedding_model,
        device=settings.embedding_device,
    )
    manager.ingest_pdfs(
        pdf_paths,
        max_bullets_per_chunk=args.max_bullets,
        min_words_per_chunk=args.min_words,
        force=args.force,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
