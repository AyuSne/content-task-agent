"""New protocol local actions recover their own exact receipts without replay."""
import json
import os
import subprocess
import sys
from datetime import datetime

import pytest
from conftest import cli_json, run_cli_process
from test_brief_loop import BRIEF
from test_campaign_draft import _tool
from test_process_resume import _pending_call, _tool_result

FIXED_UTC = "2026-10-29T01:02:03+00:00"


def _fixed_clock(monkeypatch):
    from cta import config
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(FIXED_UTC).astimezone(tz)
    monkeypatch.setattr(config, "datetime", Clock)


def _frozen_cli_process(*args, crash=None):
    script = f"""
from datetime import datetime
import cta.config
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat({FIXED_UTC!r}).astimezone(tz)
cta.config.datetime = Clock
from cta.cli import main
raise SystemExit(main())
"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if crash is not None:
        env["CTA_FAULT_CRASH"] = crash
    return subprocess.run([sys.executable, "-c", script, *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", check=False)


def _action(name):
    return {
        'estimate_cost': {'actions': ['submit_script', 'review', 'save']},
        'update_plan': {'steps': [{'id': 'write', 'title': '编写方案', 'status': 'current',
                                   'depends_on': [], 'references': []}]},
        'read_material': {'material_id': 'M-001'},
        'propose_brief': BRIEF,
    }[name]


@pytest.mark.parametrize('name', ['estimate_cost', 'update_plan', 'read_material', 'propose_brief'])
@pytest.mark.parametrize('point', ['after_tool_request', 'before_local_tool_receipt', 'after_tool_effect'])
def test_local_action_receipt_tracks_exact_call_without_replay(cli_env, model_script, name, point):
    task = cli_json('create', '--topic', '恢复动作', '--budget', '10', '--deadline', '2026-10-30')
    path = model_script([_tool(name, _action(name))])
    crashed = run_cli_process('run', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': point})
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json('inspect', task['task_id'])
    call = _pending_call(before)
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    receipt = json.loads(_tool_result(after, call['id'])['content'])
    assert receipt['recovered'] is True
    if point == 'after_tool_effect':
        assert receipt['ok'] is True
    else:
        assert receipt['ok'] is False and receipt['error'] == 'local_action_interrupted'
    for field in ('estimates', 'plan_history', 'briefs', 'ledger', 'attempts'):
        assert after[field] == before[field]
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents'] == 0
    assert after['progress']['used_rounds'] == before['progress']['used_rounds'] == 1
    calls = [call['id'] for m in after['messages'] if m['role'] == 'assistant' for call in m['tool_calls']]
    results = [m['tool_call_id'] for m in after['messages'] if m['role'] == 'tool']
    assert calls == results


@pytest.mark.parametrize('point', ['after_tool_request', 'after_tool_effect'])
def test_same_second_estimate_recovery_does_not_use_previous_result(cli_env, model_script, monkeypatch, point):
    _fixed_clock(monkeypatch)
    task = cli_json('create', '--topic', '同秒估算', '--budget', '10', '--deadline', '2026-10-30')
    action = _tool('estimate_cost', _action('estimate_cost'))
    model_script([action, _tool('search_materials', {'query': '自动卡点'}), action])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    initial = cli_json('inspect', task['task_id'])
    first = next(json.loads(m['content']) for m in initial['messages'] if m['role'] == 'tool' and m['name'] == 'estimate_cost')
    assert first['estimate']['incurred_cents'] == 0 and initial['resources']['sim_spent_cents'] == 20
    crashed = _frozen_cli_process('run', task['task_id'], '--max-rounds', '1', crash=point)
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json('inspect', task['task_id'])
    call = _pending_call(before)
    request = next(m for m in before['messages'] if m['role'] == 'assistant' and any(c['id'] == call['id'] for c in m['tool_calls']))
    assert initial['estimates'][-1]['created_at'] == request['created_at']
    resumed = _frozen_cli_process('resume', task['task_id'], '--max-rounds', '0')
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    receipt = json.loads(_tool_result(after, call['id'])['content'])
    if point == 'after_tool_request':
        assert receipt['error'] == 'local_action_interrupted' and not receipt['ok']
    else:
        assert receipt['ok'] and receipt['estimate_id'] != first['estimate_id']
        assert receipt['estimate_id'] == before['estimates'][-1]['estimate_id']
        assert receipt['estimate']['incurred_cents'] == 20 and receipt['estimate']['remaining_budget_cents'] == 980
    for field in ('estimates', 'ledger', 'attempts'):
        assert after[field] == before[field]
    assert after['progress']['used_rounds'] == before['progress']['used_rounds'] == 3


def test_executed_estimate_rejection_recovers_original_feedback(cli_env, model_script):
    task = cli_json('create', '--topic', '估算拒绝', '--budget', '10', '--deadline', '2026-10-30')
    path = model_script([_tool('estimate_cost', {'actions': ['submit_script']})])
    crashed = run_cli_process('run', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_tool_effect'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    call = _pending_call(before)
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    receipt = json.loads(_tool_result(after, call['id'])['content'])
    assert not receipt['ok'] and receipt['error'] == 'missing_delivery_actions'
    assert set(receipt['missing_actions']) == {'review', 'save'} and receipt['recovered']
    assert after['estimates'] == before['estimates'] and after['ledger'] == before['ledger']
    assert after['progress']['used_rounds'] == before['progress']['used_rounds'] == 1
