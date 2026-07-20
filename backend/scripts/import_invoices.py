import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_ROOT))

from app.db.database import init_db  # noqa: E402
from app.schemas import InvoiceCreate  # noqa: E402
from app.services.invoice_service import create_invoice  # noqa: E402


def main() -> None:
    init_db()
    invoice_dir = ROOT / "Invoice"
    json_files = sorted(invoice_dir.glob("*.json"))

    for path in json_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        payload = InvoiceCreate(
            invoice_number=str(data.get("invoice_number") or data.get("invoice_id")),
            supplier_name=str(data.get("supplier_name")),
            buyer_name=data.get("buyer_name"),
            invoice_date=str(data.get("invoice_date")),
            amount=float(data.get("amount") or 0),
            currency=str(data.get("currency") or "MYR"),
            description=str(data.get("description")),
        )
        invoice = create_invoice(payload)
        print(f"Imported {invoice.invoice_number}")


if __name__ == "__main__":
    main()
