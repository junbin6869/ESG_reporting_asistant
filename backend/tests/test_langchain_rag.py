import unittest
from unittest.mock import Mock, patch

from langchain_core.documents import Document

from app.services import rag_service


class LangChainRagTests(unittest.TestCase):
    @patch.object(rag_service, "get_guideline_retriever")
    def test_retrieval_maps_documents_to_existing_evidence_contract(self, get_retriever):
        retriever = Mock()
        retriever.invoke.return_value = [
            Document(
                page_content="Use renewable energy where practical.",
                metadata={
                    "source": "guideline.pdf",
                    "section_title": "Environmental (E)",
                    "topic": "Energy",
                    "page_start": 4,
                    "page_end": 5,
                    "chunk_index": 2,
                },
            )
        ]
        get_retriever.return_value = retriever

        evidence = rag_service.retrieve_guidelines(" renewable energy ", top_k=2)

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].chunk_id, "guideline.pdf_c2")
        self.assertEqual(evidence[0].section, "Environmental (E)")
        self.assertEqual(evidence[0].page, "4-5")
        retriever.invoke.assert_called_once_with("renewable energy")
        get_retriever.assert_called_once_with(
            top_k=2,
            search_type=None,
            score_threshold=None,
        )

    @patch.object(rag_service, "get_vector_store")
    def test_mmr_retriever_uses_diverse_fetch_pool(self, get_vector_store):
        vector_store = Mock()
        expected = Mock()
        vector_store.as_retriever.return_value = expected
        get_vector_store.return_value = vector_store

        actual = rag_service.get_guideline_retriever(top_k=3, search_type="mmr")

        self.assertIs(actual, expected)
        vector_store.as_retriever.assert_called_once_with(
            search_type="mmr",
            search_kwargs={"k": 3, "fetch_k": 12, "lambda_mult": 0.5},
        )

    @patch.object(rag_service, "get_vector_store")
    def test_threshold_retriever_clamps_threshold(self, get_vector_store):
        vector_store = Mock()
        get_vector_store.return_value = vector_store

        rag_service.get_guideline_retriever(
            top_k=2,
            search_type="similarity_score_threshold",
            score_threshold=1.5,
        )

        vector_store.as_retriever.assert_called_once_with(
            search_type="similarity_score_threshold",
            search_kwargs={"k": 2, "score_threshold": 1.0},
        )

    def test_empty_query_does_not_open_vector_store(self):
        with patch.object(rag_service, "get_guideline_retriever") as get_retriever:
            self.assertEqual(rag_service.retrieve_guidelines("  "), [])
            get_retriever.assert_not_called()


if __name__ == "__main__":
    unittest.main()

