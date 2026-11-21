"""The company's business definition, kept as one entry in the Catalog.

The definition is not a file that ships with the agent. It is an entry that
the business can own and edit, and the agent reads it from there. What comes
back is a receipt: which entry, when it was last updated, and a hash of the
text. The receipt goes into the trace, so a request can be traced back to the
exact definition it used.

The entry is not linked to the `orders` table. A term linked to the table
could reach the "raw" condition too, and the comparison would stop being fair.
"""

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
DESCRIPTION = "営業会議でいう「売上」の定義"
OVERVIEW_KEY = "dataplex-types.global.overview"


class CatalogError(RuntimeError):
    pass


def catalog_location() -> str:
    return os.getenv("CATALOG_LOCATION", "us")


class CatalogClient:
    """Thin REST client. `token` returns a fresh OAuth access token."""

    def __init__(self, project: str, location: str, token: Callable[[], str]):
        self.project = project
        self.parent = f"projects/{project}/locations/{location}"
        self.entry_name = f"{self.parent}/entryGroups/{GROUP_ID}/entries/{ENTRY_ID}"
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
        return response

    def _ensure(self, collection: str, ident: str, id_key: str, payload: dict) -> dict:
        name = f"{collection}/{ident}"
        existing = self._request("GET", name, missing_ok=True)
        if existing is not None:
            return existing
        self._wait(self._request("POST", collection + "?" + urllib.parse.urlencode({id_key: ident}), payload))
        return self._request("GET", name)

    def ensure_entry(self, starter: str) -> bool:
        """Create the entry from `starter` when missing. An existing entry is never overwritten."""
        if self._request("GET", self.entry_name + "?view=ALL", missing_ok=True) is not None:
            return False
        meta = {"displayName": "Sales demo definitions", "description": "adk-cloud-run-sales-demo"}
        self._ensure(self.parent + "/entryGroups", GROUP_ID, "entryGroupId", meta)
        kind = self._ensure(self.parent + "/entryTypes", TYPE_ID, "entryTypeId", meta)
        self._request(
            "POST",
            f"{self.parent}/entryGroups/{GROUP_ID}/entries?" + urllib.parse.urlencode({"entryId": ENTRY_ID}),
            {
                "entryType": kind["name"],
                "entrySource": {"displayName": TITLE, "description": DESCRIPTION},
                "aspects": {OVERVIEW_KEY: {"data": {"content": starter, "contentType": "MARKDOWN"}}},
            },
        )
        return True

    def definition(self) -> dict:
        """Read the entry. Returns the text and a receipt of what was read."""
        entry = self._request("GET", self.entry_name + "?view=ALL", missing_ok=True)
        if entry is None:
            raise CatalogError(f"{self.entry_name} does not exist. Run scripts/provision.py first.")
        overviews = [
            aspect for aspect in entry.get("aspects", {}).values()
            if aspect.get("aspectType", "").endswith("/aspectTypes/overview")
        ]
        body = (overviews[0].get("data", {}).get("content", "") if len(overviews) == 1 else "").strip()
        if not body:
            raise CatalogError(f"{self.entry_name} has no overview text. A local file is not used instead.")
        return {
            "entry": entry["name"],
            "title": entry.get("entrySource", {}).get("displayName", ""),
            "update_time": entry.get("updateTime", ""),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body": body,
        }
