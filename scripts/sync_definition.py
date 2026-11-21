#!/usr/bin/env python3
"""Apply the Catalog definition to both agents and check that they match.

Edit the entry in the Catalog, then run:

    PROJECT_ID=my-project python scripts/sync_definition.py

1. Reads the definition from the Catalog.
2. Gives it to the "defined" Data Agent.
3. Starts a new Cloud Run revision, so the Root Agent reads it too. The Root
   Agent refuses to start if the Data Agent holds a different definition, and
   Cloud Run then keeps serving the previous revision.

Until step 3 finishes, the running Root Agent still uses the old definition.
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
    version = definition["sha256"][:12]
    print(f"Catalog: {definition['entry']}")
    print(f"         updated {definition['update_time']} sha256 {version}")

    agents = DataAgentClient(project, os.getenv("DATA_AGENT_LOCATION", "us"), token)
    changed = agents.set_definition(os.getenv("BQ_DATASET", "sales_demo"), definition["body"])
    print("Data Agent: " + ("updated" if changed else "already had this definition"))

    if "--no-restart" in sys.argv[1:]:
        print("Root Agent: not restarted (--no-restart). It keeps the definition it started with.")
        return
    print("Root Agent: starting a new revision...")
    gcloud("run", "services", "update", service, "--project=" + project, "--region=" + region,
           "--update-env-vars=DEFINITION_VERSION=" + version)
    serving = gcloud("run", "services", "describe", service, "--project=" + project, "--region=" + region,
                     "--format=value(status.latestReadyRevisionName)")
    print(f"Root Agent: {serving} is serving with definition {version}")


if __name__ == "__main__":
    main()
