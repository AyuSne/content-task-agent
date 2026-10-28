"""Extract the already-executed real ticket-03 trial into reviewable evidence."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIAL = Path(r"D:\AAA小影科技\.scratch\content-task-agent-build\run-state\real-trial-03")


def load(name: str) -> dict:
    return json.loads((TRIAL / name).read_text(encoding="utf-8"))


def write(name: str, body: dict) -> None:
    (HERE / name).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    for source, target in [
        ("01-create.json", "05-real-create.json"),
        ("02-brief-run.json", "06-real-brief-run.json"),
        ("03-draft-run.json", "07-real-draft-run.json"),
    ]:
        write(target, load(source))
    inspect = load("04-inspect.json")
    output = inspect["outputs"][0]
    summary = {
        "task_id": inspect["task_id"],
        "status": inspect["status"],
        "phase": inspect["progress"]["phase"],
        "used_rounds": inspect["progress"]["used_rounds"],
        "sim_elapsed_ms": inspect["progress"]["sim_elapsed_ms"],
        "sim_spent_cents": inspect["resources"]["sim_spent_cents"],
        "real_usage": inspect["resources"]["real_usage"],
        "output": {
            "version": output["version"],
            "status": output["status"],
            "strategy": output["content"]["strategy"],
            "activity": output["content"]["activity"],
            "script": output["content"]["script"],
            "materials": output["content"]["materials"],
            "production_steps": output["content"]["production_steps"],
            "evidence": output["content"]["evidence"],
            "open_questions": output["content"]["open_questions"],
            "content_md": output["content_md"],
        },
        "attempts": inspect["attempts"],
        "ledger": inspect["ledger"],
        "llm_requests": inspect["llm_requests"],
        "generator_messages_count": inspect["generator_messages_count"],
        "main_messages": [
            {
                "seq": message["seq"],
                "role": message["role"],
                "name": message["name"],
                "content": message["content"],
                "tool_calls": message["tool_calls"],
            }
            for message in inspect["messages"]
            if message["role"] in ("assistant", "tool")
        ],
    }
    write("08-real-trace.json", summary)
    print("real evidence extracted", inspect["task_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
