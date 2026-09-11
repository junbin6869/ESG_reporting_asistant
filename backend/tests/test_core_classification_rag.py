import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from langchain_core.documents import Document

from app.schemas import EvidenceItem, InvoiceRow
from app.services import classification_service, rag_service


class CoreClassificationAndRagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.invoice = InvoiceRow(
            id="invoice-1",
            invoice_number="INV-1",
            supplier_name="Solar Supplier Sdn Bhd",
            invoice_date="2026-01-02",
            amount=1000,
            currency="MYR",
            description="Solar panel installation",
        )
        self.evidence = [
            EvidenceItem(
                chunk_id="chunk-1",
                source="guideline.pdf",
                section="Environmental (E)",
                topic="Energy",
                page=4,
                supporting_text="Increase the use of renewable energy.",
            ),
            EvidenceItem(
                chunk_id="chunk-2",
                source="guideline.pdf",
                section="Social (S)",
                topic="Safety",
                page=8,
                supporting_text="Protect employee health and safety.",
            ),
        ]

    @patch.object(classification_service, "save_classification")
    @patch.object(classification_service, "retrieve_guidelines")
    @patch.object(classification_service, "get_invoice")
    @patch.object(classification_service, "get_classification_chain")
    @patch.object(classification_service, "get_query_rewrite_chain")
    def test_classification_saves_only_selected_evidence(
        self,
        get_query_chain,
        get_chain,
        get_invoice,
        retrieve_guidelines,
        save_classification,
    ):
        get_invoice.return_value = self.invoice
        retrieve_guidelines.return_value = self.evidence
        get_query_chain.return_value.invoke.return_value = (
            classification_service.ClassificationQueryPlan(
                queries=["renewable energy installation"]
            )
        )
        get_chain.return_value.invoke.return_value = (
            classification_service.ClassificationDecision(
                category="Environmental",
                confidence="high",
                reason="The invoice is supported by renewable-energy guidance.",
                evidence_chunk_ids=["chunk-1"],
            )
        )
        save_classification.side_effect = lambda **kwargs: kwargs["classification"]

        with patch.object(classification_service.settings, "openai_api_key", "test-key"):
            result = classification_service.classify_invoice("invoice-1")

        self.assertEqual(result.category, "Environmental")
        self.assertEqual([item.chunk_id for item in result.evidence], ["chunk-1"])
        self.assertEqual(retrieve_guidelines.call_count, 2)

    @patch.object(classification_service, "retrieve_guidelines", return_value=[])
    @patch.object(classification_service, "get_invoice")
    @patch.object(classification_service, "get_classification_chain")
    @patch.object(classification_service, "get_query_rewrite_chain")
    def test_invalid_llm_structure_returns_safe_gateway_error(
        self,
        get_query_chain,
        get_chain,
        get_invoice,
        _retrieve_guidelines,
    ):
        get_invoice.return_value = self.invoice
        get_query_chain.return_value.invoke.return_value = (
            classification_service.ClassificationQueryPlan(queries=["solar energy"])
        )
        get_chain.return_value.invoke.return_value = {"category": "invalid"}

        with patch.object(classification_service.settings, "openai_api_key", "test-key"):
            with self.assertRaises(HTTPException) as raised:
                classification_service.classify_invoice("invoice-1")

        self.assertEqual(raised.exception.status_code, 502)

    @patch.object(classification_service, "get_query_rewrite_chain")
    def test_query_rewrite_keeps_original_and_deduplicates(self, get_query_chain):
        get_query_chain.return_value.invoke.return_value = (
            classification_service.ClassificationQueryPlan(
                queries=["Solar panel installation", "renewable energy investment"]
            )
        )

        queries = classification_service.build_retrieval_queries(
            self.invoice.model_dump(exclude={"classification"})
        )

        self.assertEqual(
            queries,
            ["Solar panel installation", "renewable energy investment"],
        )

    @patch.object(classification_service, "get_query_rewrite_chain")
    def test_query_rewrite_failure_falls_back_to_original(self, get_query_chain):
        get_query_chain.return_value.invoke.side_effect = RuntimeError("timeout")

        queries = classification_service.build_retrieval_queries(
            self.invoice.model_dump(exclude={"classification"})
        )

        self.assertEqual(queries, ["Solar panel installation"])

    @patch.object(rag_service, "get_guideline_retriever")
    def test_rag_maps_documents_to_evidence_contract(self, get_retriever):
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
        self.assertEqual(evidence[0].page, "4-5")
        retriever.invoke.assert_called_once_with("renewable energy")


if __name__ == "__main__":
    unittest.main()
