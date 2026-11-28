#!/usr/bin/env python3
"""Refresh the Root Agent after editing the linked glossary term."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sales.catalog import CatalogClient, catalog_location  # noqa: E402


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
