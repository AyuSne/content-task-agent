"""A restarted process keeps the execution limit chosen for this task."""

import json

from conftest import run_cli_process


def test_new_process_does_not_replace_task_round_limit(cli_env):
    data = cli_env / "limit-data"
    created = run_cli_process(
        "create", "--topic", "轮数持久化", "--budget", "10", "--deadline", "2026-10-30",
        data_dir=data, extra_env={"CTA_MAX_ROUNDS": "12"},
    )
    assert created.returncode == 0, created.stderr
    task = json.loads(created.stdout)
    assert task["progress"]["max_rounds"] == 12
    restarted = run_cli_process(
        "status", task["task_id"], data_dir=data, extra_env={"CTA_MAX_ROUNDS": "24"},
    )
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["progress"]["max_rounds"] == 12
