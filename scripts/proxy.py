#!/usr/bin/env python3
"""Open the private Cloud Run service from this machine.

Forwards http://localhost:PORT to the service and adds your identity token
(from the gcloud CLI) to every request, so the comparison page works in a
browser:

    SERVICE_URL=https://... python scripts/proxy.py
    open http://localhost:8790/

Set GCLOUD_ACCOUNT to pick an account, PORT to change the port.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

TARGET = (os.environ.get("SERVICE_URL") or sys.exit("Set SERVICE_URL.")).rstrip("/")
_token = {"value": "", "at": 0.0}
client = httpx.AsyncClient(timeout=600)


def token() -> str:
    # Identity tokens last an hour. Ask for a new one every 30 minutes.
    if time.time() - _token["at"] > 1800:
        command = ["gcloud", "auth", "print-identity-token"]
        if os.getenv("GCLOUD_ACCOUNT"):
            command.append("--account=" + os.environ["GCLOUD_ACCOUNT"])
        _token["value"] = subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()
        _token["at"] = time.time()
    return _token["value"]


async def forward(request):
    headers = {"Authorization": "Bearer " + token()}
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers["content-type"]
    upstream = client.build_request(
        request.method, TARGET + request.url.path, content=await request.body(), headers=headers
    )
    response = await client.send(upstream, stream=True)

    async def body():
        async for chunk in response.aiter_raw():
            yield chunk
        await response.aclose()

    keep = {k: v for k, v in response.headers.items() if k.lower() in ("content-type", "cache-control")}
    return StreamingResponse(body(), status_code=response.status_code, headers=keep)


app = Starlette(routes=[Route("/{path:path}", forward, methods=["GET", "POST"])])

if __name__ == "__main__":
    # Local only: the forwarded requests carry your identity.
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8790")), log_level="warning")
