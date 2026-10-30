"""Run fixed old and current clean checkouts against frozen shared scenarios."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

OLD_SHA = "836aa827d835e54a8e9385c18e7a3ff05ed3e600"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("old", "new", "cases", "materials", "env-file", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--resume-attempts", type=int, default=2)
    args = parser.parse_args()
    try:
        old_sha = git(args.old, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        parser.error("fixed old baseline must be a git repository")
    if old_sha != OLD_SHA:
        parser.error("fixed old baseline must be 836aa827d835e54a8e9385c18e7a3ff05ed3e600")
    roots = {"old": args.old.resolve(), "new": args.new.resolve()}
    sources = {}
    for side, root in roots.items():
        status = git(root, "status", "--porcelain")
        if status:
            parser.error(f"{side} source has uncommitted changes; commit them before measurement")
        lock = root / "uv.lock"
        sources[side] = {"commit": git(root, "rev-parse", "HEAD"), "status": status,
                         "lock_sha256": sha(lock) if lock.is_file() else None,
                         "runner_sha256": sha(root / "scripts/run_eval_suite.py")}
    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    ids = [case["id"] for case in suite["cases"]]
    if args.case and set(args.case) - set(ids):
        parser.error("unknown case ID")
    sys.path.insert(0, str(roots["new"] / "src"))
    from cta.config import load_env_file

    load_env_file(args.env_file)
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") != "https://api.deepseek.com":
        parser.error("DeepSeek key and official HTTPS base URL are required")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    for name in list(env):
        if name.startswith("CTA_") or name.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(name)
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    shutil.copyfile(args.cases, output / "cases.json")
    shutil.copyfile(args.materials, output / "materials.json")
    env["CTA_MATERIALS_PATH"] = str(output / "materials.json")
    versions = {}
    for package in ("openai", "httpx"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    manifest = {"sources": sources, "cases_sha256": sha(output / "cases.json"),
                "materials_sha256": sha(output / "materials.json"), "python": sys.version,
                "packages": versions, "model": env.get("DEEPSEEK_MODEL", "deepseek-flash"),
                "base_url": "https://api.deepseek.com", "route": "direct; TLS verification enabled",
                "started_unix": time.time(), "runs": []}
    write(output / "manifest.json", manifest)
    for case in ids:
        if args.case and case not in args.case:
            continue
        for side, root in roots.items():
            side_dir = output / side
            side_dir.mkdir(exist_ok=True)
            child_env = dict(env, PYTHONPATH=str(root / "src"))
            command = [sys.executable, str(root / "scripts/run_eval_suite.py"), "--cases", str(output / "cases.json"),
                       "--case", case, "--output", str(side_dir), "--resume-attempts", str(args.resume_attempts)]
            started = time.monotonic()
            try:
                result = subprocess.run(command, cwd=root, env=child_env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", check=False)
                stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
            except OSError as exc:
                stdout, stderr, exit_code = "", str(exc), None
            (side_dir / f"{case}.stdout.txt").write_text(stdout.replace(key, "[REDACTED]"), encoding="utf-8")
            (side_dir / f"{case}.stderr.txt").write_text(stderr.replace(key, "[REDACTED]"), encoding="utf-8")
            summary = side_dir / "summary.json"
            if summary.exists():
                summary.rename(side_dir / f"{case}.summary.json")
            manifest["runs"].append({"side": side, "case": case, "exit_code": exit_code,
                                     "wall_seconds": round(time.monotonic() - started, 3)})
            write(output / "manifest.json", manifest)
            print(f"{side}/{case}: exit={exit_code}", flush=True)
    manifest["finished_unix"] = time.time()
    write(output / "manifest.json", manifest)
    return int(any(item["exit_code"] != 0 for item in manifest["runs"]))


if __name__ == "__main__":
    raise SystemExit(main())
