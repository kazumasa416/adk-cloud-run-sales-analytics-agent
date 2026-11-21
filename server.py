"""ADK API server for the Cloud Run agent service.

One entry point, two apps. The app name picks the condition:

    sales_raw      no business definition
    sales_defined  business definition

Run locally:  GOOGLE_CLOUD_PROJECT=my-project uvicorn server:app --port 8081
(the server reads the Catalog and the Data Agent when it starts)
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from google.adk.cli.fast_api import get_fast_api_app

from sales.root import build_root_agent

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = str(ROOT / "agents")

# ADK loads an app on its first request. Build the "defined" agent here as well,
# so a Catalog definition that the Data Agent does not hold stops the server
# from starting. Cloud Run then keeps serving the previous revision.
build_root_agent("defined")

app: FastAPI = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=os.getenv("SESSION_SERVICE_URI") or None,
    use_local_storage=False,
    web=False,
    trace_to_cloud=os.getenv("TRACE_TO_CLOUD", "false").lower() == "true",
)


@app.get("/", include_in_schema=False)
def compare_page() -> FileResponse:
    """The comparison page: one question, two conditions, side by side."""
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-store"})
