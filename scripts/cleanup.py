#!/usr/bin/env python3
"""Delete what scripts/provision.py created. Lists first; deletes only with --yes.

    PROJECT_ID=my-project python scripts/cleanup.py         # show what would be deleted
    PROJECT_ID=my-project python scripts/cleanup.py --yes   # delete

Removes the demo agents, its glossary term and link, the labelled order
tables, and legacy Catalog resources created by earlier sample versions.
The BigQuery dataset and the Cloud Run service are left for you (see README).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from provision import bq, gcloud, token  # noqa: E402
from sales import data  # noqa: E402
from sales.catalog import GLOSSARY_DESCRIPTION, GROUP_ID, TYPE_ID, CatalogClient, catalog_location  # noqa: E402
from sales.data_agent import MARK, DataAgentClient  # noqa: E402


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    dataset = os.getenv("BQ_DATASET", "sales_demo")
    delete = "--yes" in sys.argv[1:]
    found = []

    agents = DataAgentClient(project, os.getenv("DATA_AGENT_LOCATION", "us"), token)
    for name in agents.created_by_this_demo():
        found.append("Data Agent  " + name)
        if delete:
            agents.delete(name)

    catalog = CatalogClient(project, catalog_location(), token)
    number = gcloud("projects", "describe", project, "--format=value(projectNumber)")
    link_name, expected = catalog.table_link(number, dataset, data.GLOSSARY_TABLE)
    link = catalog._request("GET", link_name, missing_ok=True)
    if link and catalog.matches_link(link, expected):
        found.append("Catalog link  " + link_name)
        if delete:
            catalog._wait(catalog._request("DELETE", link_name))
    glossary = catalog._request("GET", catalog.glossary, missing_ok=True)
    if glossary and glossary.get("description") == GLOSSARY_DESCRIPTION:
        if catalog._request("GET", catalog.term, missing_ok=True):
            found.append("Catalog term  " + catalog.term)
            if delete:
                catalog._wait(catalog._request("DELETE", catalog.term))
        found.append("Catalog glossary  " + catalog.glossary)
        if delete:
            catalog._wait(catalog._request("DELETE", catalog.glossary))

    # Resources created by releases before the business glossary migration.
    legacy_entry = f"{catalog.parent}/entryGroups/{GROUP_ID}/entries/confirmed-sales"
    group = catalog._request("GET", f"{catalog.parent}/entryGroups/{GROUP_ID}", missing_ok=True)
    if group is not None and group.get("description") == MARK:
        if catalog._request("GET", legacy_entry, missing_ok=True) is not None:
            found.append("Catalog entry  " + legacy_entry)
            if delete:
                catalog._request("DELETE", legacy_entry)
        found.append("Catalog entry group  " + group["name"])
        if delete:
            catalog._wait(catalog._request("DELETE", f"{catalog.parent}/entryGroups/{GROUP_ID}"))
    kind = catalog._request("GET", f"{catalog.parent}/entryTypes/{TYPE_ID}", missing_ok=True)
    if kind is not None and kind.get("description") == MARK:
        found.append("Catalog entry type  " + kind["name"])
        if delete:
            catalog._wait(catalog._request("DELETE", f"{catalog.parent}/entryTypes/{TYPE_ID}"))

    for table_id in (data.GLOSSARY_TABLE, data.TABLE):
        table_path = f"/projects/{project}/datasets/{dataset}/tables/{table_id}"
        table = bq("GET", table_path, None, project)
        if table is not None and (table.get("labels") or {}).get(data.LABEL[0]) == data.LABEL[1]:
            found.append(f"BigQuery table  {project}.{dataset}.{table_id}")
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
