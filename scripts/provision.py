#!/usr/bin/env python3
"""Create the BigQuery dataset, the five tables and the two Data Agents.

Run once per project, as a user who owns the project:

    PROJECT_ID=my-project python scripts/provision.py

Uses the gcloud CLI for access tokens (set GCLOUD_ACCOUNT to pick an account).
Safe to rerun: tables are recreated, existing Data Agents are kept.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ec import data  # noqa: E402
from ec.data_agent import VARIANTS, DataAgentClient  # noqa: E402

APIS = [
    "bigquery.googleapis.com",
    "geminidataanalytics.googleapis.com",
    "cloudaicompanion.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudtrace.googleapis.com",
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
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return None
        raise SystemExit(f"BigQuery {method} {path} failed: HTTP {exc.code} {exc.read().decode()[:800]}")


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    dataset = os.getenv("BQ_DATASET", "ec_demo")
    bq_location = os.getenv("BQ_LOCATION", "US")
    agent_location = os.getenv("DATA_AGENT_LOCATION", "us")

    print(f"Project: {project} / dataset: {dataset} ({bq_location}) / Data Agent location: {agent_location}")
    print("Enabling APIs...")
    gcloud("services", "enable", *APIS, "--project=" + project)

    created = bq("POST", f"/projects/{project}/datasets", {
        "datasetReference": {"projectId": project, "datasetId": dataset},
        "location": bq_location,
        "description": "Synthetic e-commerce data for adk-cloud-run-ec-analytics-agent",
    }, project)
    print("Dataset created." if created else "Dataset already exists.")

    print("Loading the five tables and the view...")
    result = bq("POST", f"/projects/{project}/queries", {
        "query": data.seed_sql(project, dataset),
        "useLegacySql": False,
        "location": bq_location,
        "timeoutMs": 120000,
    }, project)
    if not result or not result.get("jobComplete"):
        raise SystemExit("The seed script did not finish: " + json.dumps(result)[:600])
    check = bq("POST", f"/projects/{project}/queries", {
        "query": " UNION ALL ".join(
            f"SELECT '{t}' AS t, COUNT(*) AS n FROM `{project}.{dataset}.{t}`" for t in data.FIELDS
        ) + " ORDER BY t",
        "useLegacySql": False,
        "location": bq_location,
    }, project)
    counts = {row["f"][0]["v"]: int(row["f"][1]["v"]) for row in check.get("rows", [])}
    print("Row counts:", counts, "total", sum(counts.values()))

    client = DataAgentClient(project, agent_location, token)
    for variant in VARIANTS:
        name, was_created = client.ensure(variant, dataset)
        print(("Created " if was_created else "Kept ") + name)


if __name__ == "__main__":
    main()
