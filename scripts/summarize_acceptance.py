"""Build an evidence index without averaging incomplete delivery into success."""

import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scenario(name):
    folder = Path("examples/repair-live") / name
    data = read(folder / "05-inspect.json")
    usage = data["resources"]["real_model_usage"]
    return {
        "case": name, "task_id": data["task_id"], "status": data["status"],
        "output_version": data["artifacts"]["campaign_versions"],
        "rounds": data["progress"]["used_rounds"], "limit": data["progress"]["max_rounds"],
        "simulated_cost_cny_cent": data["resources"]["sim_spent_cents"],
        "simulated_elapsed_ms": data["progress"]["sim_elapsed_ms"],
        "requests": usage["requests"], "unknown_usage_requests": usage["unknown_usage_requests"],
        "known_prompt_tokens": usage["prompt_tokens"], "known_completion_tokens": usage["completion_tokens"],
        "cache_hit_tokens": usage["cache_hit_tokens"], "cache_miss_tokens": usage["cache_miss_tokens"],
        "api_elapsed_ms": usage["elapsed_ms"], "reason": data["reason"],
        "source": str(folder / "05-inspect.json"),
    }


def main():
    groups = {
        "normal": [scenario(f"normal-delivery-0{i}") for i in (1, 2, 3)],
        "budget": [scenario(f"budget-delivery-0{i}") for i in (1, 2, 3, 4)],
        "recovery": [scenario(f"recovery-delivery-0{i}") for i in (1, 2, 3)],
        "insufficient": [scenario("insufficient-knowledge2-01")],
        "post_refactor_smoke": [scenario("post-refactor-smoke")],
    }
    audit = read("examples/repair-live/audit-summary.json")
    keys = ("requests", "unknown_usage_requests", "prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens")
    totals = {key: sum(item["resources"]["real_model_usage"][key] for item in audit) for key in keys}
    calibration = read("examples/repair-calibration/knowledge2/result.json")
    human = read("examples/repair-human/import-01/04-inspect.json")
    result = {
        "groups": groups, "unique_recorded_content_tasks": len(audit),
        "task_usage_including_historical_failures_and_human_edit": totals,
        "evidence_invariants_hold": all(item["invariants_hold"] for item in audit),
        "calibration": {key: calibration[key] for key in ("calibration_version", "knowledge_version", "metrics", "usage", "wall_seconds")},
        "human_import": {"task_id": human["task_id"], "status": human["status"], "input_version": human["input"]["version"],
                         "output_version": human["progress"]["current_output"]["version"], "source": "examples/repair-human/import-01"},
        "comparison": {"source": "examples/repair-comparison/normal-01/suite.json",
                       "readback_source": "examples/repair-comparison/readback-01/result.json"},
        "cost_units": {"simulation": "CNY_cent", "usage": "request/token", "actual_money": "unknown"},
        "notes": ["不将正确停止混入合格交付，不删除早期失败。", "任务用量汇总不包括单独校准、外部评价与网络诊断。",
                  "子Agent开发Token用量不可得；真实API金额没有可靠来源，保留未知。",
                  "budget-delivery-03原模型停止说明误称美分，权威账本单位为CNY_cent（人民币分）。"],
    }
    Path("examples/acceptance-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(audit), "usage": totals, "calibration": calibration["metrics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
