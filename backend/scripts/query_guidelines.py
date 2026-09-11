"""Query the local ESG guideline index and print screenshot-friendly evidence.

This utility performs retrieval only. It does not call an LLM and does not
modify SQLite or the Chroma collection.

Example:
    python scripts/query_guidelines.py "health and safety indicators" --top-k 2
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
import textwrap
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.rag_service import retrieve_guidelines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query the local ESG guideline vector index.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default="health and safety indicators",
        help="Natural-language ESG query.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=2,
        help="Number of guideline chunks to display (default: 2).",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=94,
        help="Terminal line width for supporting text (default: 94).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=600,
        help="Maximum supporting-text characters per result (default: 600).",
    )
    return parser.parse_args()


def truncate_text(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def main() -> None:
    args = parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1")
    if args.width < 50:
        raise SystemExit("--width must be at least 50")
    if args.max_chars < 80:
        raise SystemExit("--max-chars must be at least 80")

    print("=" * args.width)
    print("LOCAL ESG GUIDELINE RETRIEVAL")
    print(f'Query: "{args.query.strip()}"')
    print(f"Requested results: top {args.top_k}")
    print("=" * args.width)

    # Model-loading progress is emitted on stderr. Suppress it to keep the
    # terminal output suitable for a report screenshot.
    with contextlib.redirect_stderr(io.StringIO()):
        evidence_items = retrieve_guidelines(args.query, top_k=args.top_k)
    if not evidence_items:
        print("No guideline chunks were retrieved.")
        return

    print(f"Retrieved {len(evidence_items)} guideline chunk(s).\n")
    for position, item in enumerate(evidence_items, start=1):
        print(f"RESULT {position}")
        print("-" * args.width)
        print(f"Chunk ID : {item.chunk_id}")
        print(f"Source   : {item.source}")
        print(f"Section  : {item.section or 'Not specified'}")
        print(f"Topic    : {item.topic or 'Not specified'}")
        print(f"Page     : {item.page if item.page is not None else 'Not specified'}")
        print("Evidence :")
        supporting_text = truncate_text(item.supporting_text, args.max_chars)
        print(textwrap.fill(supporting_text, width=args.width, subsequent_indent="           "))
        print()

    print("=" * args.width)
    print("Retrieved from the local Chroma ESG guideline collection.")


if __name__ == "__main__":
    main()
