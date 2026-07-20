from app.db.database import db_session
from app.schemas import DashboardSummary, EsgCategory, SummaryBucket


CATEGORIES: list[EsgCategory] = [
    "Environmental",
    "Social",
    "Governance",
    "Non-ESG",
]


def get_dashboard_summary(period_start: str, period_end: str) -> DashboardSummary:
    with db_session() as db:
        invoice_rows = db.execute(
            """
            SELECT i.*, c.category
            FROM invoices i
            LEFT JOIN (
                SELECT c1.*
                FROM invoice_classifications c1
                WHERE c1.id = (
                    SELECT c2.id
                    FROM invoice_classifications c2
                    WHERE c2.invoice_id = c1.invoice_id
                      AND c2.invoice_version = c1.invoice_version
                    ORDER BY c2.created_at DESC, c2.id DESC
                    LIMIT 1
                )
            ) c ON c.invoice_id = i.id AND c.invoice_version = i.version
            WHERE i.invoice_date BETWEEN ? AND ?
            """,
            (period_start, period_end),
        ).fetchall()

    total_amounts: dict[str, float] = {}
    bucket_counts = {category: 0 for category in CATEGORIES}
    bucket_amounts: dict[EsgCategory, dict[str, float]] = {
        category: {} for category in CATEGORIES
    }

    unclassified_count = 0
    for row in invoice_rows:
        currency = str(row["currency"] or "MYR").upper()
        amount = float(row["amount"] or 0)
        total_amounts[currency] = total_amounts.get(currency, 0.0) + amount
        category = row["category"]
        if category not in bucket_counts:
            unclassified_count += 1
            continue
        bucket_counts[category] += 1
        category_amounts = bucket_amounts[category]
        category_amounts[currency] = category_amounts.get(currency, 0.0) + amount

    buckets = {
        category: _summary_bucket(bucket_counts[category], bucket_amounts[category])
        for category in CATEGORIES
    }

    return DashboardSummary(
        period={
            "label": get_period_label(period_start),
            "start": period_start,
            "end": period_end,
        },
        total_invoices=_summary_bucket(len(invoice_rows), total_amounts),
        categories=buckets,
        unclassified_count=unclassified_count,
    )


def _summary_bucket(count: int, amounts: dict[str, float]) -> SummaryBucket:
    normalized = {currency: round(amount, 2) for currency, amount in sorted(amounts.items())}
    if not normalized:
        return SummaryBucket(
            count=count,
            amount=0.0,
            currency="MYR",
            amounts_by_currency={},
        )
    if len(normalized) == 1:
        currency, amount = next(iter(normalized.items()))
        return SummaryBucket(
            count=count,
            amount=amount,
            currency=currency,
            amounts_by_currency=normalized,
        )
    return SummaryBucket(
        count=count,
        amount=0.0,
        currency="MIXED",
        amounts_by_currency=normalized,
    )


def get_period_label(period_start: str) -> str:
    year = period_start[:4]
    try:
        month = int(period_start[5:7])
    except ValueError:
        return f"{period_start} period"

    quarter = ((month - 1) // 3) + 1
    return f"{year}-Q{quarter}"
