#!/usr/bin/env python3
"""Send one question to a deployed (or local) agent service and print what came back.

    python scripts/ask.py sales_defined "質問"            # SERVICE_URL must be set
    python scripts/ask.py sales_raw sales_defined sales_reuse    # default question, several conditions

Uses `gcloud auth print-identity-token` for a private Cloud Run service
(set GCLOUD_ACCOUNT to pick an account).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

QUESTION = "10月の売上は、9月と比べてどうでしたか？"


def identity_token() -> str:
    command = ["gcloud", "auth", "print-identity-token"]
    if os.getenv("GCLOUD_ACCOUNT"):
        command.append("--account=" + os.environ["GCLOUD_ACCOUNT"])
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def ask(base: str, app: str, question: str, headers: dict) -> dict:
    def post(path: str, body: dict):
        request = urllib.request.Request(base + path, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
        return urllib.request.urlopen(request, timeout=600)

    session = f"s{int(time.time() * 1000)}"
    post(f"/apps/{app}/users/demo/sessions/{session}", {}).read()
    started = time.monotonic()
    result: dict = {"app": app, "answer": "", "tool": None, "usage": [], "errors": []}
    body = {"appName": app, "userId": "demo", "sessionId": session,
            "newMessage": {"role": "user", "parts": [{"text": question}]}}
    with post("/run_sse", body) as stream:
        for raw in stream:
            line = raw.decode("utf-8")
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event.get("error") or event.get("errorMessage"):
                result["errors"].append(event.get("error") or event.get("errorMessage"))
            if event.get("usageMetadata"):
                result["usage"].append(event["usageMetadata"])
            for part in (event.get("content") or {}).get("parts") or []:
                response = part.get("functionResponse")
                if response:
                    result["tool"] = response.get("response")
                elif part.get("text") and not part.get("thought"):
                    result["answer"] = part["text"]
    result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return result


def main() -> None:
    args = sys.argv[1:]
    apps = [a for a in args if a.startswith("sales_")] or ["sales_defined"]
    rest = [a for a in args if not a.startswith("sales_")]
    question = rest[0] if rest else QUESTION
    base = os.environ.get("SERVICE_URL", "http://localhost:8081").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if base.startswith("https://"):
        headers["Authorization"] = "Bearer " + identity_token()
    for app in apps:
        result = ask(base, app, question, headers)
        tool = result["tool"] or {}
        print(f"===== {app}  {result['elapsed_seconds']}s")
        if result["errors"]:
            print("ERRORS:", json.dumps(result["errors"], ensure_ascii=False)[:1200])
        print("Data Agent calls:", tool.get("data_agent_calls"), "| seconds:", tool.get("data_agent_seconds"),
              "| review:", (tool.get("review") or {}).get("passed"), "| same evidence:", tool.get("reviewed_same_evidence"))
        for table in tool.get("tables") or []:
            print("ROWS:", json.dumps(table.get("rows"), ensure_ascii=False)[:400])
        tokens = [(u.get("promptTokenCount"), u.get("candidatesTokenCount"), u.get("thoughtsTokenCount")) for u in result["usage"]]
        print("Root model tokens (in, out, thinking):", tokens)
        try:
            answer = json.loads(result["answer"])
            before, after = answer["previous_month_jpy"], answer["current_month_jpy"]
            print(f"COUNTED: {answer['counted_orders']}")
            print(f"SALES: {before:,} -> {after:,}  ({after - before:+,} JPY, {(after - before) / before * 100:+.1f}%)")
            print("SUMMARY:", answer["summary_ja"].replace("\n", " ")[:400])
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            print("ANSWER:", result["answer"].replace("\n", " ")[:500])
        if os.getenv("SAVE_DIR"):
            with open(os.path.join(os.environ["SAVE_DIR"], f"{app}-{int(time.time())}.json"), "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
