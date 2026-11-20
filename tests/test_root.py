import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ec import data  # noqa: E402
from ec.data_agent import extract, published_context  # noqa: E402
from ec.root import CONDITIONS, build_root_agent, review_evidence  # noqa: E402

MESSAGES = [
    {"systemMessage": {"text": {"parts": ["thinking"], "textType": "THOUGHT"}}},
    {"systemMessage": {"data": {"generatedSql": "SELECT 1"}}},
    {"systemMessage": {"data": {"result": {"name": "t", "data": [{"category": "Electronics", "diff": -64000}]}}}},
    {"systemMessage": {"text": {"parts": ["Electronics, -64,000 円"], "textType": "FINAL_RESPONSE"}}},
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
    ctx = SimpleNamespace(state={}, user_content=SimpleNamespace(parts=[SimpleNamespace(text="Q04")]))
    result = asyncio.run(tool(ctx))
    again = asyncio.run(tool(ctx))
    return client, result, again


def test_fixture_is_five_tables_and_28_rows():
    rows = data.fixture()
    assert list(rows) == ["products", "campaigns", "orders", "order_items", "refunds"]
    assert sum(len(r) for r in rows.values()) == 28
    sql = data.seed_sql("demo-project", "ec_demo")
    assert sql.count("CREATE OR REPLACE TABLE") == 5 and "v_revenue_events" in sql


def test_raw_and_defined_share_tables_and_differ_only_in_definition():
    raw = published_context("p", "d", "raw")
    defined = published_context("p", "d", "defined")
    assert raw["datasourceReferences"] == defined["datasourceReferences"]
    assert "v_revenue_events" not in str(raw["datasourceReferences"])
    assert "COMPLETED" not in raw["systemInstruction"]
    assert defined["systemInstruction"].startswith(raw["systemInstruction"])
    assert "Include only COMPLETED orders" in defined["systemInstruction"]


def test_root_instruction_follows_the_condition():
    assert "COMPLETED" not in build_root_agent("raw").instruction
    for condition in ("defined", "reuse"):
        assert "Include only COMPLETED orders" in build_root_agent(condition).instruction


def test_each_condition_changes_one_thing():
    assert CONDITIONS["raw"]["share_evidence"] == CONDITIONS["defined"]["share_evidence"]
    assert CONDITIONS["defined"]["variant"] == CONDITIONS["reuse"]["variant"]

    client, result, again = run_tool("raw")
    assert [v for v, _ in client.calls] == ["raw", "raw"]
    assert result["data_agent_calls"] == 2 and again["note"]

    client, result, _ = run_tool("defined")
    assert [v for v, _ in client.calls] == ["defined", "defined"]
    assert client.calls[0][1] == "Q04"

    client, result, _ = run_tool("reuse")
    assert [v for v, _ in client.calls] == ["defined"]
    assert result["data_agent_calls"] == 1 and result["reviewed_same_evidence"] is True
    assert result["answer"].startswith("Electronics")


def test_extract_and_review():
    evidence = extract(MESSAGES)
    assert evidence["answer"] == "Electronics, -64,000 円" and evidence["sql"] == ["SELECT 1"]
    assert review_evidence(evidence)["passed"] is True
    assert review_evidence(extract([]))["passed"] is False
