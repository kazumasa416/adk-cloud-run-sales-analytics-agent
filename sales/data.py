"""The simplest fixture that makes "what counts as sales?" matter.

One table, `orders`, with four columns. An order is 受注 (ordered, not yet
accepted by the customer), 検収済 (accepted: this is when the company books
the sale) or 取消 (cancelled).

                      September     October      change
    検収済 only        12,000,000    10,560,000   -12.0%   <- the company's "sales"
    受注 + 検収済      12,600,000    13,530,000    +7.4%
    every row          13,500,000    14,190,000    +5.1%

The same question gives an increase or a decrease depending on which rows
are counted. All amounts are integer JPY, tax excluded.
"""

from __future__ import annotations

TABLE = "orders"
FIELDS = ["order_id", "order_date", "status", "amount_jpy"]
DESCRIPTIONS = {
    "order_id": "注文 ID",
    "order_date": "注文日",
    "status": "注文の状態：受注、検収済、取消",
    "amount_jpy": "注文金額（円、税抜）",
}
# (month, status, number of orders, amount per order)
PLAN = [
    ("2026-09", "検収済", 40, 300_000),
    ("2026-09", "受注", 2, 300_000),
    ("2026-09", "取消", 3, 300_000),
    ("2026-10", "検収済", 32, 330_000),
    ("2026-10", "受注", 9, 330_000),
    ("2026-10", "取消", 2, 330_000),
]
QUESTION = "10月の売上は、9月と比べてどうでしたか？"


def fixture() -> list[tuple[str, str, str, int]]:
    rows = []
    for month, status, count, amount in PLAN:
        start = len([r for r in rows if r[1].startswith(month)])
        for index in range(count):
            number = start + index
            day = number % 28 + 1
            rows.append((f"O-{month[5:]}-{number + 1:03d}", f"{month}-{day:02d}", status, amount))
    return rows


def totals(statuses: set[str]) -> dict[str, int]:
    """Monthly totals over the given statuses, for checking answers by hand."""
    result: dict[str, int] = {}
    for _, date, status, amount in fixture():
        if status in statuses:
            result[date[:7]] = result.get(date[:7], 0) + amount
    return result


# Marks the table as created by this demo. provision.py refuses to touch a
# table named `orders` that does not carry it.
LABEL = ("managed-by", "adk-cloud-run-sales-demo")


def seed_sql(project: str, dataset: str) -> str:
    """One BigQuery script that creates the table and its rows.

    It never replaces a table: if `orders` already exists the script fails.
    """
    table = f"`{project}.{dataset}.{TABLE}`"
    columns = ",\n  ".join(
        f"{name} {kind} NOT NULL OPTIONS(description='{DESCRIPTIONS[name]}')"
        for name, kind in zip(FIELDS, ["STRING", "DATE", "STRING", "INT64"])
    )
    values = ",\n  ".join(
        f"('{order_id}', DATE '{date}', '{status}', {amount})" for order_id, date, status, amount in fixture()
    )
    return (
        f"CREATE TABLE {table} (\n  {columns}\n)\n"
        f"OPTIONS(labels=[('{LABEL[0]}', '{LABEL[1]}')]);\n"
        f"INSERT INTO {table} ({', '.join(FIELDS)}) VALUES\n  {values};"
    )
