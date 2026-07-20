import re
from io import BytesIO

import fitz
from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.schemas import InvoiceExtractionDraft, InvoiceExtractionPreview


MIN_EXTRACTABLE_TEXT_LENGTH = 80
MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_RETURNED_RAW_TEXT_CHARACTERS = 50_000


async def preview_invoice_upload(file: UploadFile) -> InvoiceExtractionPreview:
    filename = file.filename or "invoice.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files can use extraction preview.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")
    if len(content) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Invoice PDF exceeds the 10 MB upload limit.",
        )

    return await run_in_threadpool(_preview_invoice_content, content, filename)


def _preview_invoice_content(
    content: bytes,
    filename: str,
) -> InvoiceExtractionPreview:

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to read uploaded PDF.") from exc

    if document.page_count > MAX_PDF_PAGES:
        document.close()
        raise HTTPException(
            status_code=413,
            detail=f"Invoice PDF exceeds the {MAX_PDF_PAGES}-page processing limit.",
        )

    raw_text = _extract_pdf_text(document)
    method = "pdf_text"
    warnings = ["Please verify extracted fields before importing."]

    if len(raw_text.strip()) < MIN_EXTRACTABLE_TEXT_LENGTH:
        ocr_text, ocr_warnings = _extract_ocr_text(document)
        raw_text = ocr_text
        warnings.extend(ocr_warnings)
        method = "ocr" if ocr_text.strip() else "unavailable"

    invoice, parse_warnings = _parse_invoice_text(raw_text, filename=filename)
    warnings.extend(parse_warnings)
    document.close()

    returned_raw_text = raw_text[:MAX_RETURNED_RAW_TEXT_CHARACTERS]
    if len(raw_text) > MAX_RETURNED_RAW_TEXT_CHARACTERS:
        warnings.append(
            "Extracted text was truncated in the preview response; parsed fields used the full text."
        )

    return InvoiceExtractionPreview(
        method=method,
        raw_text=returned_raw_text,
        invoice=invoice,
        warnings=_dedupe(warnings),
    )


def _extract_pdf_text(document: fitz.Document) -> str:
    pages = [page.get_text("text") for page in document]
    return "\n".join(page for page in pages if page).strip()


def _extract_ocr_text(document: fitz.Document) -> tuple[str, list[str]]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return (
            "",
            [
                "This PDF has no extractable text. OCR fallback requires pytesseract and Pillow.",
            ],
        )

    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return (
            "",
            [
                "This PDF has no extractable text. Tesseract OCR is not installed or TESSERACT_CMD is not configured.",
            ],
        )

    text_blocks: list[str] = []
    warnings: list[str] = []
    for page_index, page in enumerate(document):
        try:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            with Image.open(BytesIO(pixmap.tobytes("png"))) as image:
                text_blocks.append(
                    pytesseract.image_to_string(
                        image,
                        lang=settings.ocr_languages,
                        config="--oem 3 --psm 6 -c preserve_interword_spaces=1",
                    )
                )
        except Exception as exc:
            warnings.append(f"OCR failed on page {page_index + 1}: {exc}")

    return "\n".join(text_blocks).strip(), warnings


