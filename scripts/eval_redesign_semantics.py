"""Prepare a controlled, derived scope pair for the public calibration CLI."""

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/calibration/2026-10-29.v2.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare"])
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    pair = copy.deepcopy(source["pairs"][0])
    pair["id"] = "redesign-scope-4.2"
    pair["issue"] = "创作证据范围超出 M-001 适用版本"
    pair["bad"] = copy.deepcopy(pair["good"])
    pair["bad"]["evidence"][0]["scope"] = "所有 iOS 和 Android 版本，包括 4.2 以下"
    pair["label_source"] = {
        "basis": "M-001 范围明确为 iOS/Android 4.2 及以上；4.2 以下不受支持。",
        "mutation_paths": ["evidence[0].scope"],
        "expected_issue_location": "方案 evidence E1 的 scope；不是第2段字幕本身",
        "provenance": "派生自 data/calibration/2026-10-29.v2.json 第一对的 good；不是独立样本。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"version": "redesign-semantics-scope-v1", "pairs": [pair]},
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
