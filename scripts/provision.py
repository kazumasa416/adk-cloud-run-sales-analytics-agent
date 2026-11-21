#!/usr/bin/env python3
"""Create the BigQuery dataset, the orders table, the Catalog entry and the two Data Agents.

Run once per project, as a user who owns the project:

    PROJECT_ID=my-project python scripts/provision.py

Uses the gcloud CLI for access tokens (set GCLOUD_ACCOUNT to pick an account).
Safe to rerun: an existing table, Catalog entry and Data Agents are kept. A
table named `orders` that this script did not create is never touched. To
load the rows again, pass --recreate-table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales import data  # noqa: E402
from sales.catalog import CatalogClient, catalog_location  # noqa: E402
from sales.data_agent import VARIANTS, DataAgentClient, starter_rules  # noqa: E402

APIS = [
    "bigquery.googleapis.com",
    "geminidataanalytics.googleapis.com",
    "cloudaicompanion.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudtrace.googleapis.com",
    "dataplex.googleapis.com",
]
BQ = "https://bigquery.googleapis.com/bigquery/v2"


def gcloud(*args: str) -> str:
    command = ["gcloud", *args]
    if os.getenv("GCLOUD_ACCOUNT"):
        command.append("--account=" + os.environ["GCLOUD_ACCOUNT"])
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def token() -> str:
    return gcloud("auth", "print-access-token")


def bq(method: str, path: str, payload: dict | None, project: str) -> dict | None:
    request = urllib.request.Request(
        BQ + path,
        method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json", "x-goog-user-project": project},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 409 or (exc.code == 404 and method == "GET"):
            return None
        raise SystemExit(f"BigQuery {method} {path} failed: HTTP {exc.code} {exc.read().decode()[:800]}")


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    dataset = os.getenv("BQ_DATASET", "sales_demo")
    bq_location = os.getenv("BQ_LOCATION", "US")
    agent_location = os.getenv("DATA_AGENT_LOCATION", "us")

    print(f"Project: {project} / dataset: {dataset} ({bq_location}) / Data Agent location: {agent_location}")
    print("Enabling APIs...")
    gcloud("services", "enable", *APIS, "--project=" + project)

    created = bq("POST", f"/projects/{project}/datasets", {
        "datasetReference": {"projectId": project, "datasetId": dataset},
        "location": bq_location,
        "description": "Synthetic sales data for the ADK on Cloud Run demo",
    }, project)
    print("Dataset created." if created else "Dataset already exists.")

    table_path = f"/projects/{project}/datasets/{dataset}/tables/{data.TABLE}"
    table = bq("GET", table_path, None, project)
    ours = table is not None and (table.get("labels") or {}).get(data.LABEL[0]) == data.LABEL[1]
    if table is not None and not ours:
        raise SystemExit(
            f"{project}.{dataset}.{data.TABLE} already exists and was not created by this script "
            f"(no {data.LABEL[0]}={data.LABEL[1]} label). Nothing was changed. Set BQ_DATASET to another dataset."
        )
    if ours and "--recreate-table" not in sys.argv[1:]:
        print("Kept the orders table.")
    else:
        if ours:
            print("Dropping the orders table (--recreate-table)...")
            bq("DELETE", table_path, None, project)
        print("Loading the orders table...")
        result = bq("POST", f"/projects/{project}/queries", {
            "query": data.seed_sql(project, dataset),
            "useLegacySql": False,
            "location": bq_location,
            "timeoutMs": 120000,
        }, project)
        if not result or not result.get("jobComplete"):
            raise SystemExit("The seed script did not finish: " + json.dumps(result)[:600])
    check = bq("POST", f"/projects/{project}/queries", {
        "query": f"SELECT status, COUNT(*) AS n FROM `{project}.{dataset}.{data.TABLE}` GROUP BY status ORDER BY status",
        "useLegacySql": False,
        "location": bq_location,
    }, project)
    counts = {row["f"][0]["v"]: int(row["f"][1]["v"]) for row in check.get("rows", [])}
    print("Rows by status:", counts, "total", sum(counts.values()))
    # The table and its rows are created by two statements. If the second one
    # failed, the labelled table is there but the rows are not.
    expected = dict(Counter(row[2] for row in data.fixture()))
    if counts != expected:
        raise SystemExit(
            f"The orders table does not hold the {sum(expected.values())} demo rows (expected {expected}). "
            "Run again with --recreate-table to load them."
        )

    catalog = CatalogClient(project, catalog_location(), token)
    print(("Created " if catalog.ensure_entry(starter_rules()) else "Kept ") + catalog.entry_name)
    definition = catalog.definition()
    print(f"Definition: updated {definition['update_time']} sha256 {definition['version']}")

    client = DataAgentClient(project, agent_location, token)
    for variant in VARIANTS:
        # The "defined" agent is named after the version of the definition it holds.
        version = definition["version"] if variant == "defined" else ""
        name, was_created = client.ensure(variant, dataset, definition["body"], version)
        print(("Created " if was_created else "Kept ") + name)


if __name__ == "__main__":
    main()
