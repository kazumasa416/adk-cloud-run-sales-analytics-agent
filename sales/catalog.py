"""Knowledge Catalog business glossary and its BigQuery table link."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

API = "https://dataplex.googleapis.com/v1/"
GROUP_ID = "sales-demo"
TYPE_ID = "sales-definition"
ENTRY_ID = "confirmed-sales"
TITLE = "確定売上"
GLOSSARY_ID = "sales-demo-glossary"
GLOSSARY_DESCRIPTION = "adk-cloud-run-sales-demo"


class CatalogError(RuntimeError):
    pass


def catalog_location() -> str:
    return os.getenv("CATALOG_LOCATION", "us")


class CatalogClient:
    """Thin REST client. `token` returns a fresh OAuth access token."""

    def __init__(self, project: str, location: str, token: Callable[[], str]):
        self.project = project
        self.parent = f"projects/{project}/locations/{location}"
        self.location = location
        self.glossary = f"{self.parent}/glossaries/{GLOSSARY_ID}"
        self.term = f"{self.glossary}/terms/{ENTRY_ID}"
        self._token = token

    def _request(self, method: str, path: str, payload: dict | None = None, missing_ok: bool = False):
        request = urllib.request.Request(
            API + path,
            method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + self._token(),
                "Content-Type": "application/json",
                "x-goog-user-project": self.project,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            body = exc.read().decode("utf-8", "replace")[:1000]
            raise CatalogError(f"HTTP {exc.code} from {method} {path.split('?')[0]}: {body}") from exc

    def _wait(self, response: dict) -> dict:
        """Entry groups and entry types are created by long-running operations."""
        for _ in range(60):
            if "/operations/" not in response.get("name", "") or response.get("done"):
                break
            time.sleep(2)
            response = self._request("GET", response["name"])
        if response.get("error"):
            raise CatalogError("Catalog operation failed: " + json.dumps(response["error"])[:600])
        if "/operations/" in response.get("name", "") and not response.get("done"):
            raise CatalogError(f"Catalog operation timed out: {response['name']}")
        return response

    def _ensure(self, collection: str, ident: str, id_key: str, payload: dict) -> dict:
        name = f"{collection}/{ident}"
        existing = self._request("GET", name, missing_ok=True)
        if existing is not None:
            return existing
        self._wait(self._request("POST", collection + "?" + urllib.parse.urlencode({id_key: ident}), payload))
        return self._request("GET", name)

    def ensure_term(self, starter: str) -> None:
        glossary = self._ensure(self.parent + "/glossaries", GLOSSARY_ID, "glossaryId", {
            "displayName": "Sales demo", "description": GLOSSARY_DESCRIPTION,
        })
        if glossary.get("description") != GLOSSARY_DESCRIPTION:
            raise CatalogError(f"Glossary already exists outside this demo: {self.glossary}")
        self._ensure(self.glossary + "/terms", ENTRY_ID, "termId", {
            "displayName": TITLE, "description": starter, "parent": self.glossary,
        })

    def table_link(self, number: str, dataset: str, table: str) -> tuple[str, dict]:
        parent = f"projects/{number}/locations/{self.location}"
        groups = parent + "/entryGroups"
        source = (f"{groups}/@bigquery/entries/bigquery.googleapis.com/projects/{self.project}"
                  f"/datasets/{dataset}/tables/{table}")
        target = f"{groups}/@dataplex/entries/{parent}/glossaries/{GLOSSARY_ID}/terms/{ENTRY_ID}"
        key = hashlib.sha256(source.encode()).hexdigest()[:12]
        return f"{groups}/@bigquery/entryLinks/sales-glossary-{key}", {
            "entryLinkType": "projects/dataplex-types/locations/global/entryLinkTypes/definition",
            "entryReferences": [{"name": source, "type": "SOURCE"}, {"name": target, "type": "TARGET"}],
        }

    def ensure_link(self, number: str, dataset: str, table: str) -> None:
        name, payload = self.table_link(number, dataset, table)
        collection, ident = name.rsplit("/", 1)
        link = self._ensure(collection, ident, "entryLinkId", payload)
        if not self.matches_link(link, payload):
            raise CatalogError(f"Existing entry link points to different resources: {name}")

    @staticmethod
    def matches_link(link: dict, payload: dict) -> bool:
        def refs(value):
            return {(r["type"], r["name"], r.get("path", "")) for r in value["entryReferences"]}
        return link.get("entryLinkType", "").endswith("/entryLinkTypes/definition") and refs(link) == refs(payload)

    def definition(self) -> dict:
        term = self._request("GET", self.term)
        body = term.get("description", "").strip()
        if not body:
            raise CatalogError(f"{self.term} has no definition.")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return {"entry": term["name"], "title": term["displayName"], "update_time": term.get("updateTime", ""),
                "sha256": digest, "version": digest[:12], "body": body}
