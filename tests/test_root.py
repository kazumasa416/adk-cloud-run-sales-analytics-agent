import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales import data  # noqa: E402
from sales.catalog import CatalogClient  # noqa: E402
from sales.data_agent import extract, holds_definition, published_context, starter_rules  # noqa: E402
from sales.root import CONDITIONS, SalesAnswer, build_root_agent, review_evidence  # noqa: E402

MESSAGES = [
    {"systemMessage": {"text": {"parts": ["thinking"], "textType": "THOUGHT"}}},
    {"systemMessage": {"data": {"generatedSql": "SELECT 1"}}},
    {"systemMessage": {"data": {"result": {"name": "t", "data": [{"month": "2026-10", "sales": 10560000}]}}}},
    {"systemMessage": {"text": {"parts": ["確定売上は前月比 -12% です"], "textType": "FINAL_RESPONSE"}}},
]


RECEIPT = {"entry": "projects/p/locations/us/entryGroups/sales-demo/entries/confirmed-sales",
           "title": "確定売上", "update_time": "2026-11-21T00:00:00Z", "sha256": "a" * 64, "body": starter_rules()}


def build(condition, **kwargs):
    """Tests never reach the Catalog: the definition is handed in."""
    return build_root_agent(condition, definition_loader=lambda: RECEIPT, **kwargs)


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, variant, question):
        self.calls.append((variant, question))
        return MESSAGES


def run_tool(condition):
    client = FakeClient()
    agent = build(condition, client_factory=lambda: client)
    tool = agent.tools[0]
    ctx = SimpleNamespace(state={}, user_content=SimpleNamespace(parts=[SimpleNamespace(text=data.QUESTION)]))
    result = asyncio.run(tool(ctx))
    again = asyncio.run(tool(ctx))
    return client, result, again


def test_the_answer_depends_on_which_orders_count():
    assert len(data.fixture()) == 88
    assert len({row[0] for row in data.fixture()}) == 88

    confirmed = data.totals({"検収済"})
    assert confirmed == {"2026-09": 12_000_000, "2026-10": 10_560_000}  # -12.0%

    not_cancelled = data.totals({"受注", "検収済"})
    assert not_cancelled == {"2026-09": 12_600_000, "2026-10": 13_530_000}  # +7.4%

    change = lambda t: round((t["2026-10"] - t["2026-09"]) / t["2026-09"] * 100, 1)
    assert change(confirmed) == -12.0 and change(not_cancelled) == 7.4


def test_seed_sql_creates_one_table():
    sql = data.seed_sql("demo-project", "sales_demo")
    # The script can create the table but never replace one that is already there.
    assert sql.count("CREATE TABLE") == 1 and "OR REPLACE" not in sql and "DROP" not in sql
    assert "managed-by" in sql
    assert sql.count("DATE '2026-") == 88
    # Column descriptions say what a column is, never how sales are defined.
    assert "売上" not in sql


def test_raw_and_defined_share_the_table_and_differ_only_in_definition():
    raw = published_context("p", "d", "raw")
    defined = published_context("p", "d", "defined", starter_rules())
    assert raw["datasourceReferences"] == defined["datasourceReferences"]
    assert "検収済" not in raw["systemInstruction"]
    assert defined["systemInstruction"].startswith(raw["systemInstruction"])
    assert "検収済" in defined["systemInstruction"]


def test_root_instruction_follows_the_condition():
    assert "検収済" not in build("raw").instruction
    assert "検収済" in build("defined").instruction


def test_the_definition_comes_from_the_catalog_and_leaves_a_receipt():
    # "raw" must not read the Catalog at all.
    def no_catalog():
        raise AssertionError("raw read the Catalog")
    build_root_agent("raw", definition_loader=no_catalog)

    _, raw, _ = run_tool("raw")
    _, defined, _ = run_tool("defined")
    assert raw["definition"] is None
    assert defined["definition"] == {k: RECEIPT[k] for k in ("entry", "update_time", "sha256")}

    # Without the Catalog text there is no "defined" Data Agent: no local fallback.
    with pytest.raises(ValueError):
        published_context("p", "d", "defined")


def test_the_data_agent_must_hold_the_same_definition_as_the_catalog():
    agent = {"dataAnalyticsAgent": {"publishedContext": published_context("p", "d", "defined", starter_rules())}}
    assert holds_definition(agent, starter_rules())
    assert not holds_definition(agent, starter_rules().replace("検収済", "受注"))
    assert not holds_definition(None, starter_rules())


def test_catalog_entry_is_read_into_a_receipt():
    entry = {"name": "projects/p/locations/us/entryGroups/sales-demo/entries/confirmed-sales",
             "updateTime": "2026-11-21T01:02:03Z", "entrySource": {"displayName": "確定売上"},
             "aspects": {"x.global.overview": {"aspectType": "projects/dataplex-types/locations/global/aspectTypes/overview",
                                               "data": {"content": " 検収済だけを数える \n"}}}}
    client = CatalogClient("p", "us", lambda: "t")
    client._request = lambda method, path, payload=None, missing_ok=False: entry
    got = client.definition()
    assert got["body"] == "検収済だけを数える" and got["update_time"] == "2026-11-21T01:02:03Z"
    assert len(got["sha256"]) == 64 and got["entry"].endswith("/entries/confirmed-sales")


def test_the_data_agent_is_asked_once_per_question():
    assert set(CONDITIONS) == {"raw", "defined"}
    for condition in CONDITIONS:
        # The tool runs twice in run_tool; the second run reuses the first result.
        client, result, again = run_tool(condition)
        assert client.calls == [(condition, data.QUESTION)]
        assert result["review"]["passed"] is True and again["note"]
        assert result["answer"] == "確定売上は前月比 -12% です"


def test_extract_and_review():
    evidence = extract(MESSAGES)
    assert evidence["answer"] == "確定売上は前月比 -12% です" and evidence["sql"] == ["SELECT 1"]
    assert review_evidence(evidence)["passed"] is True
    assert review_evidence(extract([]))["passed"] is False


def test_answer_has_a_fixed_shape_and_no_derived_numbers():
    for condition in CONDITIONS:
        assert build(condition).output_schema is SalesAnswer
    fields = set(SalesAnswer.model_fields)
    assert {"counted_orders", "previous_month_jpy", "current_month_jpy", "summary_ja"} <= fields
    # The difference and the rate are left to code, so the model cannot get them wrong.
    assert not {f for f in fields if "diff" in f or "rate" in f or "pct" in f}
