import unittest
from unittest.mock import Mock

from app.infrastructure.guideline_index import VectorDBManager


class GuidelineIndexTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.manager = VectorDBManager(vector_store=self.store)
        self.chunks = [
            {
                "id": "guideline.pdf_c0",
                "text": "A sufficiently long guideline chunk for testing indexing.",
                "metadata": {
                    "source": "guideline.pdf",
                    "section": "E",
                    "section_title": "Environmental (E)",
                    "topic": "Energy",
                    "page_start": 1,
                    "page_end": 1,
                    "chunk_index": 0,
                },
            }
        ]

    def test_index_upserts_current_chunks_and_removes_stale_source_chunks(self):
        self.store.get.return_value = {
            "ids": ["guideline.pdf_c0", "guideline.pdf_c9"],
            "metadatas": [{}, {}],
        }

        result = self.manager._store_chunks(
            self.chunks,
            source_name="guideline.pdf",
            source_version="sha256-v1",
            index_version="schema-v1",
            force=False,
        )

        self.assertEqual(result["upserted"], 1)
        self.assertEqual(result["deleted"], 1)
        document = self.store.add_documents.call_args.kwargs["documents"][0]
        self.assertEqual(document.id, "guideline.pdf_c0")
        self.assertEqual(document.metadata["source_version"], "sha256-v1")
        self.store.delete.assert_called_once_with(ids=["guideline.pdf_c9"])

    def test_unchanged_source_is_not_reembedded(self):
        self.store.get.return_value = {
            "ids": ["guideline.pdf_c0"],
            "metadatas": [
                {"source_version": "sha256-v1", "index_version": "schema-v1"}
            ],
        }

        result = self.manager._store_chunks(
            self.chunks,
            source_name="guideline.pdf",
            source_version="sha256-v1",
            index_version="schema-v1",
            force=False,
        )

        self.assertEqual(result["status"], "skipped")
        self.store.add_documents.assert_not_called()
        self.store.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
