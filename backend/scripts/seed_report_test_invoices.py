"""Seed the local SQLite database with 100 ESG-report test invoices.

The existing ten invoices are retained. This script idempotently adds invoice
numbers INV-2026-0011 through INV-2026-0100. Run
``classify_report_test_invoices.py`` afterwards to classify the new records
through the real RAG and LLM pipeline.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_ROOT))

from app.db.database import init_db  # noqa: E402
from app.schemas import InvoiceCreate  # noqa: E402
from app.services.invoice_service import create_invoice  # noqa: E402


BUYER = "ABC Manufacturing Sdn Bhd"

TEMPLATES: dict[str, list[tuple[str, str, float]]] = {
    "Environmental": [
        ("SolarTech Energy Sdn Bhd", "Rooftop solar system maintenance and inverter performance inspection", 4850.00),
        ("EcoWaste Resource Recovery", "Scheduled waste treatment and recycling service for production materials", 1320.00),
        ("AquaSave Engineering", "Water-efficiency audit and leak-reduction maintenance for factory operations", 2760.00),
        ("GreenFleet Mobility", "Electric forklift lease and charging support for warehouse operations", 3950.00),
        ("BrightPath Lighting", "LED lighting retrofit for energy-efficient production areas", 6280.00),
        ("RainHarvest Solutions", "Rainwater harvesting system maintenance for non-potable facility use", 2190.00),
        ("CircularPack Industries", "Recycled-content packaging materials for finished goods", 3475.00),
        ("CarbonTrack Advisory", "Greenhouse-gas inventory measurement and carbon data verification service", 5400.00),
        ("CleanAir Industrial Services", "Air-emissions monitoring and filtration maintenance", 4180.00),
        ("Renewable Energy Certificates MY", "Renewable electricity certificate purchase for 2026 operations", 3650.00),
        ("EcoLandscapes Sdn Bhd", "Native tree planting and site restoration service", 2890.00),
    ],
    "Social": [
        ("SafeWork Academy", "Occupational safety and health refresher training for production employees", 2280.00),
        ("MediCare Clinic Sdn Bhd", "Employee medical screening and preventive health assessment package", 3140.00),
        ("SkillUp Learning Solutions", "Technical upskilling and career-development workshop for staff", 2675.00),
        ("Wellbeing Partners", "Employee mental-health counselling and wellness programme", 1980.00),
        ("ErgoSafe Supplies", "Ergonomic workstation assessments and equipment for office employees", 1760.00),
        ("Inclusive Talent Consultants", "Diversity, equity and inclusion training for managers", 2350.00),
        ("Community STEM Foundation", "STEM education programme sponsorship for local secondary-school students", 5000.00),
        ("FirstAid Response Malaysia", "First-aid certification and emergency-response training", 1890.00),
        ("WorkLife Support Services", "Employee childcare support and family wellbeing programme", 3210.00),
        ("ApprenticeWorks Sdn Bhd", "Structured apprenticeship placement and mentoring programme", 4120.00),
        ("PPE Safety Equipment", "Personal protective equipment for manufacturing employees", 3575.00),
    ],
    "Governance": [
        ("SecureAudit Consulting", "Internal controls audit and corporate-governance review service", 4650.00),
        ("DataShield Advisory", "Personal-data protection compliance assessment and policy review", 3980.00),
        ("Integrity First Malaysia", "Anti-bribery and corruption compliance training for employees", 2850.00),
        ("WhistleLine Services", "Independent whistleblowing hotline and case-management subscription", 2400.00),
        ("CyberGuard Assurance", "Cybersecurity controls audit and vulnerability assessment", 5760.00),
        ("Regulatory Counsel Partners", "Regulatory compliance legal advisory for manufacturing operations", 4920.00),
        ("BoardWorks Institute", "Board governance and fiduciary-duties workshop", 3680.00),
        ("Ethical Supply Chain Audit", "Supplier code-of-conduct due-diligence assessment", 4370.00),
        ("Assure ESG Partners", "Independent ESG disclosure assurance readiness review", 6150.00),
        ("Tax Governance Advisors", "Tax compliance controls review and documentation service", 3310.00),
        ("Records Compliance Solutions", "Records-retention policy and information-governance review", 2590.00),
        ("RiskRegister Systems", "Enterprise risk-register software subscription and configuration", 4220.00),
    ],
    "Non-ESG": [
        ("OfficeMart Supplies", "General office stationery including pens, files and printer paper", 430.00),
        ("Cafe Rasa Enterprise", "Refreshments and catering for internal monthly meeting", 760.00),
        ("PrintPro Services", "Routine business-card and brochure printing service", 1280.00),
        ("Express Courier Malaysia", "Domestic courier charges for routine customer deliveries", 890.00),
        ("Workspace Decor Studio", "Office decorative items and reception-area accessories", 1560.00),
        ("IT Peripherals Store", "Standard computer mice, keyboards and monitor cables", 2240.00),
        ("CleanOffice Supplies", "General cleaning supplies for office pantry and washrooms", 1185.00),
        ("Meeting Room Rentals", "External meeting-room rental for sales planning session", 950.00),
        ("Corporate Gifts Trading", "Promotional merchandise for customer appreciation event", 2840.00),
        ("Vehicle Service Centre", "Routine passenger-vehicle servicing and replacement tyres", 3290.00),
        ("MailBox Business Services", "Postage, document binding and routine administrative services", 690.00),
        ("Office Furniture Outlet", "Standard visitor chairs and storage cabinets", 4750.00),
    ],
}

# Existing records are Environmental 3, Social 4, Governance 1, and Non-ESG 2.
# These additions make every category total exactly 25 records.
TARGET_ADDITIONS = {
    "Environmental": 22,
    "Social": 21,
    "Governance": 24,
    "Non-ESG": 23,
}


def test_records() -> list[tuple[InvoiceCreate, str]]:
    records: list[tuple[InvoiceCreate, str]] = []
    invoice_number = 11
    invoice_date = date(2026, 1, 6)

    for category, count in TARGET_ADDITIONS.items():
        templates = TEMPLATES[category]
        for index in range(count):
            supplier, description, base_amount = templates[index % len(templates)]
            amount = round(base_amount + (index // len(templates)) * 175.0, 2)
            record_date = invoice_date + timedelta(days=(invoice_number - 11) * 4)
            records.append(
                (
                    InvoiceCreate(
                        invoice_number=f"INV-2026-{invoice_number:04d}",
                        supplier_name=supplier,
                        buyer_name=BUYER,
                        invoice_date=record_date.isoformat(),
                        amount=amount,
                        currency="MYR",
                        description=f"{description} ({record_date.strftime('%B %Y')}).",
                    ),
                    category,
                )
            )
            invoice_number += 1

    return records


def main() -> None:
    init_db()
    processed = 0

    for payload, _category in test_records():
        create_invoice(payload)
        processed += 1

    print(f"Processed {processed} synthetic invoices without classifying them.")


if __name__ == "__main__":
    main()
