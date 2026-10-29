"""Finite comparison of the exact SDK wrapper, without executing any returned tool."""

import argparse
import functools
import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from cta.config import load_config
from cta.model import DeepSeekClient
from cta.prompts import TOOL_DEFINITIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", nargs="+", default=["wrapper128", "wrapper_default", "raw_sdk_default", "wrapper_timeout30"],
                        choices=("wrapper128", "wrapper8192", "wrapper16384", "wrapper_default", "raw_sdk_default", "wrapper_timeout30"))
    args = parser.parse_args()
    config = load_config("real")
    inspected = json.loads(args.inspect.read_text(encoding="utf-8"))
    messages = []
    for entry in inspected["messages"]:
        item = {key: entry[key] for key in ("role", "content") if entry.get(key) is not None}
        item.update({key: entry[key] for key in ("tool_calls", "tool_call_id", "name") if entry.get(key)})
        messages.append(item)
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for arm in args.arms:
        os.environ["CTA_LLM_TIMEOUT"] = "30" if arm == "wrapper_timeout30" else "180"
        client = DeepSeekClient(config)
        row = {"arm": arm, "tls_events": [], "usage": None, "error": None}

        def trace(event, info):
            if event.startswith("connection."):
                row["tls_events"].append({"event": event, "error": config.redact(str(info.get("exception", "")))})

        def record(request):
            request.extensions["trace"] = trace
            row["body_sha256"] = hashlib.sha256(request.content).hexdigest()
            row["body_bytes"] = len(request.content)

        client.client._client.event_hooks["request"].append(record)
        if arm in ("wrapper128", "wrapper8192", "wrapper16384"):
            client.client.chat.completions.create = functools.partial(client.client.chat.completions.create, max_tokens=int(arm.removeprefix("wrapper")))
        started = time.monotonic()
        try:
            if arm == "raw_sdk_default":
                raw = client.client.chat.completions.create(
                    model=config.model, messages=messages, tools=TOOL_DEFINITIONS, tool_choice="auto", stream=False,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                row.update({"response_model": raw.model, "usage": raw.usage.model_dump(),
                            "finish_reason": raw.choices[0].finish_reason,
                            "tools": [call.function.name for call in raw.choices[0].message.tool_calls or []]})
            else:
                row.update(asdict(client.complete(messages, TOOL_DEFINITIONS, cursor=0)))
        except Exception as exc:  # external diagnostic boundary; records every actual attempt
            chain = []
            while exc is not None:
                chain.append(config.redact(f"{type(exc).__name__}: {exc}"))
                exc = exc.__cause__
            row["error"] = " <- ".join(chain)
        finally:
            client.client.close()
        row["wall_seconds"] = round(time.monotonic() - started, 3)
        rows.append(row)
        (args.output / "result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: row.get(k) for k in ("arm", "error", "usage", "wall_seconds")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
