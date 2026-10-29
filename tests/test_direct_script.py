import json
import sqlite3
from copy import deepcopy

import pytest
from conftest import cli_json, run_cli, run_cli_process
from test_brief_loop import BRIEF

SCRIPT = {
    'title': '节奏剪辑', 'duration_seconds': 15, 'direction': '教程与校园情境结合',
    'segments': [{'segment_id': 's1', 'duration_seconds': 15, 'visual': '待制作操作录屏',
                  'voiceover': '让片段对齐节拍', 'on_screen_text': '对齐节拍', 'evidence_ids': ['e1']}],
    'evidence': [{'id': 'e1', 'claim': '片段对齐节拍', 'material_id': 'M-001',
                  'material_version': '2026-10-29.v1', 'quote': '把用户选中的视频片段对齐到节拍点',
                  'scope': 'iOS/Android 4.2 及以上', 'use': '功能演示'}],
}

def test_direct_script_is_saved_and_charged_without_generator(cli_env, model_script):
    task = cli_json('create', '--topic', '校园剪辑挑战', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'propose_brief', 'arguments': BRIEF, 'expected_tool_names': [
                      'read_skill', 'update_plan', 'review_campaign', 'save_campaign', 'search_materials',
                      'read_material', 'estimate_cost', 'propose_brief', 'submit_script', 'submit_campaign',
                      'request_clarification', 'record_disposition', 'record_dispositions', 'stop_incomplete']},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(x['content'] for x in view['messages'] if x['name'] == 'submit_script' and x['role'] == 'tool'))
    assert result['ok'] is True
    assert result['script_ref']['version'] == 1
    assert view['scripts'][0]['content']['script'] == SCRIPT
    assert view['scripts'][0]['content']['brief_version'] == 1
    assert view['resources']['sim_spent_cents'] == 120
    assert len(view['llm_requests']) == 2
    assert all(row['purpose'].startswith('main_loop_round_') for row in view['llm_requests'])
    system = '\n'.join(x['content'] for x in view['messages'] if x['role'] == 'system' and x['content'])
    assert 'submit_script' in system and '信息充分时直接创作' in system
    assert 'generate_script' not in system and '至少比较功能演示' not in system


def test_rejects_bad_evidence_without_charge_then_accepts_corrected_script(cli_env, model_script):
    task = cli_json('create', '--topic', '节奏', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    bad = deepcopy(SCRIPT)
    bad['evidence'][0]['quote'] = '材料不存在的引文'
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': bad}},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '3')
    view = cli_json('inspect', task['task_id'])
    results = [json.loads(x['content']) for x in view['messages'] if x['role'] == 'tool' and x['name'] == 'submit_script']
    assert results[0]['error'] == 'invalid_script'
    assert results[0]['started'] is False and results[0]['charged'] is False
    assert 'quote' in ' '.join(results[0]['details'])
    assert results[1]['ok'] is True
    assert len(view['scripts']) == 1 and len(view['attempts']) == 1
    assert view['resources']['sim_spent_cents'] == 120


def test_budget_rejection_does_not_start_script(cli_env, model_script):
    task = cli_json('create', '--topic', '预算', '--budget', '0.01', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(x['content'] for x in view['messages'] if x['role'] == 'tool' and x['name'] == 'submit_script'))
    assert result['error'] == 'budget_exceeded' and result['started'] is False
    assert view['scripts'] == [] and view['attempts'] == [] and view['ledger'] == []
    assert view['resources']['sim_spent_cents'] == 0


def test_deadline_rejection_and_unknown_brief_do_not_start_script(cli_env, model_script):
    task = cli_json('create', '--topic', '截止', '--budget', '10', '--deadline', '2026-10-29T00:00:01+08:00')
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 99, 'script': SCRIPT}},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '3')
    view = cli_json('inspect', task['task_id'])
    results = [json.loads(x['content']) for x in view['messages'] if x['role'] == 'tool' and x['name'] == 'submit_script']
    assert results[0]['error'] == 'invalid_script' and 'brief_version' in ' '.join(results[0]['details'])
    assert results[1]['error'] == 'deadline_exceeded'
    assert all(result['started'] is False and result['charged'] is False for result in results)
    assert view['scripts'] == [] and view['attempts'] == [] and view['ledger'] == []


