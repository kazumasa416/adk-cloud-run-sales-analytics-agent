import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales import data  # noqa: E402
from sales.catalog import CatalogClient  # noqa: E402
from sales.data_agent import (  # noqa: E402
    DataAgentClient, DataAgentError, agent_id, differences, extract, published_context, starter_rules,
)
from sales.root import CONDITIONS, SalesAnswer, build_root_agent, review_evidence  # noqa: E402

MESSAGES = [
    {"systemMessage": {"text": {"parts": ["thinking"], "textType": "THOUGHT"}}},
    {"systemMessage": {"data": {"generatedSql": "SELECT 1"}}},
    {"systemMessage": {"data": {"result": {"name": "t", "data": [{"month": "2026-10", "sales": 10560000}]}}}},
    {"systemMessage": {"text": {"parts": ["確定売上は前月比 -12% です"], "textType": "FINAL_RESPONSE"}}},
]


RECEIPT = {"entry": "projects/p/locations/us/glossaries/sales-demo-glossary/terms/confirmed-sales",
           "title": "確定売上", "update_time": "2026-11-21T00:00:00Z", "sha256": "a" * 64, "version": "a" * 12,
           "body": starter_rules()}


def build(condition, **kwargs):
    """Tests never reach the Catalog: the definition is handed in."""
    return build_root_agent(condition, definition_loader=lambda: RECEIPT, **kwargs)


class FakeClient:
    def __init__(self):
        self.calls = []

    def name(self, variant):
        return "agents/" + agent_id(variant)

    def chat(self, agent, question):
        self.calls.append((agent, question))
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


def test_agents_share_instructions_but_only_defined_uses_the_linked_table():
    raw = published_context("p", "d", "raw")
    defined = published_context("p", "d", "defined")
    for context, table in [(raw, data.TABLE), (defined, data.GLOSSARY_TABLE)]:
        assert context["datasourceReferences"]["bq"]["tableReferences"] == [
            {"projectId": "p", "datasetId": "d", "tableId": table}
        ]
    assert raw["systemInstruction"] == defined["systemInstruction"]
    assert "検収済" not in defined["systemInstruction"]


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


def test_catalog_links_the_defined_table_to_the_glossary_term():
    client = CatalogClient("p", "us", lambda: "token")
    name, payload = client.table_link("123", "d", data.GLOSSARY_TABLE)
    source, target = payload["entryReferences"]
    assert source["type"] == "SOURCE" and source["name"].endswith("/projects/p/datasets/d/tables/orders_glossary")
    assert target["type"] == "TARGET" and target["name"].endswith(
        "/@dataplex/entries/projects/123/locations/us/glossaries/sales-demo-glossary/terms/confirmed-sales"
    )
    assert "/entryGroups/@bigquery/entryLinks/" in name
    # Dataplex normalizes the public type's project ID to its project number.
    persisted = dict(payload, entryLinkType="projects/123/locations/global/entryLinkTypes/definition")
    assert client.matches_link(persisted, payload)
    assert not client.matches_link(persisted, client.table_link("123", "d", data.TABLE)[1])


def test_an_existing_data_agent_is_reused_only_for_the_same_definition_and_table():
    held = {"dataAnalyticsAgent": {"publishedContext": published_context("p", "d", "defined")}}
    assert differences(held, published_context("p", "d", "defined")) == []

    client = DataAgentClient("p", "us", lambda: "t")
    requests = []

    def request(method, url, payload=None, timeout=330):
        requests.append(method)
        return held

    client._request = request
    assert client.ensure("defined", "d") == (client.name("defined"), False)
    # Same instruction, another dataset: the agent would still analyse the old table.
    with pytest.raises(DataAgentError, match="other tables"):
        client.ensure("defined", "another_dataset")
    held["dataAnalyticsAgent"]["publishedContext"]["systemInstruction"] += starter_rules()
    with pytest.raises(DataAgentError, match="different instruction"):
        client.ensure("defined", "d")
    # An existing agent is read, never written.
    assert set(requests) == {"GET"}


def test_catalog_term_is_read_into_a_receipt():
    entry = {"name": RECEIPT["entry"], "updateTime": "2026-11-21T01:02:03Z",
             "displayName": "確定売上", "description": " 検収済だけを数える "}
    client = CatalogClient("p", "us", lambda: "t")
    client._request = lambda method, path, payload=None, missing_ok=False: entry
    got = client.definition()
    assert got["body"] == "検収済だけを数える" and got["update_time"] == "2026-11-21T01:02:03Z"
    assert len(got["sha256"]) == 64 and got["entry"] == client.term
    assert got["version"] == got["sha256"][:12]


def test_the_data_agent_is_asked_once_per_question():
    assert set(CONDITIONS) == {"raw", "defined"}
    for condition in CONDITIONS:
        # The tool runs twice in run_tool; the second run reuses the first result.
        client, result, again = run_tool(condition)
        agent = {"raw": "agents/sales-raw", "defined": "agents/sales-defined"}[condition]
        assert client.calls == [(agent, data.QUESTION)]
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


def test_existing_glossary_term_keeps_edited_definition(monkeypatch):
    from sales.catalog import GLOSSARY_DESCRIPTION
    catalog = CatalogClient("p", "us", lambda: "token")
    resources = {catalog.glossary: {"description": GLOSSARY_DESCRIPTION},
                 catalog.term: {"name": catalog.term, "displayName": "確定売上", "description": "編集済み"}}

    def existing(method, path, **kwargs):
        assert method == "GET"
        return resources[path]

    monkeypatch.setattr(catalog, "_request", existing)
    catalog.ensure_term(starter_rules())
    assert catalog.definition()["body"] == "編集済み"


def test_provision_uses_verified_glossary_configuration(monkeypatch):
    from collections import Counter
    from scripts import provision
    monkeypatch.setenv("PROJECT_ID", "p")
    monkeypatch.setenv("BQ_DATASET", "d")
    monkeypatch.setattr(sys, "argv", ["provision.py"])
    monkeypatch.setattr(provision, "gcloud", lambda *args: "123")
    seen = []

    def bigquery(method, path, payload, project):
        if method == "GET":
            return {"labels": dict([data.LABEL])} if path.endswith("/orders") else None
        if path.endswith("/queries"):
            sql = payload["query"]
            if "GROUP BY status ORDER BY status" in sql:
                counts = Counter(r[2] for r in data.fixture())
                return {"rows": [{"f": [{"v": status}, {"v": n}]} for status, n in counts.items()]}
            if "CREATE TABLE" in sql:
                assert "CREATE TABLE `p.d.orders_glossary` CLONE `p.d.orders`" in sql
                seen.append("clone")
            return {"jobComplete": True, "rows": [{"f": [{"v": "0"}]}]}
        return {}

    monkeypatch.setattr(provision, "bq", bigquery)
    monkeypatch.setattr(CatalogClient, "ensure_term", lambda self, body: seen.append(body))
    monkeypatch.setattr(CatalogClient, "ensure_link", lambda self, *args: seen.append(args))
    def ensure(self, variant, dataset):
        seen.append((variant, dataset))
        return self.name(variant), True
    monkeypatch.setattr(DataAgentClient, "ensure", ensure)
    provision.main()
    assert seen == ["clone", starter_rules(), ("123", "d", "orders_glossary"), ("raw", "d"), ("defined", "d")]
