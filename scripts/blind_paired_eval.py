"""Prepare anonymous campaign comparisons and reveal mapping after recorded review."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(args) -> None:
    suite = read(args.cases)
    args.output.mkdir(parents=True, exist_ok=False)
    blind = args.output / "blind"
    blind.mkdir()
    if args.materials:
        shutil.copyfile(args.materials, blind / "materials.json")
    mapping = []
    for case in suite["cases"]:
        candidates = []
        for side, roots in (("old", args.old), ("new", args.new)):
            candidate = secrets.token_hex(8)
            paths = [root / case["id"] / "05-inspect.json" for root in roots]
            path = next((item for item in paths if item.is_file()), paths[0])
            entry = {"candidate": candidate, "available": False, "content": None, "task_status": "unknown",
                     "source_error": "missing_inspect"}
            if path.is_file():
                try:
                    facts = read(path)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    facts = None
                if (not isinstance(facts, dict) or not isinstance(facts.get("status"), str)
                        or not isinstance(facts.get("outputs"), list)):
                    entry["source_error"] = "invalid_inspect"
                    candidates.append(entry)
                    mapping.append({"candidate": candidate, "case": case["id"], "side": side,
                                    "source": str(path), "available": False})
                    continue
                entry["source_error"] = None
                entry["task_status"] = facts["status"]
                campaigns = [item for item in facts["outputs"] if item["kind"] == "campaign"]
                save = facts.get("save") or {}
                selected = next((item for item in campaigns if item["version"] == save.get("output_version")), None)
                if selected is None and campaigns:
                    selected = campaigns[-1]
                if selected:
                    entry["available"] = True
                    entry["content"] = selected["content"]
            candidates.append(entry)
            mapping.append({"candidate": candidate, "case": case["id"], "side": side,
                            "source": str(path), "available": entry["available"]})
        secrets.SystemRandom().shuffle(candidates)
        case_input = {key: value for key, value in {**suite["defaults"], **case}.items()
                      if key not in ("expected_status", "fault", "no_model_requests")}
        case_input["requirements"] = suite["defaults"].get("requirements", []) + case.get("requirements", [])
        write(blind / f"{case['id']}.json", {"input": case_input, "candidates": candidates})
    write(blind / "reviews.json", [])
    write(args.output / "mapping.json", mapping)


def reveal(args) -> None:
    mapping = read(args.package / "mapping.json")
    reviews = read(args.package / "blind" / "reviews.json")
    required = {item["candidate"] for item in mapping if item["available"]}
    by_id = {item["candidate"]: item for item in reviews}
    if len(by_id) != len(reviews) or set(by_id) != required:
        raise ValueError("Review every available candidate once before revealing the mapping")
    fields = ("facts", "requirements", "production", "uncertainty", "remarks")
    if any(not isinstance(by_id[candidate].get(field), str) or not by_id[candidate][field].strip()
           for candidate in required for field in fields):
        raise ValueError("Each review must record facts, requirements, production, uncertainty and remarks")
    destination = args.package / "revealed.json"
    if destination.exists():
        raise ValueError("Reveal already exists; preserve the first review and reveal")
    write(destination, [{**item, "review": by_id.get(item["candidate"])} for item in mapping])


def correct(args) -> None:
    if not (args.package / "revealed.json").is_file():
        raise ValueError("Reveal the original mapping before recording corrections")
    mapping = read(args.package / "mapping.json")
    if args.candidate not in {item["candidate"] for item in mapping} or not args.reason.strip():
        raise ValueError("A known candidate and an explanation are required")
    event = {"candidate": args.candidate, "side": args.side, "reason": args.reason,
             "recorded_at": datetime.now(UTC).isoformat()}
    with (args.package / "mapping-corrections.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("prepare")
    create.add_argument("--cases", type=Path, required=True)
    create.add_argument("--materials", type=Path)
    create.add_argument("--old", type=Path, action="append", required=True)
    create.add_argument("--new", type=Path, action="append", required=True)
    create.add_argument("--output", type=Path, required=True)
    show = sub.add_parser("reveal")
    show.add_argument("--package", type=Path, required=True)
    fix = sub.add_parser("correct")
    fix.add_argument("--package", type=Path, required=True)
    fix.add_argument("--candidate", required=True)
    fix.add_argument("--side", choices=("old", "new"), required=True)
    fix.add_argument("--reason", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "reveal": reveal, "correct": correct}[args.command](args)


if __name__ == "__main__":
    main()
