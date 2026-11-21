"""Conversational Analytics (Data Agent) client: published context, create, chat.

Two agents look at the same `orders` table. The only difference is whether the
company's business definition is in the system instruction. The definition
itself comes from the Catalog (see sales/catalog.py):

    raw      orders table, no business definition
    defined  orders table, business definition

Keeping the data source identical is what makes the comparison fair.

A "defined" agent is never rewritten. Each version of the definition gets its
own agent, named after the hash of the text (sales-defined-<version>). A Root
Agent that started with one version keeps asking the agent of that version, so
an answer and the version recorded for it cannot drift apart while a new
definition is being rolled out.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {
    "us": "geminidataanalytics.us.rep.googleapis.com",
    "eu": "geminidataanalytics.eu.rep.googleapis.com",
    "global": "geminidataanalytics.googleapis.com",
}
TABLES = ["orders"]
VARIANTS = ("raw", "defined")
MARK = "adk-cloud-run-sales-demo"
DISPLAY_NAMES = {"raw": "売上分析（業務定義なし）", "defined": "売上分析（業務定義あり）"}


class DataAgentError(RuntimeError):
    pass


def baseline_instruction() -> str:
    return (ROOT / "semantics/baseline.txt").read_text(encoding="utf-8").strip()


def starter_rules() -> str:
    """The text the Catalog entry is first created with. Not read at run time."""
    return (ROOT / "semantics/business-rules.txt").read_text(encoding="utf-8").strip()


def published_context(project: str, dataset: str, variant: str, rules: str = "") -> dict:
    """`rules` is the definition read from the Catalog. Required for "defined"."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant: {variant}")
    instruction = baseline_instruction()
    if variant == "defined":
        if not rules.strip():
            raise ValueError("The defined variant needs the definition from the Catalog.")
        instruction += "\n\n" + rules.strip()
    return {
        "datasourceReferences": {"bq": {"tableReferences": [
            {"projectId": project, "datasetId": dataset, "tableId": table} for table in TABLES
        ]}},
        "systemInstruction": instruction,
        "options": {"datasource": {"bigQueryMaxBilledBytes": "104857600"}},
    }


def _published(agent: dict | None) -> dict:
    return (agent or {}).get("dataAnalyticsAgent", {}).get("publishedContext", {})


def _tables(context: dict) -> set[tuple]:
    references = context.get("datasourceReferences", {}).get("bq", {}).get("tableReferences", [])
    return {(r.get("projectId"), r.get("datasetId"), r.get("tableId")) for r in references}


def holds_definition(agent: dict | None, rules: str) -> bool:
    """True when the "defined" Data Agent was given exactly this definition."""
    return _published(agent).get("systemInstruction") == baseline_instruction() + "\n\n" + rules.strip()


def differences(agent: dict, context: dict) -> list[str]:
    """What an existing agent holds that differs from `context`. Empty when it can be reused."""
    published, found = _published(agent), []
    if published.get("systemInstruction") != context["systemInstruction"]:
        found.append("a different instruction")
    if _tables(published) != _tables(context):
        tables = ", ".join(sorted(".".join(str(part) for part in table) for table in _tables(published)))
        found.append(f"other tables ({tables or 'none'})")
    return found


def agent_id(variant: str, version: str = "") -> str:
    """`version` is the short hash of the definition. Only "defined" has one."""
    base = os.getenv(f"DATA_AGENT_ID_{variant.upper()}", f"sales-{variant}")
    return f"{base}-{version}" if version else base


class DataAgentClient:
    """Thin REST client. `token` returns a fresh OAuth access token."""

    def __init__(self, project: str, location: str, token: Callable[[], str]):
        if location not in HOSTS:
            raise DataAgentError(f"Unsupported Data Agent location: {location}")
        self.project = project
        self.parent = f"projects/{project}/locations/{location}"
        self.base = f"https://{HOSTS[location]}/v1/"
        self._token = token

    def name(self, variant: str, version: str = "") -> str:
        return f"{self.parent}/dataAgents/{agent_id(variant, version)}"

    def _request(self, method: str, url: str, payload: dict | None = None, timeout: int = 330):
        request = urllib.request.Request(
            url,
            method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + self._token(),
                "Content-Type": "application/json",
                "x-goog-user-project": self.project,
                "x-server-timeout": "300",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:2000]
            raise DataAgentError(f"HTTP {exc.code} from {method} {url.split('?')[0]}: {body}") from exc

    def get(self, variant: str, version: str = "") -> dict | None:
        try:
            return self._request("GET", self.base + self.name(variant, version))
        except DataAgentError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def ensure(self, variant: str, dataset: str, rules: str = "", version: str = "") -> tuple[str, bool]:
        """Create the agent when missing. Returns (resource name, created).

        An existing agent is reused only when it holds this instruction and
        looks at this dataset's table. It is never rewritten.
        """
        context = published_context(self.project, dataset, variant, rules)
        name = self.name(variant, version)
        existing = self.get(variant, version)
        if existing is not None:
            found = differences(existing, context)
            if found:
                raise DataAgentError(
                    f"{name} exists with {' and '.join(found)}. Nothing was changed. "
                    f"Delete it (scripts/cleanup.py) or set DATA_AGENT_ID_{variant.upper()} to a new id."
                )
            return name, False
        payload = {
            "name": name,
            "displayName": DISPLAY_NAMES[variant] + (f" {version}" if version else ""),
            "description": MARK + ":" + variant,
            "dataAnalyticsAgent": {"publishedContext": context},
        }
        url = f"{self.base}{self.parent}/dataAgents:createSync?dataAgentId={agent_id(variant, version)}"
        self._request("POST", url, payload)
        return name, True

    def created_by_this_demo(self) -> list[str]:
        """Resource names of every Data Agent in the project that carries this demo's mark."""
        names, page = [], ""
        while True:
            url = f"{self.base}{self.parent}/dataAgents?pageSize=100" + (f"&pageToken={page}" if page else "")
            response = self._request("GET", url) or {}
            names += [a["name"] for a in response.get("dataAgents", []) if a.get("description", "").startswith(MARK)]
            page = response.get("nextPageToken", "")
            if not page:
                return names

    def delete(self, name: str) -> None:
        self._request("DELETE", self.base + name + ":deleteSync")

    def chat(self, agent: str, question: str) -> list[dict]:
        """Ask the agent with this resource name one question. The streaming response is one JSON array at EOF."""
        payload = {
            "parent": self.parent,
            "messages": [{"userMessage": {"text": question}}],
            "dataAgentContext": {"dataAgent": agent},
        }
        messages = self._request("POST", f"{self.base}{self.parent}:chat", payload)
        return messages if isinstance(messages, list) else [messages]


def extract(messages: list[dict]) -> dict:
    """Final answer text, generated SQL, result tables and errors."""
    result: dict = {"answer": "", "sql": [], "tables": [], "errors": []}
    for message in messages:
        if "error" in message:
            result["errors"].append(message["error"])
        system = message.get("systemMessage", {})
        text = system.get("text", {})
        if text.get("textType") == "FINAL_RESPONSE":
            result["answer"] = "\n".join(text.get("parts", []))
        data = system.get("data", {})
        if "generatedSql" in data:
            result["sql"].append(data["generatedSql"])
        if "result" in data:
            result["tables"].append({
                "name": data["result"].get("name", ""),
                "rows": data["result"].get("data", []),
            })
    return result
