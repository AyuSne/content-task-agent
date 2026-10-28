"""Ticket 01: task intake, identity, duplicate handling and mode isolation.

Seam: the public CLI. Assertions use only the JSON documents the CLI returns and
observable persistence across a real separate process.
"""

import json

from conftest import VALID, cli_json, run_cli, run_cli_process


def _create(*extra, **overrides):
    values = {**VALID, **overrides}
    args = ["create", "--topic", values["topic"], "--budget", values["budget"], "--deadline", values["deadline"]]
    if values.get("audience"):
        args += ["--audience", values["audience"]]
    for req in values.get("requirements", []):
        args += ["--requirement", req]
    return cli_json(*args, *extra)


def test_missing_required_inputs_create_needs_input_task_with_reasons(cli_env):
    doc = run_cli("create")
    code, out, err = doc
    assert code == 0, err
    body = json.loads(out)

    assert body["status"] == "needs_input"
    assert body["task_id"].startswith("t_")
    errors = {e["field"]: e for e in body["validation"]["errors"]}
    assert set(errors) == {"topic", "budget", "deadline"}
    assert all(e["code"] == "required" and e["message"] for e in errors.values())

    queried = cli_json("status", body["task_id"])
    assert queried["status"] == "needs_input"
    assert queried["reason"] == body["reason"]
    assert queried["task_id"] == body["task_id"]


def test_invalid_values_are_explained_and_raw_input_preserved(cli_env):
    body = cli_json(
        "create",
        "--topic", "   ",
        "--budget", "0",
        "--deadline", "2026-10-28T00:00:00+08:00",
    )
    assert body["status"] == "needs_input"
    errors = {e["field"]: e["code"] for e in body["validation"]["errors"]}
    assert errors["topic"] == "required"
    assert errors["budget"] == "not_positive"
    assert errors["deadline"] == "not_after_start"

    raw = body["input"]["raw"]
    assert raw["budget"] == "0"
    assert raw["deadline"] == "2026-10-28T00:00:00+08:00"
    assert body["input"]["budget_cents"] is None
    assert body["input"]["deadline"] is None


def test_complete_input_creates_queryable_task_without_work_or_spend(cli_env):
    body = _create()
    assert body["status"] == "in_progress"
    assert body["task_id"].startswith("t_")
    assert body["progress"]["used_rounds"] == 0
    assert body["resources"]["sim_spent_cents"] == 0
    assert body["resources"]["budget_cents"] == 1000
    assert body["progress"]["current_output"] is None
    assert body["input"]["budget"] == "10.00"
    assert body["input"]["deadline"] == "2026-10-30T04:00:00+00:00"

    queried = cli_json("status", body["task_id"])
    assert queried["task_id"] == body["task_id"]
    assert queried["status"] == "in_progress"
    assert queried["input"]["topic"] == "校园剪辑挑战"
    assert queried["resources"]["sim_spent_cents"] == 0
    assert queried["progress"]["used_rounds"] == 0


def test_duplicate_request_returns_same_task_and_does_not_execute(cli_env):
    first = _create()
    second = _create()

    assert second["task_id"] == first["task_id"]
    assert second["duplicate"] is True
    assert first["duplicate"] is False
    assert second["status"] == "in_progress"
    assert second["progress"]["used_rounds"] == 0
    assert second["resources"]["sim_spent_cents"] == 0


def test_equivalent_normalized_input_is_treated_as_duplicate(cli_env):
    first = cli_json(
        "create",
        "--topic", "  校园   剪辑挑战 ",
        "--budget", "10.0",
        "--deadline", "2026-10-30T12:00:00+08:00",
    )
    second = cli_json(
        "create",
        "--topic", "校园 剪辑挑战",
        "--budget", "10.00",
        "--deadline", "2026-10-30T04:00:00Z",
    )
    assert first["task_id"] == second["task_id"]
    assert second["duplicate"] is True


def test_explicit_new_request_gets_independent_identity(cli_env):
    first = _create()
    second = _create("--new")
    assert second["task_id"] != first["task_id"]
    assert second["duplicate"] is False
    assert cli_json("status", first["task_id"])["task_id"] == first["task_id"]
    assert cli_json("status", second["task_id"])["task_id"] == second["task_id"]


def test_persistence_is_visible_to_a_real_separate_process(cli_env):
    created = _create()
    data_dir = cli_env / "data"

    queried = run_cli_process("status", created["task_id"], data_dir=data_dir)
    assert queried.returncode == 0, queried.stderr
    body = json.loads(queried.stdout)
    assert body["task_id"] == created["task_id"]
    assert body["status"] == "in_progress"
    assert body["input"]["topic"] == "校园剪辑挑战"
    assert body["resources"]["sim_spent_cents"] == 0

    again = run_cli_process(
        "create", "--topic", VALID["topic"], "--budget", VALID["budget"], "--deadline", VALID["deadline"],
        data_dir=data_dir,
    )
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["task_id"] == created["task_id"]


def test_modes_have_separate_stores_and_identities(cli_env):
    mock_task = _create()
    real_task = _create("--mode", "real")

    assert mock_task["task_id"] != real_task["task_id"]
    assert mock_task["mode"] == "mock"
    assert real_task["mode"] == "real"
    data_dir = cli_env / "data"
    assert (data_dir / "mock" / "tasks.db").exists()
    assert (data_dir / "real" / "tasks.db").exists()

    # The same id is not visible in the other mode's store.
    not_found = run_cli("--mode", "real", "status", mock_task["task_id"])
    assert not_found[0] != 0


def test_status_never_echoes_the_api_key(cli_env):
    body = _create()
    output = json.dumps(cli_json("status", body["task_id"]))
    assert "sk-test-super-secret-key" not in output
    assert body["model_config"]["api_key"] == "set"
    assert body["model_config"]["base_url"] == "https://api.deepseek.com"


def test_unparseable_deadline_is_explained(cli_env):
    body = cli_json("create", "--topic", "主题", "--budget", "10", "--deadline", "tomorrow")
    assert body["status"] == "needs_input"
    errors = {e["field"]: e["code"] for e in body["validation"]["errors"]}
    assert errors == {"deadline": "invalid_format"}
    assert body["input"]["raw"]["deadline"] == "tomorrow"


def test_list_is_scoped_to_mode(cli_env):
    mock_task = _create()
    real_task = _create("--mode", "real")
    listing = cli_json("list")
    assert listing["mode"] == "mock"
    assert listing["count"] == 1
    assert listing["tasks"][0]["task_id"] == mock_task["task_id"]
    assert listing["tasks"][0]["task_id"] != real_task["task_id"]

