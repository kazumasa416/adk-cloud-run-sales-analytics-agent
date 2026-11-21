"""Conversational Analytics (Data Agent) client: published context, create, chat.

Two agents look at the same `orders` table. The only difference is whether the
company's business definition is in the system instruction. The definition
itself comes from the Catalog (see sales/catalog.py):

    raw      orders table, no business definition
    defined  orders table, business definition

Keeping the data source identical is what makes the comparison fair.
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


def holds_definition(agent: dict | None, rules: str) -> bool:
    """True when the "defined" Data Agent was given exactly this definition."""
    published = (agent or {}).get("dataAnalyticsAgent", {}).get("publishedContext", {})
    return published.get("systemInstruction") == baseline_instruction() + "\n\n" + rules.strip()


def agent_id(variant: str) -> str:
    return os.getenv(f"DATA_AGENT_ID_{variant.upper()}", f"sales-{variant}")


class DataAgentClient:
    """Thin REST client. `token` returns a fresh OAuth access token."""

    def __init__(self, project: str, location: str, token: Callable[[], str]):
        if location not in HOSTS:
            raise DataAgentError(f"Unsupported Data Agent location: {location}")
        self.project = project
        self.parent = f"projects/{project}/locations/{location}"
        self.base = f"https://{HOSTS[location]}/v1/"
        self._token = token

    def name(self, variant: str) -> str:
        return f"{self.parent}/dataAgents/{agent_id(variant)}"

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

    def get(self, variant: str) -> dict | None:
        try:
            return self._request("GET", self.base + self.name(variant))
        except DataAgentError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def ensure(self, variant: str, dataset: str, rules: str = "") -> tuple[str, bool]:
        """Create the agent when missing. Returns (resource name, created)."""
        context = published_context(self.project, dataset, variant, rules)
        existing = self.get(variant)
        if existing is not None:
            published = existing.get("dataAnalyticsAgent", {}).get("publishedContext", {})
            if published.get("systemInstruction") != context["systemInstruction"]:
                raise DataAgentError(
                    f"{self.name(variant)} exists with a different instruction. "
                    "Delete it or set DATA_AGENT_ID_" + variant.upper() + " to a new id."
                )
            return self.name(variant), False
        payload = {
            "name": self.name(variant),
            "displayName": DISPLAY_NAMES[variant],
            "description": "adk-cloud-run-sales-demo:" + variant,
            "dataAnalyticsAgent": {"publishedContext": context},
        }
        url = f"{self.base}{self.parent}/dataAgents:createSync?dataAgentId={agent_id(variant)}"
        self._request("POST", url, payload)
        return self.name(variant), True

    def set_definition(self, dataset: str, rules: str) -> bool:
        """Give the "defined" agent this definition. Returns False when it already had it."""
        existing = self.get("defined")
        if existing is None:
            raise DataAgentError(self.name("defined") + " does not exist. Run scripts/provision.py first.")
        if holds_definition(existing, rules):
            return False
        context = published_context(self.project, dataset, "defined", rules)
        mask = "data_analytics_agent.published_context"
        url = f"{self.base}{self.name('defined')}:updateSync?updateMask={mask}"
        self._request("PATCH", url, {"name": self.name("defined"), "dataAnalyticsAgent": {"publishedContext": context}})
        if not holds_definition(self.get("defined"), rules):
            raise DataAgentError("The Data Agent did not take the new definition.")
        return True

    def chat(self, variant: str, question: str) -> list[dict]:
        """Ask one question. The streaming response is one JSON array at EOF."""
        payload = {
            "parent": self.parent,
            "messages": [{"userMessage": {"text": question}}],
            "dataAgentContext": {"dataAgent": self.name(variant)},
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
