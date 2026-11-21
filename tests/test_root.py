import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales import data  # noqa: E402
from sales.data_agent import extract, published_context  # noqa: E402
from sales.root import CONDITIONS, SalesAnswer, build_root_agent, review_evidence  # noqa: E402

MESSAGES = [
    {"systemMessage": {"text": {"parts": ["thinking"], "textType": "THOUGHT"}}},
    {"systemMessage": {"data": {"generatedSql": "SELECT 1"}}},
    {"systemMessage": {"data": {"result": {"name": "t", "data": [{"month": "2026-10", "sales": 10560000}]}}}},
    {"systemMessage": {"text": {"parts": ["確定売上は前月比 -12% です"], "textType": "FINAL_RESPONSE"}}},
]


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, variant, question):
        self.calls.append((variant, question))
        return MESSAGES


def run_tool(condition):
    client = FakeClient()
    agent = build_root_agent(condition, client_factory=lambda: client)
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
    assert sql.count("CREATE OR REPLACE TABLE") == 1
    assert sql.count("DATE '2026-") == 88
    # Column descriptions say what a column is, never how sales are defined.
    assert "売上" not in sql


def test_raw_and_defined_share_the_table_and_differ_only_in_definition():
    raw = published_context("p", "d", "raw")
    defined = published_context("p", "d", "defined")
    assert raw["datasourceReferences"] == defined["datasourceReferences"]
    assert "検収済" not in raw["systemInstruction"]
    assert defined["systemInstruction"].startswith(raw["systemInstruction"])
    assert "検収済" in defined["systemInstruction"]


def test_root_instruction_follows_the_condition():
    assert "検収済" not in build_root_agent("raw").instruction
    for condition in ("defined", "reuse"):
        assert "検収済" in build_root_agent(condition).instruction


def test_each_condition_changes_one_thing():
    assert CONDITIONS["raw"]["share_evidence"] == CONDITIONS["defined"]["share_evidence"]
    assert CONDITIONS["defined"]["variant"] == CONDITIONS["reuse"]["variant"]

    client, result, again = run_tool("raw")
    assert [v for v, _ in client.calls] == ["raw", "raw"]
    assert result["data_agent_calls"] == 2 and again["note"]

    client, result, _ = run_tool("defined")
    assert [v for v, _ in client.calls] == ["defined", "defined"]
    assert client.calls[0][1] == data.QUESTION

    client, result, _ = run_tool("reuse")
    assert [v for v, _ in client.calls] == ["defined"]
    assert result["data_agent_calls"] == 1 and result["reviewed_same_evidence"] is True


def test_extract_and_review():
    evidence = extract(MESSAGES)
    assert evidence["answer"] == "確定売上は前月比 -12% です" and evidence["sql"] == ["SELECT 1"]
    assert review_evidence(evidence)["passed"] is True
    assert review_evidence(extract([]))["passed"] is False


def test_answer_has_a_fixed_shape_and_no_derived_numbers():
    for condition in CONDITIONS:
        assert build_root_agent(condition).output_schema is SalesAnswer
    fields = set(SalesAnswer.model_fields)
    assert {"counted_orders", "previous_month_jpy", "current_month_jpy", "summary_ja"} <= fields
    # The difference and the rate are left to code, so the model cannot get them wrong.
    assert not {f for f in fields if "diff" in f or "rate" in f or "pct" in f}