def _parse_invoice_text(
    raw_text: str,
    filename: str | None = None,
) -> tuple[InvoiceExtractionDraft, list[str]]:
    text = _normalize_text(raw_text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    warnings: list[str] = []

    invoice_number, invoice_number_warning = _extract_invoice_number(
        lines,
        text,
        filename,
    )
    if invoice_number_warning:
        warnings.append(invoice_number_warning)
    supplier_name = _extract_supplier(lines)
    buyer_name = _extract_buyer(lines)
    invoice_date = _normalize_date(_first_match(text, [
        r"\bDate\s*[^0-9]{0,8}(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4})",
        r"\bInvoice\s*Date\s*[^0-9]{0,8}(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4})",
        r"\bDate\s*[^0-9]{0,12}(\d{7,8})",
        r"\bInvoice\s*Date\s*[^0-9]{0,12}(\d{7,8})",
    ]))
    amount = _extract_amount(text)
    description = _extract_description(lines)

    required = {
        "invoice_number": invoice_number,
        "supplier_name": supplier_name,
        "invoice_date": invoice_date,
        "amount": amount,
        "description": description,
    }
    for field, value in required.items():
        if value in {"", None}:
            warnings.append(f"Could not confidently extract {field}. Please fill it manually.")

    return (
        InvoiceExtractionDraft(
            invoice_number=invoice_number,
            supplier_name=supplier_name,
            buyer_name=buyer_name or None,
            invoice_date=invoice_date,
            amount=amount or 0,
            currency="MYR",
            description=description,
        ),
        warnings,
    )


def _normalize_text(raw_text: str) -> str:
    cleaned = raw_text.replace("\ufffd", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_supplier(lines: list[str]) -> str:
    for line in lines[:10]:
        upper = line.upper()
        if "SDN BHD" in upper or "BERHAD" in upper or "ENTERPRISE" in upper:
            return _clean_company_name(line)
    return ""


def _extract_invoice_number(
    lines: list[str],
    text: str,
    filename: str | None = None,
) -> tuple[str, str | None]:
    filename_number = _extract_invoice_number_from_filename(filename or "")
    ocr_number = ""

    for line in lines:
        upper = line.upper()
        if any(label in upper for label in ["P/O NO", "PO NO", "PASSPORT", "TEL"]):
            continue
        match = re.search(
            r"^\s*(?:Invoice\s*)?No\s*[:：>\-»\s]*([A-Za-z0-9$§][A-Za-z0-9$§\-\/]*)\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            ocr_number = _normalize_invoice_number_token(match.group(1))
            break

    if not ocr_number:
        match = re.search(
            r"(?:CASH\s+SALE\s*/\s*INVOICE|INVOICE)[\s\S]{0,80}?\bNo\s*[:：>\-»\s]*([A-Za-z0-9$§][A-Za-z0-9$§\-\/]*)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            ocr_number = _normalize_invoice_number_token(match.group(1))

    if not ocr_number:
        match = re.search(
            r"\bInvoice\s*(?:No|Number)\s*[:：>\-»\s]*([A-Za-z0-9$§][A-Za-z0-9$§\-\/]*)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            ocr_number = _normalize_invoice_number_token(match.group(1))

    if filename_number and not ocr_number:
        return filename_number, "Invoice number was inferred from the file name."

    if filename_number and ocr_number and _looks_like_ocr_number_misread(
        ocr_number,
        filename_number,
    ):
        return (
            filename_number,
            f"Invoice number was taken from the file name because OCR read {ocr_number}.",
        )

    return ocr_number, None


def _normalize_invoice_number_token(value: str) -> str:
    clean = value.strip().strip(" .:-")
    if re.fullmatch(r"[$§Ss]\d{2,}", clean):
        return f"5{clean[1:]}"
    if re.fullmatch(r"[Oo]\d{2,}", clean):
        return f"0{clean[1:]}"
    return clean


def _extract_invoice_number_from_filename(filename: str) -> str:
    if not filename:
        return ""
    basename = filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    candidates = re.findall(r"\b(\d{3,12})\b", basename)
    for candidate in candidates:
        if not re.fullmatch(r"19\d{2}|20\d{2}|21\d{2}", candidate):
            return candidate
    return candidates[0] if candidates else ""


def _looks_like_ocr_number_misread(ocr_number: str, filename_number: str) -> bool:
    if not (ocr_number.isdigit() and filename_number.isdigit()):
        return False
    if filename_number.endswith(ocr_number) and len(filename_number) - len(ocr_number) == 1:
        return True
    if len(ocr_number) != len(filename_number):
        return False
    differences = sum(
        1
        for left, right in zip(ocr_number, filename_number)
        if left != right
    )
    return 0 < differences <= 2


def _extract_buyer(lines: list[str]) -> str:
    skip_terms = {
        "CASH SALE / INVOICE",
        "AUTHORISED SIGNATURE",
        "ITEM",
        "DESCRIPTION",
    }
    for index, line in enumerate(lines):
        if re.fullmatch(r"No", line, flags=re.IGNORECASE) or re.match(
            r"No\s*:", line, flags=re.IGNORECASE
        ):
            for candidate in lines[index + 1:index + 5]:
                upper = candidate.upper()
                if (
                    upper not in skip_terms
                    and not upper.startswith("YOUR P/O")
                    and not re.match(r"^\d", candidate)
                    and len(candidate) >= 3
                ):
                    return _strip_inline_labels(candidate)
    return ""


def _extract_amount(text: str) -> float | None:
    total_matches = re.findall(
        r"Total[\s\S]{0,80}?(?:RM)?\s*([0-9][0-9,]*\.\d{2})",
        text,
        re.IGNORECASE,
    )
    if total_matches:
        return _to_float(total_matches[-1])

    decimal_matches = re.findall(r"\b([0-9][0-9,]*\.\d{2})\b", text)
    candidates = [
        value
        for value in (_to_float(match) for match in decimal_matches)
        if value is not None
    ]
    if not candidates:
        return None
    return max(candidates)


def _extract_description(lines: list[str]) -> str:
    for line in lines:
        item_description = _extract_item_description_from_line(line)
        if item_description:
            return item_description

    for index, line in enumerate(lines):
        if line.lower() == "description":
            for candidate in lines[index + 1:index + 14]:
                item_description = _extract_item_description_from_line(candidate)
                if item_description:
                    return item_description
                if _looks_like_item_description(candidate):
                    return _strip_item_prefix(candidate)

    for line in lines[20:]:
        if _looks_like_item_description(line):
            return _strip_item_prefix(line)

    return ""


def _extract_item_description_from_line(value: str) -> str:
    patterns = [
        r"^\s*\d+[.)]?\s+(.+?)\s+\d+\s+(?:PC|PCS|UNIT|UNITS|EA|SET)\b",
        r"^\s*\d+[.)]?\s+(.+?)\s+[0-9][0-9,]*\.\d{2}\s+[0-9][0-9,]*\.\d{2}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" :-")
    return ""


def _looks_like_item_description(value: str) -> bool:
    upper = value.upper()
    excluded = {
        "AUTHORISED SIGNATURE",
        "QTY",
        "UOM",
        "U/PRICE",
        "DISC.",
        "TOTAL",
        "RM",
        "PC",
    }
    if upper in excluded:
        return False
    if any(
        marker in upper
        for marker in [
            "SDN BHD",
            "CASH SALE",
            "INVOICE",
            "LORONG",
            "TAMAN",
            "TEL",
            "EMAIL",
            "@",
            "PASSPORT",
            "RINGGIT MALAYSIA",
            "BANK",
            "REFER",
        ]
    ):
        return False
    if re.fullmatch(r"[0-9.,]+", value):
        return False
    return len(value) >= 5 and any(char.isalpha() for char in value)


def _normalize_date(value: str) -> str:
    if not value:
        return ""

    clean_value = value.strip()
    if re.fullmatch(r"\d{7,8}", clean_value):
        if len(clean_value) == 7:
            day = clean_value[:2]
            month = clean_value[2]
            year = clean_value[3:]
        else:
            day = clean_value[:2]
            month = clean_value[2:4]
            year = clean_value[4:]
        try:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        except ValueError:
            return value

    parts = re.split(r"[\/\-.]", clean_value)
    if len(parts) != 3:
        return value

    day, month, year = parts
    if len(year) == 2:
        year = f"20{year}"

    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return value


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _clean_company_name(value: str) -> str:
    match = re.search(
        r"([A-Z0-9&.,' -]*?\b(?:SDN\.?\s*BHD|BERHAD|ENTERPRISE)\b)",
        value,
        flags=re.IGNORECASE,
    )
    company = match.group(1) if match else value
    company = re.sub(r"\s*\([^)]*\)\s*$", "", company)
    company = re.sub(r"^[^A-Za-z0-9]+", "", company)
    words = company.split()
    while len(words) > 1 and len(words[0]) <= 2 and not words[0].isalnum():
        words.pop(0)
    return " ".join(words).strip(" :-")


def _strip_inline_labels(value: str) -> str:
    return re.split(
        r"\b(?:Your\s+P/?O\s+No|Order\s+Date|Attention|Date|Page|TEL)\b",
        value,
        flags=re.IGNORECASE,
    )[0].strip(" :-")


def _strip_item_prefix(value: str) -> str:
    return re.sub(r"^\s*\d+[.)]?\s*", "", value).strip(" :-")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
