#!/usr/bin/env python3
"""Print the span tree of the most recent /run_sse requests from Cloud Trace.

    PROJECT_ID=my-project python scripts/trace.py [count]

Cloud Trace's list API does not always return these traces, so the trace IDs are
taken from the Cloud Run request log and fetched one by one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime


def gcloud(*args: str) -> str:
    command = ["gcloud", *args]
    if os.getenv("GCLOUD_ACCOUNT"):
        command.append("--account=" + os.environ["GCLOUD_ACCOUNT"])
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def seconds(span: dict) -> float:
    def parse(value: str) -> float:
        head, _, fraction = value.rstrip("Z").partition(".")
        return datetime.fromisoformat(head).timestamp() + float("0." + (fraction or "0"))
    return parse(span["endTime"]) - parse(span["startTime"])


def main() -> None:
    project = os.environ.get("PROJECT_ID") or sys.exit("Set PROJECT_ID.")
    service = os.getenv("SERVICE_NAME", "sales-analytics-agent")
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rows = gcloud(
        "logging", "read",
        f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" AND httpRequest.requestUrl:"run_sse"',
        "--project=" + project, "--freshness=2h", f"--limit={count}", "--format=value(timestamp,trace)",
    ).splitlines()
    token = gcloud("auth", "print-access-token")
    for row in reversed(rows):
        stamp, trace_ref = row.split("\t")
        trace_id = trace_ref.rsplit("/", 1)[-1]
        request = urllib.request.Request(
            f"https://cloudtrace.googleapis.com/v1/projects/{project}/traces/{trace_id}",
            headers={"Authorization": "Bearer " + token},
        )
        spans = json.load(urllib.request.urlopen(request)).get("spans", [])
        by_id = {span["spanId"]: span for span in spans}

        def depth(span: dict) -> int:
            level = 0
            while span.get("parentSpanId") in by_id:
                span = by_id[span["parentSpanId"]]
                level += 1
            return level

        print(f"--- {stamp[11:19]} trace {trace_id[:8]} ({len(spans)} spans)")
        for span in sorted(spans, key=lambda s: s["startTime"]):
            if span["name"].startswith("fastapi.") or " http " in span["name"]:
                continue
            labels = span.get("labels") or {}
            shown = {
                key.replace("gen_ai.usage.", "").replace("sales.", ""): str(value)[:40]
                for key, value in labels.items()
                if key.startswith("sales.") and key != "sales.question" or key in ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")
            }
            print("  " + "  " * depth(span) + f"{span['name'][:46]:46} {seconds(span):6.2f}s  " + (json.dumps(shown, ensure_ascii=False) if shown else ""))
        if os.getenv("SAVE_DIR"):
            with open(os.path.join(os.environ["SAVE_DIR"], f"trace-{trace_id[:8]}.json"), "w", encoding="utf-8") as fh:
                json.dump({"traceId": trace_id, "spans": spans}, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
