"""Command line interface: create, query and list content tasks.

The CLI is the public seam used by acceptance tests. Every command returns a
JSON document (default) or a short text view, and persists through SQLite.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from cta.config import MODES, load_config
from cta.identity import RawInput
from cta.service import TaskNotFound, TaskService


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mode", choices=MODES, default=argparse.SUPPRESS)
    common.add_argument("--data-dir", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="cta", description="内容任务规划 Agent")
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", parents=[common], help="创建或找回内容任务")
    create.add_argument("--topic")
    create.add_argument("--budget")
    create.add_argument("--deadline")
    create.add_argument("--audience")
    create.add_argument("--requirement", action="append", default=[])
    create.add_argument("--new", action="store_true", help="同一内容也显式新建独立任务")

    status = sub.add_parser("status", parents=[common], help="查询任务状态")
    status.add_argument("task_id")

    run = sub.add_parser("run", parents=[common], help="执行或显式续跑主循环")
    run.add_argument("task_id")
    run.add_argument("--max-rounds", type=int, default=None, help="仅用于测试注入的更小轮数上限")

    inspect = sub.add_parser("inspect", parents=[common], help="查看消息、知识、工具结果与消耗")
    inspect.add_argument("task_id")

    review = sub.add_parser("review", parents=[common], help="在独立上下文中核验当前完整方案")
    review.add_argument("task_id")

    save = sub.add_parser("save", parents=[common], help="核对核验与资源后核实保存")
    save.add_argument("task_id")

    export = sub.add_parser("export", parents=[common], help="从权威状态导出方案、报告与 Trace")
    export.add_argument("task_id")

    calibrate = sub.add_parser("calibrate", parents=[common], help="运行六对正反样例的评估器校准")
    calibrate.add_argument("--pairs", default=None, help="校准样例 JSON 路径")
    calibrate.add_argument("--output", default=None, help="结果 JSON 输出路径")

    sub.add_parser("list", parents=[common], help="列出当前模式下的任务")
    return parser


def _emit(document: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return
    print(_render_text(document))


def _render_text(document: dict) -> str:
    if "error" in document:
        return f"错误：{document.get('message', document['error'])}"
    if "tasks" in document:
        lines = [f"模式：{document['mode']}，共 {document['count']} 个任务"]
        for task in document["tasks"]:
            lines.append(f"- {task['task_id']}  {task['status']}  {task['reason']}")
        return "\n".join(lines)
    if "calibration_version" in document:
        metrics = document["metrics"]
        return "\n".join(
            [
                f"校准 {document['calibration_version']}（{document['mode']}）",
                (
                    f"问题稿命中 {metrics['bad_detected']}/{metrics['problem_samples']}；漏检 {len(metrics['missed'])}；"
                    f"合格稿误报 {len(metrics['false_positives'])}；未完成 {metrics['incomplete_reviews']}"
                ),
                (
                    f"请求 {document['usage']['requests']} 次；未知用量 {document['usage']['unknown_usage_requests']}；"
                    f"结果 {document.get('output_path', '未写入文件')}"
                ),
            ]
        )
    input_view = document.get("input", {})
    progress = document.get("progress", {})
    resources = document.get("resources", {})
    lines = [
        f"任务：{document.get('task_id')}  模式：{document.get('mode')}  状态：{document.get('status')}",
        f"原因：{document.get('reason')}",
        "输入：主题={topic}；预算={budget} 元；截止={deadline}".format(
            topic=input_view.get("topic"),
            budget=input_view.get("budget"),
            deadline=input_view.get("deadline"),
        ),
        f"进度：轮数 {progress.get('used_rounds')}/{progress.get('max_rounds')}；模拟已用 {progress.get('sim_elapsed_ms')} ms",
        f"模拟费用：已发生 {resources.get('sim_spent_cents')} 分；剩余 {resources.get('remaining_cents')} 分",
    ]
    errors = document.get("validation", {}).get("errors", [])
    for error in errors:
        lines.append(f"待补充[{error['field']}]：{error['message']}")
    for note in document.get("validation", {}).get("interpretations", []):
        lines.append(f"解释：{note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(mode=args.mode, data_dir=args.data_dir)
    except ValueError as exc:
        _emit({"error": "bad_config", "message": str(exc)}, args.format)
        return 2

    service = TaskService(config)
    try:
        if args.command == "create":
            raw = RawInput.from_args(
                topic=args.topic,
                budget=args.budget,
                deadline=args.deadline,
                audience=args.audience,
                requirements=args.requirement,
            )
            document = service.create(raw, explicit_new=args.new)
        elif args.command == "status":
            document = service.status(args.task_id)
        elif args.command == "run":
            from cta.loop import run_task

            document = run_task(config, args.task_id, max_rounds=args.max_rounds)
        elif args.command == "inspect":
            document = service.inspect(args.task_id)
        elif args.command == "review":
            from cta.review import ReviewError, review_task

            try:
                document = review_task(config, args.task_id)
            except ReviewError as exc:
                document = {"error": "review_blocked", "task_id": args.task_id, "message": str(exc)}
                _emit(document, args.format)
                return 4
        elif args.command == "save":
            from cta.save import save_task

            document = save_task(config, args.task_id)
            if "save_error" in document:
                _emit(document, args.format)
                return 4
        elif args.command == "export":
            from cta.export import export_task

            document = export_task(config, args.task_id)
        elif args.command == "calibrate":
            from cta.calibration import run_calibration

            output = args.output
            if output is None:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                output = config.exports_dir / "calibration" / f"calibration-{stamp}.json"
            document = run_calibration(config, pairs_path=args.pairs, output_path=output)
        elif args.command == "list":
            document = service.list_tasks()
        else:  # pragma: no cover - argparse guards this
            raise SystemExit(2)
    except TaskNotFound as exc:
        document = {"error": "task_not_found", "task_id": exc.task_id, "message": str(exc)}
        _emit(document, args.format)
        return 3
    except ValueError as exc:
        document = {"error": "operation_blocked", "task_id": getattr(args, "task_id", None), "message": str(exc)}
        _emit(document, args.format)
        return 4

    _emit(document, args.format)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
