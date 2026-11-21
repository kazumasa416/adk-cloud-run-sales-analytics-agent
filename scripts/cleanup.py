#!/usr/bin/env python3
"""Delete what scripts/provision.py created. Lists first; deletes only with --yes.

    PROJECT_ID=my-project python scripts/cleanup.py         # show what would be deleted
    PROJECT_ID=my-project python scripts/cleanup.py --yes   # delete

Only resources that carry this demo's mark are deleted: the `orders` table with
its label, the two Data Agents and the Catalog entry with their description.
The BigQuery dataset and the Cloud Run service are left for you (see README).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from provision import bq, token  # noqa: E402
from sales import data  # noqa: E402
from sales.catalog import GROUP_ID, TYPE_ID, CatalogClient, catalog_location  # noqa: E402
from sales.data_agent import VARIANTS, DataAgentClient  # noqa: E402

MARK = "adk-cloud-run-sales-demo"


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    dataset = os.getenv("BQ_DATASET", "sales_demo")
    delete = "--yes" in sys.argv[1:]
    found = []

    agents = DataAgentClient(project, os.getenv("DATA_AGENT_LOCATION", "us"), token)
    for variant in VARIANTS:
        agent = agents.get(variant)
        if agent is not None and agent.get("description", "").startswith(MARK):
            found.append("Data Agent  " + agents.name(variant))
            if delete:
                agents._request("DELETE", agents.base + agents.name(variant) + ":deleteSync")

    catalog = CatalogClient(project, catalog_location(), token)
    group = catalog._request("GET", f"{catalog.parent}/entryGroups/{GROUP_ID}", missing_ok=True)
    if group is not None and group.get("description") == MARK:
        if catalog._request("GET", catalog.entry_name, missing_ok=True) is not None:
            found.append("Catalog entry  " + catalog.entry_name)
            if delete:
                catalog._request("DELETE", catalog.entry_name)
        found.append("Catalog entry group  " + group["name"])
        if delete:
            catalog._wait(catalog._request("DELETE", f"{catalog.parent}/entryGroups/{GROUP_ID}"))
    kind = catalog._request("GET", f"{catalog.parent}/entryTypes/{TYPE_ID}", missing_ok=True)
    if kind is not None and kind.get("description") == MARK:
        found.append("Catalog entry type  " + kind["name"])
        if delete:
            catalog._wait(catalog._request("DELETE", f"{catalog.parent}/entryTypes/{TYPE_ID}"))

    table_path = f"/projects/{project}/datasets/{dataset}/tables/{data.TABLE}"
    table = bq("GET", table_path, None, project)
    if table is not None and (table.get("labels") or {}).get(data.LABEL[0]) == data.LABEL[1]:
        found.append(f"BigQuery table  {project}.{dataset}.{data.TABLE}")
        if delete:
            bq("DELETE", table_path, None, project)

    for line in found:
        print(("Deleted  " if delete else "Would delete  ") + line)
    if not found:
        print("Nothing to delete.")
    elif not delete:
        print("Run again with --yes to delete.")


if __name__ == "__main__":
    main()
