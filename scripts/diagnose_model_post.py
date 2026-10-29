"""Bounded real GET/POST diagnosis. Credentials never enter the evidence file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cta.config import load_config, load_env_file
from cta.model import DeepSeekClient
from cta.prompts import MAIN_SYSTEM_PROMPT, PROMPT_VERSION, TOOL_DEFINITIONS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    load_env_file(args.env_file)
    config = load_config("real")
    rows = []
    for repetition in range(1, 4):
        for kind in ("get_models", "post_minimal", "post_main_tools"):
            row = {"kind": kind, "repetition": repetition, "started_at": datetime.now(UTC).isoformat()}
            start = time.monotonic()
            if kind == "get_models":
                try:
                    with httpx.Client(trust_env=False, timeout=30) as client:
                        response = client.get(config.base_url.rstrip("/") + "/models")
                    row["http_status"] = response.status_code
                except httpx.HTTPError as exc:
                    causes = []
                    while exc is not None:
                        causes.append(config.redact(f"{type(exc).__name__}: {exc}"))
                        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
                    row["error_causes"] = causes
                row["usage"] = "not_applicable: unauthenticated GET"
            else:
                messages = [{"role": "user", "content": "请回复ok。"}]
                tools = []
                if kind == "post_main_tools":
                    messages = [{"role": "system", "content": MAIN_SYSTEM_PROMPT}, {
                        "role": "user", "content": "主题：帧剪校园社团招新推广；受众：18-24岁大学生。"
                        "任务模拟工具预算2000分，方案截止2026-10-30T12:00:00+08:00。先决定一个合法动作。",
                    }]
                    tools = TOOL_DEFINITIONS
                client = DeepSeekClient(config)
                sent = []
                def record_request(request, destination=sent):
                    destination.append({"method": request.method, "url": str(request.url),
                                 "body_bytes": len(request.content),
                                 "body_sha256": hashlib.sha256(request.content).hexdigest()})
                client.client._client.event_hooks["request"].append(record_request)
                try:
                    response = client.complete(messages, tools, cursor=0)
                    row.update(asdict(response))
                finally:
                    client.client.close()
                row["http_requests"] = sent
            row["finished_at"] = datetime.now(UTC).isoformat()
            row["wall_seconds"] = round(time.monotonic() - start, 3)
            rows.append(row)
            result = {"model_config": config.model_config_public(), "prompt_version": PROMPT_VERSION,
                      "sequence": "three repetitions of GET -> minimal POST -> main-tools POST; fresh client per request",
                      "attempts": rows}
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"kind": kind, "repetition": repetition, "seconds": row["wall_seconds"],
                              "status": row.get("http_status"), "error": row.get("error"),
                              "error_causes": row.get("error_causes"), "usage": row.get("usage")},
                             ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