def test_revision_references_and_preserves_the_complete_previous_script(cli_env, model_script):
    task = cli_json('create', '--topic', '返修', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    prefix = [{'name': 'propose_brief', 'arguments': BRIEF},
              {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}]
    model_script(prefix)
    cli_json('run', task['task_id'], '--max-rounds', '2')
    first = cli_json('inspect', task['task_id'])['scripts'][0]
    revised = deepcopy(SCRIPT)
    revised['segments'][0]['voiceover'] = '先选择片段，再对齐节拍。'
    ref = {'id': first['output_id'], 'version': first['version']}
    model_script(prefix + [{'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': revised, 'revises_script_ref': ref}}])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    assert len(view['scripts']) == 2
    assert view['scripts'][0]['content']['script'] == SCRIPT
    assert view['scripts'][1]['content']['script'] == revised
    assert view['scripts'][1]['content']['revises_script_ref'] == ref
    assert view['resources']['sim_spent_cents'] == 240
    assert [a['mode'] for a in view['attempts']] == ['submit_script', 'revision']


def test_rejects_another_tasks_revision_reference_without_charge(cli_env, model_script):
    a = cli_json('create', '--topic', '参考原稿', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    b = cli_json('create', '--topic', '另一任务', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}])
    cli_json('run', a['task_id'], '--max-rounds', '2')
    original = cli_json('inspect', a['task_id'])['scripts'][0]
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT,
                   'revises_script_ref': {'id': original['output_id'], 'version': 1}}}])
    cli_json('run', b['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', b['task_id'])
    result = json.loads(next(x['content'] for x in view['messages'] if x['role'] == 'tool' and x['name'] == 'submit_script'))
    assert result['error'] == 'invalid_script' and 'revises_script_ref' in ' '.join(result['details'])
    assert result['started'] is False and not view['attempts'] and not view['scripts']


@pytest.mark.parametrize('fault,expected_error,versions,attempts', [
    ('after_tool_request', 'script_interrupted', 0, 0),
    ('script_after_start', 'script_interrupted', 0, 1),
    ('after_tool_effect', None, 1, 1),
])
def test_cross_process_recovery_preserves_attempt_and_verifies_script_effect(cli_env, model_script, fault, expected_error, versions, attempts):
    task = cli_json('create', '--topic', '恢复', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    script_path = model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                                {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}},
                                {'name': 'stop_incomplete', 'arguments': {'reason': '测试停止'}}])
    # Run the brief separately: after_tool_effect is a generic hook.
    cli_json('run', task['task_id'], '--max-rounds', '1')
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script_path), 'CTA_FAULT_CRASH': fault})
    assert crashed.returncode == 70
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script_path)})
    assert resumed.returncode == 0, resumed.stderr
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(x['content'] for x in view['messages'] if x['role'] == 'tool' and x['name'] == 'submit_script'))
    assert result.get('error') == expected_error
    assert result['recovered'] is True
    assert len(view['scripts']) == versions
    assert len(view['attempts']) == attempts and view['resources']['sim_spent_cents'] == 120 * attempts
    if attempts:
        assert view['attempts'][0]['status'] == ('success' if versions else 'interrupted')


def test_legacy_task_is_readable_but_execution_and_writes_are_blocked(cli_env, model_script):
    task = cli_json('create', '--topic', '旧任务', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    with sqlite3.connect(cli_env / 'data' / 'mock' / 'tasks.db') as conn:
        conn.execute("UPDATE tasks SET protocol_version = 'legacy' WHERE id = ?", (task['task_id'],))
    model_script([{'name': 'propose_brief', 'arguments': BRIEF}])
    assert cli_json('status', task['task_id'])['versions']['protocol'] == 'legacy'
    for command in [('run', task['task_id']), ('pause', task['task_id']), ('review', task['task_id']),
                    ('update', task['task_id'], '--budget', '12')]:
        code, out, _ = run_cli(*command)
        assert code != 0
        blocked = json.loads(out)
        assert blocked['error'] == 'legacy_protocol'
        assert '836aa827' in blocked['message']
    view = cli_json('inspect', task['task_id'])
    assert view['llm_requests'] == [] and view['attempts'] == []


def test_clarification_after_script_preserves_the_accepted_version(cli_env, model_script):
    task = cli_json('create', '--topic', '节奏', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}},
                  {'name': 'request_clarification', 'arguments': {'questions': [
                      {'question': '是否可以补充产品材料？', 'impact': '影响其余内容', 'blocking': True}]}}])
    result = cli_json('run', task['task_id'], '--max-rounds', '3')
    view = cli_json('inspect', task['task_id'])
    assert result['status'] == 'needs_input'
    assert view['scripts'][0]['content']['script'] == SCRIPT
    assert view['scripts'][0]['content']['brief_version'] == 1
    assert len(view['attempts']) == 1 and view['resources']['sim_spent_cents'] == 120
    assert view['clarifications'][0]['questions'][0]['blocking'] is True
