"""Synthetic e-commerce fixture: higher August gross sales, lower profit.

Taken from the Data & AI Summit '26 Fall hands-on
(github.com/mbk-digital/data-ai-summit-2026-fall-handson). All amounts are
integer JPY. Five tables, 28 rows, small enough to check every answer by hand.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_TEMPLATE = Path(__file__).with_name("schema.sql")

FIELDS = {
    "products": ["product_id", "product_name", "category"],
    "campaigns": ["campaign_id", "campaign_name"],
    "orders": ["order_id", "ordered_date", "status"],
    "order_items": [
        "order_item_id", "order_id", "product_id", "campaign_id",
        "quantity", "unit_price_jpy", "discount_jpy", "unit_cost_jpy",
    ],
    "refunds": [
        "refund_id", "order_item_id", "refunded_date",
        "refund_amount_jpy", "returned_quantity", "recovered_cost_jpy",
    ],
}
DATE_FIELDS = {"ordered_date", "refunded_date"}

# Sale and refund events under the company's definition: COMPLETED orders only,
# refunds recognised on the refund date, only the recovered cost returned.
EVENT_SELECT = """
SELECT 'SALE' AS event_type, i.order_item_id AS event_id,
       o.ordered_date AS event_date, o.order_id, i.order_item_id,
       i.product_id, p.product_name, p.category, i.campaign_id,
       i.quantity AS sold_quantity, 0 AS returned_quantity,
       i.quantity * i.unit_price_jpy AS gross_sales_jpy,
       i.discount_jpy AS discount_jpy, 0 AS refund_jpy,
       i.quantity * i.unit_price_jpy - i.discount_jpy AS net_sales_jpy,
       i.quantity * i.unit_cost_jpy AS cogs_jpy,
       i.quantity * i.unit_price_jpy - i.discount_jpy
         - i.quantity * i.unit_cost_jpy AS gross_profit_jpy
FROM {order_items} i JOIN {orders} o ON o.order_id = i.order_id
JOIN {products} p ON p.product_id = i.product_id
WHERE o.status = 'COMPLETED'
UNION ALL
SELECT 'REFUND', r.refund_id, r.refunded_date, o.order_id, i.order_item_id,
       i.product_id, p.product_name, p.category, i.campaign_id,
       0, r.returned_quantity, 0, 0, r.refund_amount_jpy,
       -r.refund_amount_jpy, -r.recovered_cost_jpy,
       -r.refund_amount_jpy + r.recovered_cost_jpy
FROM {refunds} r JOIN {order_items} i ON i.order_item_id = r.order_item_id
JOIN {orders} o ON o.order_id = i.order_id
JOIN {products} p ON p.product_id = i.product_id
WHERE o.status = 'COMPLETED'
"""


def fixture() -> dict[str, list[tuple]]:
    rows: dict[str, list[tuple]] = {
        "products": [
            ("P-A", "ワイヤレスヘッドホン", "Electronics"),
            ("P-B", "コーヒーメーカー", "Home"),
            ("P-C", "Tシャツ", "Apparel"),
        ],
        "campaigns": [("NONE", "通常販売"), ("SUMMER", "夏のセール")],
        "orders": [],
        "order_items": [],
        "refunds": [],
    }
    # id, date, product, quantity, unit price, line discount, unit cost, campaign, status
    sales = [
        ("J-A", "2026-07-10", "P-A", 10, 10000, 0, 6000, "NONE", "COMPLETED"),
        ("J-B", "2026-07-11", "P-B", 10, 8000, 0, 4000, "NONE", "COMPLETED"),
        ("J-C", "2026-07-12", "P-C", 20, 2000, 0, 800, "NONE", "COMPLETED"),
        ("A-A", "2026-08-10", "P-A", 20, 10000, 40000, 6000, "SUMMER", "COMPLETED"),
        ("A-B", "2026-08-11", "P-B", 20, 8000, 10000, 4000, "SUMMER", "COMPLETED"),
        ("A-C", "2026-08-12", "P-C", 30, 2000, 15000, 800, "SUMMER", "COMPLETED"),
        ("A-X", "2026-08-13", "P-A", 100, 10000, 0, 6000, "NONE", "CANCELLED"),
        # Shipped but not completed. The company's definition does not count it.
        # An agent that does will pick Apparel as the largest decline.
        ("A-Y", "2026-08-14", "P-A", 20, 10000, 0, 6000, "NONE", "SHIPPED"),
        ("S-A", "2026-09-01", "P-A", 1, 10000, 0, 6000, "NONE", "COMPLETED"),
    ]
    for key, date, product, qty, price, discount, cost, campaign, status in sales:
        rows["orders"].append(("O-" + key, date, status))
        rows["order_items"].append(("I-" + key, "O-" + key, product, campaign, qty, price, discount, cost))
    rows["refunds"] = [
        ("R-A1", "I-A-A", "2026-08-20", 32000, 4, 24000),
        ("R-A2", "I-A-A", "2026-08-22", 48000, 6, 0),
        ("R-B1", "I-A-B", "2026-08-25", 15000, 2, 8000),
        ("R-J1", "I-J-A", "2026-08-03", 20000, 2, 12000),
        ("R-S1", "I-A-C", "2026-09-02", 1500, 1, 800),
    ]
    return rows


def _literal(field: str, value) -> str:
    if isinstance(value, int):
        return str(value)
    text = "'" + str(value).replace("'", "\\'") + "'"
    return "DATE " + text if field in DATE_FIELDS else text


def seed_sql(project: str, dataset: str) -> str:
    """One BigQuery script that recreates the five tables, the rows and the view."""
    ref = lambda table: f"`{project}.{dataset}.{table}`"
    ddl = SCHEMA_TEMPLATE.read_text(encoding="utf-8")
    ddl = ddl.replace("{{PROJECT_ID}}", project).replace("{{DATASET_ID}}", dataset)
    ddl = ddl.replace("CREATE TABLE IF NOT EXISTS", "CREATE OR REPLACE TABLE")
    statements = [ddl]
    for table, records in fixture().items():
        values = ",\n  ".join(
            "(" + ", ".join(_literal(f, v) for f, v in zip(FIELDS[table], record)) + ")"
            for record in records
        )
        statements.append(f"INSERT INTO {ref(table)} ({', '.join(FIELDS[table])}) VALUES\n  {values};")
    view = EVENT_SELECT.format(**{t: ref(t) for t in ("order_items", "orders", "products", "refunds")})
    statements.append(f"CREATE OR REPLACE VIEW {ref('v_revenue_events')} AS{view};")
    return "\n".join(statements)
