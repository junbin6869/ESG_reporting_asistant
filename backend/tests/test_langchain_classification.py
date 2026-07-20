import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.schemas import EvidenceItem, InvoiceRow
from app.services import classification_service


class LangChainClassificationTests(unittest.TestCase):
    def setUp(self):
        self.invoice = InvoiceRow(
            id="invoice-1",
            invoice_number="INV-1",
            supplier_name="Solar Supplier",
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
                supporting_text="Increase use of renewable energy.",
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
    def test_structured_decision_is_saved_with_selected_evidence(
        self,
        get_chain,
        get_invoice,
        retrieve_guidelines,
        save_classification,
    ):
        get_invoice.return_value = self.invoice
        retrieve_guidelines.return_value = self.evidence
        chain = Mock()
        chain.invoke.return_value = classification_service.ClassificationDecision(
            category="Environmental",
            confidence="high",
            reason="The invoice is supported by renewable-energy guidance.",
            evidence_chunk_ids=["chunk-1"],
        )
        get_chain.return_value = chain
        save_classification.side_effect = lambda **kwargs: kwargs["classification"]

        with patch.object(classification_service.settings, "openai_api_key", "test-key"):
            result = classification_service.classify_invoice("invoice-1")

        self.assertEqual(result.category, "Environmental")
        self.assertEqual([item.chunk_id for item in result.evidence], ["chunk-1"])
        retrieve_guidelines.assert_called_once_with("Solar panel installation", top_k=3)
        saved = save_classification.call_args.kwargs
        self.assertEqual(saved["invoice_id"], "invoice-1")
        self.assertEqual(saved["prompt_version"], "classification_langchain_v2")
        self.assertIn("invoice_json", chain.invoke.call_args.args[0])

    @patch.object(classification_service, "save_classification")
    @patch.object(classification_service, "retrieve_guidelines")
    @patch.object(classification_service, "get_invoice")
    @patch.object(classification_service, "get_classification_chain")
    def test_empty_evidence_ids_do_not_attach_unselected_chunks(
        self,
        get_chain,
        get_invoice,
        retrieve_guidelines,
        save_classification,
    ):
        get_invoice.return_value = self.invoice
        retrieve_guidelines.return_value = self.evidence
        get_chain.return_value.invoke.return_value = (
            classification_service.ClassificationDecision(
                category="Non-ESG",
                confidence="medium",
                reason="No retrieved chunk supports an ESG classification.",
                evidence_chunk_ids=[],
            )
        )
        save_classification.side_effect = lambda **kwargs: kwargs["classification"]

        with patch.object(classification_service.settings, "openai_api_key", "test-key"):
            result = classification_service.classify_invoice("invoice-1")

        self.assertEqual(result.evidence, [])

    @patch.object(classification_service, "retrieve_guidelines", return_value=[])
    @patch.object(classification_service, "get_invoice")
    @patch.object(classification_service, "get_classification_chain")
    def test_invalid_structured_result_becomes_safe_gateway_error(
        self,
        get_chain,
        get_invoice,
        _retrieve_guidelines,
    ):
        get_invoice.return_value = self.invoice
        get_chain.return_value.invoke.return_value = {"category": "invalid"}

        with patch.object(classification_service.settings, "openai_api_key", "test-key"):
            with self.assertRaises(HTTPException) as raised:
                classification_service.classify_invoice("invoice-1")

        self.assertEqual(raised.exception.status_code, 502)

    def test_prompt_contains_invoice_and_evidence_without_manual_json_instruction(self):
        prompt = classification_service.build_classification_prompt(
            self.invoice.model_dump(exclude={"classification"}),
            self.evidence,
        )

        self.assertIn("Solar panel installation", prompt)
        self.assertIn("chunk-1", prompt)
        self.assertNotIn("Return valid JSON only", prompt)


if __name__ == "__main__":
    unittest.main()
