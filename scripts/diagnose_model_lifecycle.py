"""Record bounded TLS/connection-reuse comparisons against one existing task context."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai import OpenAI

from cta.config import load_config, load_env_file
from cta.model import DeepSeekClient
from cta.prompts import PROMPT_VERSION, TOOL_DEFINITIONS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--inspect", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wrapper-once", action="store_true", help="One uncapped production adapter call, no tool effects")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    load_env_file(args.env_file)
    config = load_config("real")
    inspected = json.loads(args.inspect.read_text(encoding="utf-8"))
    messages = []
    for entry in inspected["messages"]:
        message = {key: entry[key] for key in ("role", "content") if entry.get(key) is not None}
        for key in ("tool_calls", "tool_call_id", "name"):
            if entry.get(key):
                message[key] = entry[key]
        messages.append(message)
    rows = []
    shared_body = None
    manifest = {
        "source_task_id": inspected["task_id"], "source_inspect": str(args.inspect),
        "source_progress": inspected["progress"], "model_config": config.model_config_public(),
        "prompt_version": PROMPT_VERSION, "max_output_tokens": 128,
        "request_interval_seconds": 6, "sdk_retries": 0,
        "note": "External diagnostics only; no tool effects and no writes to the content-task ledger.",
        "attempts": rows,
    }

    def save():
        text = config.redact(json.dumps(manifest, ensure_ascii=False, indent=2))
        (args.output / "result.json").write_text(text + "\n", encoding="utf-8")

    def causes(exc):
        chain = []
        while exc is not None:
            chain.append(config.redact(f"{type(exc).__name__}: {exc}"))
            exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        return chain

    if args.wrapper_once:
        row = {"arm": "production_wrapper_uncapped", "started_at": datetime.now(UTC).isoformat(),
               "connection_trace": []}
        start = time.monotonic()

        def wrapper_trace(event, info):
            if event.startswith("connection.") or event.endswith(".failed"):
                entry = {"event": event, "offset_s": round(time.monotonic() - start, 4)}
                if "exception" in info:
                    entry["error_causes"] = causes(info["exception"])
                row["connection_trace"].append(entry)

        def wrapper_request(request):
            request.extensions["trace"] = wrapper_trace
            row["body_bytes"] = len(request.content)
            row["body_sha256"] = hashlib.sha256(request.content).hexdigest()
            (args.output / "request-body.json").write_bytes(request.content)

        def wrapper_response(response):
            row["http_version"] = response.http_version
            row["http_status"] = response.status_code

        client = DeepSeekClient(config)
        client.client._client.event_hooks["request"].append(wrapper_request)
        client.client._client.event_hooks["response"].append(wrapper_response)
        try:
            response = client.complete(messages, TOOL_DEFINITIONS, cursor=0)
            row.update(asdict(response))
        finally:
            client.client.close()
        row["finished_at"] = datetime.now(UTC).isoformat()
        row["wall_seconds"] = round(time.monotonic() - start, 3)
        manifest["max_output_tokens"] = "not_set: production wrapper default"
        manifest["adapter"] = "DeepSeekClient.complete; unchanged request construction"
        rows.append(row)
        save()
        print(json.dumps({"arm": row["arm"], "seconds": row["wall_seconds"], "error": row["error"],
                          "usage": row["usage"], "body_sha256": row["body_sha256"]}, ensure_ascii=False), flush=True)
        return 0

    for arm, expiry, http2 in (("default", 5, False), ("keepalive180", 180, False), ("http2", 5, True)):
        active = [None]

        def trace_event(event, info, state=active):
            if event.startswith("connection.") or event.endswith(".failed"):
                trace = {"event": event, "offset_s": round(time.monotonic() - state[0]["monotonic_start"], 4)}
                if "exception" in info:
                    trace["error_causes"] = causes(info["exception"])
                state[0]["connection_trace"].append(trace)

        def before_request(request, state=active, tracer=trace_event):
            nonlocal shared_body
            request.extensions["trace"] = tracer
            shared_body = request.content
            state[0]["body_bytes"] = len(request.content)
            state[0]["body_sha256"] = hashlib.sha256(request.content).hexdigest()

        def after_response(response, state=active):
            state[0]["http_version"] = response.http_version
            state[0]["http_status"] = response.status_code

        http_client = httpx.Client(
            trust_env=False, timeout=30, http2=http2, limits=httpx.Limits(keepalive_expiry=expiry),
            event_hooks={"request": [before_request], "response": [after_response]},
        )
        with OpenAI(api_key=config.api_key, base_url=config.base_url, http_client=http_client,
                    timeout=30, max_retries=0) as client:
            for index in range(1, 4):
                if index > 1:
                    time.sleep(6)
                row = {"arm": arm, "index": index, "keepalive_expiry": expiry, "http2_enabled": http2,
                       "started_at": datetime.now(UTC).isoformat(), "monotonic_start": time.monotonic(),
                       "connection_trace": [], "usage": None, "error_causes": []}
                active[0] = row
                try:
                    response = client.chat.completions.create(
                        model=config.model, messages=messages, tools=TOOL_DEFINITIONS,
                        tool_choice="auto", stream=False, max_tokens=128,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    row.update({"response_model": response.model, "response_id": response.id,
                                "usage": None if response.usage is None else response.usage.model_dump(),
                                "finish_reason": response.choices[0].finish_reason})
                except Exception as exc:  # noqa: BLE001 - diagnose and retain the actual external SDK attempt
                    row["error_causes"] = causes(exc)
                row["elapsed_s"] = round(time.monotonic() - row.pop("monotonic_start"), 3)
                row["finished_at"] = datetime.now(UTC).isoformat()
                row["new_tls_connections"] = sum(t["event"] == "connection.start_tls.started"
                                                 for t in row["connection_trace"])
                rows.append(row)
                save()
                print(json.dumps({"arm": arm, "index": index, "new_tls": row["new_tls_connections"],
                                  "protocol": row.get("http_version"), "error_causes": row["error_causes"],
                                  "seconds": row["elapsed_s"]}, ensure_ascii=False), flush=True)

    # Keep exactly the same serialized SDK body for the native TLS comparison.
    manifest["shared_body_sha256"] = hashlib.sha256(shared_body).hexdigest()
    (args.output / "request-body.json").write_bytes(shared_body)
    for index in range(1, 4):
        if index > 1:
            time.sleep(6)
        row = {"arm": "windows_curl_fresh", "index": index, "started_at": datetime.now(UTC).isoformat(),
               "body_bytes": len(shared_body), "body_sha256": hashlib.sha256(shared_body).hexdigest(),
               "usage": None, "error_causes": []}
        start = time.monotonic()
        quote = lambda value: json.dumps(value, ensure_ascii=False)  # curl config uses quoted C-style strings
        settings = "\n".join([
            "url = " + quote(config.base_url.rstrip("/") + "/chat/completions"),
            'request = "POST"', 'noproxy = "*"', 'connect-timeout = "10"', 'max-time = "30"',
            'silent', 'show-error',
            "header = " + quote("Authorization: Bearer " + config.api_key),
            'header = "Content-Type: application/json"',
            "data-binary = " + quote(shared_body.decode("utf-8")),
            'write-out = "\\nCTA_CURL_META:%{http_code}|%{http_version}|%{remote_ip}|%{time_total}"',
        ])
        completed = subprocess.run(["curl.exe", "--config", "-"], input=settings.encode("utf-8"),
                                   capture_output=True, check=False)
        body, _, metadata = completed.stdout.decode("utf-8").rpartition("\nCTA_CURL_META:")
        row.update({"elapsed_s": round(time.monotonic() - start, 3), "exit_code": completed.returncode,
                    "curl_metadata": metadata, "finished_at": datetime.now(UTC).isoformat()})
        if completed.stderr:
            row["error_causes"] = [config.redact(completed.stderr.decode("utf-8", errors="replace"))]
        if body:
            response = json.loads(body)
            row.update({"response_model": response.get("model"), "response_id": response.get("id"),
                        "usage": response.get("usage"), "error_response": response.get("error")})
        rows.append(row)
        save()
        print(json.dumps({"arm": row["arm"], "index": index, "curl_metadata": metadata,
                          "error_causes": row["error_causes"], "seconds": row["elapsed_s"]},
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
