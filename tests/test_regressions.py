"""Regression coverage for review findings and evidence boundaries."""

import json
import sqlite3

from conftest import run_cli
from test_brief_loop import BRIEF, _brief_tool, _create, _tool


def test_cjk_partial_terms_find_brand_and_music_materials(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "品牌规则 禁语 音乐授权", "mode": "extended"}),
            _tool("stop_incomplete", {"reason": "仅核对检索，不提交简报。"}),
        ]
    )
    run_cli("run", task["task_id"])
    inspected = run_cli("inspect", task["task_id"])[1]
    document = json.loads(inspected)
    search = json.loads(
        next(message for message in document["messages"] if message["role"] == "tool")["content"]
    )
    ids = [item["material_id"] for item in search["results"]]
    assert "M-004" in ids
    assert "M-006" in ids


def test_zero_result_search_is_reported_as_a_gap(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("search_materials", {"query": "完全不存在的主题"}),
            _tool("stop_incomplete", {"reason": "检索无结果，按缺口停止。"}),
        ]
    )
    run_cli("run", task["task_id"])
    document = json.loads(run_cli("inspect", task["task_id"])[1])
    search = json.loads(
        next(message for message in document["messages"] if message["role"] == "tool")["content"]
    )
    assert search["ok"] is False
    assert "gap" in search


def test_malformed_scope_type_is_rejected_without_crashing(cli_env, model_script):
    task = _create()
    brief = json.loads(json.dumps(BRIEF))
    brief["product_facts"][0]["scope"] = 123
    model_script([_tool("propose_brief", brief), _brief_tool()])
    code, out, _err = run_cli("run", task["task_id"], "--max-rounds", "2")
    assert code == 0
    document = json.loads(out)
    assert document["progress"]["phase"] == "brief_ready"
    inspected = json.loads(run_cli("inspect", task["task_id"])[1])
    rejection = next(
        message for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "propose_brief"
    )
    assert "invalid_brief" in rejection["content"]
    assert "scope 不能为空" in rejection["content"]


def test_estimate_required_arguments_and_projected_completion(cli_env, model_script):
    task = _create()
    model_script(
        [
            _tool("estimate_cost", {}),
            _tool("estimate_cost", {"actions": ["search_basic", "generate_direct", "review", "save"]}),
            _brief_tool(),
        ]
    )
    run_cli("run", task["task_id"])
    inspected = json.loads(run_cli("inspect", task["task_id"])[1])
    results = [
        json.loads(message["content"])
        for message in inspected["messages"]
        if message["role"] == "tool" and message["name"] == "estimate_cost"
    ]
    assert results[0]["ok"] is False
    assert "actions" in results[0]["message"]
    estimate = results[1]["estimate"]
    assert estimate["projected_sim_complete"]
    assert estimate["budget_gap_cents"] == 1000 - estimate["total_cost_cents"]
    assert estimate["time_gap_ms"] is not None
    assert estimate["within_deadline"] is True


def test_v1_database_is_migrated_and_queryable(cli_env):
    data_dir = cli_env / "data" / "mock"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "tasks.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, mode TEXT NOT NULL, fingerprint TEXT NOT NULL,
            dedupe_key TEXT UNIQUE, status TEXT NOT NULL, reason TEXT NOT NULL,
            input_version INTEGER NOT NULL, current_output_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            sim_elapsed_ms INTEGER NOT NULL DEFAULT 0, sim_start TEXT NOT NULL,
            used_rounds INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE task_inputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, version INTEGER NOT NULL,
            topic TEXT, budget_cents INTEGER, deadline TEXT, audience TEXT,
            requirements_json TEXT NOT NULL, raw_json TEXT NOT NULL,
            validation_json TEXT NOT NULL, interpretations_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO tasks VALUES ('t_old', 'mock', 'fp', NULL, 'in_progress', '旧任务',
            1, NULL, '2026-10-28T00:00:00+00:00', '2026-10-28T00:00:00+00:00', 0,
            '2026-10-29T00:00:00+08:00', 0, 0);
        INSERT INTO task_inputs (task_id, version, topic, budget_cents, deadline, audience,
            requirements_json, raw_json, validation_json, interpretations_json, created_at)
        VALUES ('t_old', 1, '旧主题', 1000, '2026-10-30T04:00:00+00:00', NULL, '[]',
            '{"topic":"旧主题","budget":"10","deadline":"2026-10-30T12:00:00+08:00","audience":null,"requirements":[]}',
            '[]', '[]', '2026-10-28T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    code, out, err = run_cli("status", "t_old")
    assert code == 0, err
    document = json.loads(out)
    assert document["status"] == "in_progress"
    assert document["progress"]["phase"] == "created"
    assert document["progress"]["brief_version"] == 0
