import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Isolated per-test data root; key is fake and must never be printed."""
    monkeypatch.setenv("CTA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CTA_SIM_START", "2026-10-29T00:00:00+08:00")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-super-secret-key")
    return tmp_path


@pytest.fixture
def model_script(tmp_path, monkeypatch):
    """Write a scripted model response list and point the CLI at it."""
    path = tmp_path / "model-script.json"

    def write(items):
        path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("CTA_MODEL_SCRIPT", str(path))
        return path

    return write


def run_cli(*args):
    from cta.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(args))
        except SystemExit as exc:  # argparse and explicit exits
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def cli_json(*args):
    code, out, err = run_cli(*args)
    assert code == 0, f"exit={code} stderr={err} stdout={out}"
    return json.loads(out)


def run_cli_process(*args, data_dir, extra_env=None):
    env = dict(os.environ)
    env["CTA_DATA_DIR"] = str(data_dir)
    env["CTA_SIM_START"] = "2026-10-29T00:00:00+08:00"
    env["DEEPSEEK_API_KEY"] = "sk-test-super-secret-key"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "cta", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


VALID = {
    "topic": "校园剪辑挑战",
    "budget": "10",
    "deadline": "2026-10-30T12:00:00+08:00",
}
