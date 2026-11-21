"""ADK API server for the Cloud Run agent service.

One entry point, three apps. The app name picks the condition:

    sales_raw      no business definition
    sales_defined  business definition
    sales_reuse    business definition + analysis shared within the request

Run locally:  uvicorn server:app --port 8081
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from google.adk.cli.fast_api import get_fast_api_app

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = str(ROOT / "agents")

app: FastAPI = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=os.getenv("SESSION_SERVICE_URI") or None,
    use_local_storage=False,
    web=False,
    trace_to_cloud=os.getenv("TRACE_TO_CLOUD", "false").lower() == "true",
)


@app.get("/", include_in_schema=False)
def compare_page() -> FileResponse:
    """The comparison page: one question, three conditions, side by side."""
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-store"})
