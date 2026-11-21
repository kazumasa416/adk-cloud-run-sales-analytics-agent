#!/usr/bin/env python3
"""Roll out the definition that is in the Catalog now.

Edit the entry in the Catalog, then run:

    PROJECT_ID=my-project python scripts/sync_definition.py

1. Reads the definition from the Catalog.
2. Creates a Data Agent for this version of the text (sales-defined-<version>),
   unless it exists. The agents of earlier versions are left as they are.
3. Starts a new Cloud Run revision. The Root Agent reads the Catalog when it
   starts and asks the Data Agent of the same version.

A Root Agent that is already running keeps its own version and that version's
Data Agent until it is replaced. So if step 3 fails, answers and the version
recorded for them still match: they are the old ones.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales.catalog import CatalogClient, catalog_location  # noqa: E402
from sales.data_agent import DataAgentClient  # noqa: E402


def gcloud(*args: str) -> str:
    command = ["gcloud", *args]
    if os.getenv("GCLOUD_ACCOUNT"):
        command.append("--account=" + os.environ["GCLOUD_ACCOUNT"])
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    region = os.getenv("REGION", "asia-northeast1")
    service = os.getenv("SERVICE_NAME", "sales-analytics-agent")
    token = lambda: gcloud("auth", "print-access-token")  # noqa: E731

    definition = CatalogClient(project, catalog_location(), token).definition()
    version = definition["version"]
    print(f"Catalog: {definition['entry']}")
    print(f"         updated {definition['update_time']} sha256 {version}")

    agents = DataAgentClient(project, os.getenv("DATA_AGENT_LOCATION", "us"), token)
    name, created = agents.ensure("defined", os.getenv("BQ_DATASET", "sales_demo"), definition["body"], version)
    print("Data Agent: " + ("created " if created else "already exists ") + name)

    if "--no-restart" in sys.argv[1:]:
        print("Root Agent: not restarted (--no-restart). A running one keeps the version it started with.")
        return
    print("Root Agent: starting a new revision...")
    gcloud("run", "services", "update", service, "--project=" + project, "--region=" + region,
           "--update-env-vars=DEFINITION_VERSION=" + version)
    serving = gcloud("run", "services", "describe", service, "--project=" + project, "--region=" + region,
                     "--format=value(status.latestReadyRevisionName)")
    print(f"Root Agent: {serving} is serving with definition {version}")


if __name__ == "__main__":
    main()
