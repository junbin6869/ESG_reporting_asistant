"""Create twelve controlled invoice PDFs for OCR evaluation.

Four base invoices are rendered in three conditions: native PDF text, clean
image-only scan, and moderately degraded image-only scan. Ground-truth fields
are written to ``backend/evaluation/ocr_ground_truth.json``.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "pdf" / "ocr_test_invoices"
GROUND_TRUTH_PATH = BACKEND_ROOT / "evaluation" / "ocr_ground_truth.json"
PAGE_WIDTH, PAGE_HEIGHT = A4
IMAGE_SIZE = (1654, 2339)

BASE_INVOICES = [
    {
        "supplier_name": "Green Solar Services Sdn Bhd",
        "buyer_name": "ABC Manufacturing Sdn Bhd",
        "invoice_date": "2026-09-03",
        "amount": 1250.00,
        "description": "Solar panel maintenance",
    },
    {
        "supplier_name": "SafeWork Academy Sdn Bhd",
        "buyer_name": "ABC Manufacturing Sdn Bhd",
        "invoice_date": "2026-09-05",
        "amount": 2280.00,
        "description": "Employee safety training",
    },
    {
        "supplier_name": "SecureAudit Consulting Sdn Bhd",
        "buyer_name": "ABC Manufacturing Sdn Bhd",
        "invoice_date": "2026-09-07",
        "amount": 4650.00,
        "description": "Internal controls audit",
    },
    {
        "supplier_name": "OfficeMart Supplies Enterprise",
        "buyer_name": "ABC Manufacturing Sdn Bhd",
        "invoice_date": "2026-09-09",
        "amount": 430.00,
        "description": "General office stationery",
    },
]


def display_date(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{day}/{month}/{year}"


def invoice_lines(record: dict) -> list[tuple[str, str]]:
    amount = f"{record['amount']:,.2f}"
    return [
        ("company", record["supplier_name"]),
        ("title", "INVOICE"),
        ("normal", f"Invoice No: {record['invoice_number']}"),
        ("normal", f"Invoice Date: {display_date(record['invoice_date'])}"),
        ("normal", f"Buyer: {record['buyer_name']}"),
        ("heading", "Description"),
        (
            "normal",
            f"1 {record['description']} 1 UNIT {amount} {amount}",
        ),
        ("total", f"Total RM {amount}"),
        ("footer", "Thank you for your business."),
    ]


def create_native_pdf(path: Path, record: dict) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(f"OCR Test Invoice {record['invoice_number']}")
    y = PAGE_HEIGHT - 70
    for style, text in invoice_lines(record):
        if style == "company":
            pdf.setFont("Helvetica-Bold", 18)
            pdf.drawString(55, y, text)
            y -= 52
        elif style == "title":
            pdf.setFont("Helvetica-Bold", 24)
            pdf.drawString(55, y, text)
            y -= 48
        elif style == "heading":
            y -= 15
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(55, y, text)
            y -= 28
        elif style == "total":
            y -= 20
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawRightString(PAGE_WIDTH - 55, y, text)
            y -= 50
        elif style == "footer":
            pdf.setFont("Helvetica-Oblique", 10)
            pdf.drawString(55, y, text)
        else:
            pdf.setFont("Helvetica", 11)
            pdf.drawString(55, y, text)
            y -= 26
    pdf.rect(42, 42, PAGE_WIDTH - 84, PAGE_HEIGHT - 84)
    pdf.showPage()
    pdf.save()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def invoice_image(record: dict) -> Image.Image:
    image = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    margin = 120
    draw.rectangle(
        (margin - 25, margin - 25, IMAGE_SIZE[0] - margin + 25, IMAGE_SIZE[1] - margin + 25),
        outline=(45, 60, 70),
        width=3,
    )
    y = margin
    for style, text in invoice_lines(record):
        if style == "company":
            selected_font = font(44, bold=True)
            draw.text((margin, y), text, font=selected_font, fill="black")
            y += 115
        elif style == "title":
            selected_font = font(58, bold=True)
            draw.text((margin, y), text, font=selected_font, fill=(20, 50, 75))
            y += 125
        elif style == "heading":
            y += 25
            selected_font = font(32, bold=True)
            draw.text((margin, y), text, font=selected_font, fill="black")
            y += 80
        elif style == "total":
            y += 45
            selected_font = font(34, bold=True)
            bbox = draw.textbbox((0, 0), text, font=selected_font)
            draw.text(
                (IMAGE_SIZE[0] - margin - (bbox[2] - bbox[0]), y),
                text,
                font=selected_font,
                fill="black",
            )
            y += 120
        elif style == "footer":
            selected_font = font(26)
            draw.text((margin, y), text, font=selected_font, fill=(70, 70, 70))
        else:
            selected_font = font(29)
            draw.text((margin, y), text, font=selected_font, fill="black")
            y += 70
    return image


def degrade_image(image: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    angle = rng.choice([-1.4, -0.9, 0.9, 1.3])
    degraded = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white")
    degraded = degraded.resize(
        (IMAGE_SIZE[0] // 2, IMAGE_SIZE[1] // 2),
        resample=Image.Resampling.BILINEAR,
    ).resize(IMAGE_SIZE, resample=Image.Resampling.BILINEAR)
    degraded = degraded.filter(ImageFilter.GaussianBlur(radius=0.55))

    pixels = degraded.load()
    for _ in range(9000):
        x = rng.randrange(0, IMAGE_SIZE[0])
        y = rng.randrange(0, IMAGE_SIZE[1])
        shade = rng.randrange(205, 248)
        pixels[x, y] = (shade, shade, shade)
    return degraded


def image_pdf(path: Path, image: Image.Image) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawImage(
        ImageReader(buffer),
        0,
        0,
        width=PAGE_WIDTH,
        height=PAGE_HEIGHT,
        preserveAspectRatio=False,
        mask="auto",
    )
    pdf.showPage()
    pdf.save()


def build() -> list[dict]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ground_truth: list[dict] = []
    conditions = ["native", "clean_scan", "degraded_scan"]

    for condition in conditions:
        for index, base in enumerate(BASE_INVOICES, start=1):
            prefix = {"native": "N", "clean_scan": "S", "degraded_scan": "D"}[condition]
            record = {
                **base,
                "invoice_number": f"OCR-{prefix}-{index:03d}",
                "currency": "MYR",
            }
            filename = f"{condition}_{index:02d}_{record['invoice_number']}.pdf"
            output_path = OUTPUT_DIR / filename

            if condition == "native":
                create_native_pdf(output_path, record)
            else:
                image = invoice_image(record)
                if condition == "degraded_scan":
                    image = degrade_image(image, seed=20260903 + index)
                image_pdf(output_path, image)

            ground_truth.append(
                {
                    "file": str(output_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "condition": condition,
                    **record,
                }
            )

    GROUND_TRUTH_PATH.write_text(
        json.dumps(ground_truth, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ground_truth


def main() -> None:
    records = build()
    print(f"Created {len(records)} OCR test PDFs in {OUTPUT_DIR}")
    print(f"Ground truth: {GROUND_TRUTH_PATH}")
    for condition in ("native", "clean_scan", "degraded_scan"):
        print(f"{condition}: {sum(item['condition'] == condition for item in records)}")


if __name__ == "__main__":
    main()
