from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz
from langchain_core.documents import Document

from .vector_store import create_chroma_vector_store, create_embeddings


CHUNK_SCHEMA_VERSION = "esg-point-form-v2"
SECTION_PATTERN = re.compile(r".+\(([ESG])\)\s*$")
BULLET_PATTERN = re.compile(r"^(?:[•●▪◦‣⁃·*–—-]|鈥[⑩棌鈻棪])\s*")


class VectorDBManager:
    """Compatibility facade backed by LangChain's Chroma integration.

    It retains the old project's parsing/query methods while making ingestion
    idempotent. A source is only re-embedded when its bytes, chunking version,
    or expected chunk IDs change. Re-indexing uses Chroma upsert semantics and
    removes stale chunks belonging to that source.
    """

    def __init__(
        self,
        db_dir: str = "./chroma_db",
        collection_name: str = "esg_docs",
        model_name: str = "all-MiniLM-L6-v2",
        *,
        device: str = "",
        embeddings: Any | None = None,
        vector_store: Any | None = None,
    ) -> None:
        self.db_dir = str(db_dir)
        self.collection_name = collection_name
        self.model_name = model_name
        self.device = device
        self.embeddings = embeddings

        if vector_store is None:
            self.embeddings = self.embeddings or create_embeddings(
                model_name,
                device=device,
            )
            vector_store = create_chroma_vector_store(
                db_dir=self.db_dir,
                collection_name=self.collection_name,
                embeddings=self.embeddings,
            )
        self.vector_store = vector_store

    def extract_text_by_page(self, pdf_path: str | Path) -> list[dict[str, Any]]:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        pages: list[dict[str, Any]] = []
        with fitz.open(path) as document:
            for page_number, page in enumerate(document, start=1):
                text = page.get_text()
                if text and text.strip():
                    pages.append({"page": page_number, "text": text})
        return pages

    @staticmethod
    def clean_text(text: str) -> str:
        text = re.sub(r"\r", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        cleaned_lines: list[str] = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                cleaned_lines.append("")
                continue
            if re.fullmatch(r"\d+", line):
                continue
            if line.lower() in {"chapter", "#besustainable"}:
                continue
            if re.fullmatch(r"[\W_]+", line):
                continue
            cleaned_lines.append(line)

        return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()

    @staticmethod
    def _is_section_line(line: str) -> bool:
        return bool(SECTION_PATTERN.fullmatch(line))

    @staticmethod
    def _extract_section_code(line: str) -> str | None:
        match = SECTION_PATTERN.fullmatch(line)
        return match.group(1) if match else None

    @staticmethod
    def _normalize_bullet_line(line: str) -> str:
        return re.sub(r"\s+", " ", BULLET_PATTERN.sub("", line)).strip()

    def _parse_point_form_lines(
        self,
        pages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        current_section: str | None = None
        current_section_title: str | None = None
        current_topic: str | None = None
        last_record: dict[str, Any] | None = None

        for page_data in pages:
            cleaned_text = self.clean_text(str(page_data["text"]))
            lines = [line.strip() for line in cleaned_text.split("\n") if line.strip()]

            for line in lines:
                if self._is_section_line(line):
                    current_section = self._extract_section_code(line)
                    current_section_title = line
                    current_topic = None
                    last_record = None
                    continue

                if BULLET_PATTERN.match(line):
                    bullet = self._normalize_bullet_line(line)
                    if bullet:
                        last_record = {
                            "section": current_section,
                            "section_title": current_section_title,
                            "topic": current_topic,
                            "page": int(page_data["page"]),
                            "text": bullet,
                        }
                        records.append(last_record)
                    continue

                if last_record is not None and current_topic is not None:
                    if re.match(r"^[a-z0-9(]", line) or len(line.split()) > 8:
                        last_record["text"] = f"{last_record['text']} {line}".strip()
                        continue

                current_topic = line
                last_record = None

        return records

    def _merge_records_into_chunks(
        self,
        records: list[dict[str, Any]],
        source_name: str,
        max_bullets_per_chunk: int = 3,
        min_words_per_chunk: int = 10,
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        buffer_texts: list[str] = []
        buffer_pages: list[int] = []
        current_section: str | None = None
        current_section_title: str | None = None
        current_topic: str | None = None

        def flush_buffer() -> None:
            nonlocal buffer_texts, buffer_pages
            chunk_text = re.sub(r"\s+", " ", " ".join(buffer_texts)).strip()
            if chunk_text and len(chunk_text.split()) >= min_words_per_chunk:
                chunk_index = len(chunks)
                chunks.append(
                    {
                        "id": f"{source_name}_c{chunk_index}",
                        "text": chunk_text,
                        "metadata": {
                            "source": source_name,
                            "section": current_section,
                            "section_title": current_section_title,
                            "topic": current_topic,
                            "page_start": min(buffer_pages),
                            "page_end": max(buffer_pages),
                            "chunk_index": chunk_index,
                        },
                    }
                )
            buffer_texts = []
            buffer_pages = []

        for record in records:
            section = record.get("section")
            topic = record.get("topic")
            text = str(record.get("text") or "").strip()
            if not text or section is None or topic is None:
                continue

            if section != current_section or topic != current_topic:
                flush_buffer()
                current_section = str(section)
                current_section_title = str(record.get("section_title") or "")
                current_topic = str(topic)

            buffer_texts.append(text)
            buffer_pages.append(int(record["page"]))
            if len(buffer_texts) >= max_bullets_per_chunk:
                flush_buffer()

        flush_buffer()
        return chunks

    def chunk_pages(
        self,
        pages: list[dict[str, Any]],
        source_name: str,
        max_bullets_per_chunk: int = 3,
        min_words_per_chunk: int = 10,
    ) -> list[dict[str, Any]]:
        if max_bullets_per_chunk < 1:
            raise ValueError("max_bullets_per_chunk must be at least 1")
        if min_words_per_chunk < 1:
            raise ValueError("min_words_per_chunk must be at least 1")

        records = self._parse_point_form_lines(pages)
        return self._merge_records_into_chunks(
            records,
            source_name,
            max_bullets_per_chunk=max_bullets_per_chunk,
            min_words_per_chunk=min_words_per_chunk,
        )

    @staticmethod
    def _source_version(pdf_path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(pdf_path).open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in metadata.items()
            if value is not None and isinstance(value, (str, int, float, bool))
        }

    def _get_source_records(self, source_name: str) -> dict[str, Any]:
        return self.vector_store.get(
            where={"source": source_name},
            include=["metadatas"],
        )

    def _store_chunks(
        self,
        chunks: list[dict[str, Any]],
        *,
        source_name: str,
        source_version: str,
        index_version: str,
        force: bool,
    ) -> dict[str, Any]:
        existing = self._get_source_records(source_name)
        existing_ids = [str(item) for item in existing.get("ids") or []]
        existing_metadata = existing.get("metadatas") or []
        chunk_ids = [str(chunk["id"]) for chunk in chunks]

        versions_match = len(existing_metadata) == len(existing_ids) and all(
            (metadata or {}).get("source_version") == source_version
            and (metadata or {}).get("index_version") == index_version
            for metadata in existing_metadata
        )
        if not force and set(existing_ids) == set(chunk_ids) and versions_match:
            return {
                "source": source_name,
                "status": "skipped",
                "upserted": 0,
                "deleted": 0,
                "chunks": len(chunk_ids),
            }

        indexed_at = datetime.now(timezone.utc).isoformat()
        documents: list[Document] = []
        for chunk in chunks:
            metadata = self._clean_metadata(dict(chunk.get("metadata") or {}))
            metadata.update(
                {
                    "chunk_id": str(chunk["id"]),
                    "source": source_name,
                    "source_version": source_version,
                    "index_version": index_version,
                    "chunk_schema_version": CHUNK_SCHEMA_VERSION,
                    "indexed_at": indexed_at,
                }
            )
            documents.append(
                Document(
                    id=str(chunk["id"]),
                    page_content=str(chunk["text"]),
                    metadata=metadata,
                )
            )

        if documents:
            self.vector_store.add_documents(documents=documents, ids=chunk_ids)

        stale_ids = sorted(set(existing_ids) - set(chunk_ids))
        if stale_ids:
            self.vector_store.delete(ids=stale_ids)

        return {
            "source": source_name,
            "status": "indexed",
            "upserted": len(documents),
            "deleted": len(stale_ids),
            "chunks": len(chunk_ids),
        }

    def ingest_pdfs(
        self,
        pdf_paths: Iterable[str | Path],
        max_bullets_per_chunk: int = 3,
        min_words_per_chunk: int = 10,
        *,
        force: bool = False,
        reset: bool = False,
    ) -> dict[str, Any]:
        if reset:
            self.reset_collection()

        index_version = (
            f"{CHUNK_SCHEMA_VERSION}:bullets={max_bullets_per_chunk}:"
            f"min_words={min_words_per_chunk}"
        )
        source_results: list[dict[str, Any]] = []
        for raw_path in pdf_paths:
            pdf_path = Path(raw_path)
            source_name = pdf_path.name
            print(f"[INFO] Processing PDF: {source_name}")
            pages = self.extract_text_by_page(pdf_path)
            chunks = self.chunk_pages(
                pages,
                source_name,
                max_bullets_per_chunk=max_bullets_per_chunk,
                min_words_per_chunk=min_words_per_chunk,
            )
            if not chunks:
                print(f"[WARNING] No structured ESG chunks found in {source_name}; skipped.")
                source_results.append(
                    {
                        "source": source_name,
                        "status": "empty",
                        "upserted": 0,
                        "deleted": 0,
                        "chunks": 0,
                    }
                )
                continue

            result = self._store_chunks(
                chunks,
                source_name=source_name,
                source_version=self._source_version(pdf_path),
                index_version=index_version,
                force=force,
            )
            source_results.append(result)
            print(
                f"[INFO] {result['status']}: {source_name} "
                f"({result['chunks']} chunks, {result['deleted']} stale removed)"
            )

        summary = {
            "collection": self.collection_name,
            "sources": source_results,
            "upserted": sum(item["upserted"] for item in source_results),
            "deleted": sum(item["deleted"] for item in source_results),
            "skipped": sum(item["status"] == "skipped" for item in source_results),
        }
        print(
            f"[SUCCESS] Index complete: {summary['upserted']} upserted, "
            f"{summary['deleted']} stale deleted, {summary['skipped']} sources unchanged."
        )
        return summary

    @staticmethod
    def _document_chunk_id(document: Document) -> str:
        metadata = document.metadata
        if metadata.get("chunk_id"):
            return str(metadata["chunk_id"])
        if document.id:
            return str(document.id)
        if metadata.get("source") and metadata.get("chunk_index") is not None:
            return f"{metadata['source']}_c{metadata['chunk_index']}"
        digest = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()[:16]
        return f"legacy-{digest}"

    def query(self, query_text: str, top_k: int = 5) -> dict[str, Any]:
        results = self.vector_store.similarity_search_with_score(query_text, k=top_k)
        formatted_results: list[dict[str, Any]] = []
        for rank, (document, distance) in enumerate(results, start=1):
            metadata = document.metadata
            formatted_results.append(
                {
                    "rank": rank,
                    "chunk_id": self._document_chunk_id(document),
                    "distance": distance,
                    "source": metadata.get("source", "N/A"),
                    "section": metadata.get("section", "N/A"),
                    "section_title": metadata.get("section_title", "N/A"),
                    "topic": metadata.get("topic", "N/A"),
                    "page_start": metadata.get("page_start", "N/A"),
                    "page_end": metadata.get("page_end", "N/A"),
                    "chunk_index": metadata.get("chunk_index", "N/A"),
                    "text": document.page_content,
                }
            )
        return {"query": query_text, "top_k": top_k, "results": formatted_results}

    @staticmethod
    def print_results(result_data: dict[str, Any]) -> None:
        results = result_data.get("results", [])
        if not results:
            print("[INFO] No results found.")
            return
        for item in results:
            print(
                f"#{item['rank']} {item['chunk_id']} | {item['source']} | "
                f"{item['section_title']} | page {item['page_start']}-{item['page_end']}"
            )
            print(item["text"])

    def get_all_documents(self) -> dict[str, Any]:
        return self.vector_store.get()

    def reset_collection(self) -> None:
        if self.embeddings is None:
            raise RuntimeError("Cannot reset an injected vector store without embeddings")
        self.vector_store.delete_collection()
        self.vector_store = create_chroma_vector_store(
            db_dir=self.db_dir,
            collection_name=self.collection_name,
            embeddings=self.embeddings,
        )

