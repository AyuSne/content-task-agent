"""Comparison entry: bounded readback or an explicit legacy-code recovery pointer."""

import argparse
import json
from pathlib import Path

from cta.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="有限回读对照及历史实验恢复入口")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--data-dir")
    parser.add_argument("--method", choices=("oneshot", "fixed", "enhanced", "all", "readback"), required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order", type=int, default=1)
    args = parser.parse_args(argv)
    if args.method != "readback":
        print(json.dumps({
            "error": "legacy_experiment", "method": args.method,
            "message": "旧三方式实验依赖旧业务协议；请使用固定代码 836aa827d835e54a8e9385c18e7a3ff05ed3e600 和原采用资料复现。新旧对照使用本轮验收入口。",
        }, ensure_ascii=False, indent=2))
        return 4
    from cta.comparison_review import run_readback_comparison

    case = json.loads(args.case.read_text(encoding="utf-8"))
    config = load_config(args.mode, args.data_dir)
    result = run_readback_comparison(config, case, args.output)
    print(json.dumps(config.redact_data(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
