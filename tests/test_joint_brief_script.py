import json
from copy import deepcopy

import pytest
from conftest import cli_json, run_cli_process
from test_brief_loop import BRIEF
from test_direct_script import SCRIPT


def test_joint_submission_creates_both_versions_with_one_attempt(cli_env, model_script):
    task = cli_json('create', '--topic', '联合创作', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == 'submit_script'))
    assert result['ok'] is True
    assert result['brief_version'] == 1 and result['script_ref']['version'] == 1
    assert view['brief']['version'] == 1 and view['brief']['content']['goal'] == BRIEF['goal']
    assert view['scripts'][0]['content']['brief_version'] == 1
    assert view['scripts'][0]['content']['script'] == SCRIPT
    assert len(view['attempts']) == len(view['ledger']) == 1
    assert view['resources']['sim_spent_cents'] == 120
    assert len(view['llm_requests']) == 1
    assert view['versions']['prompt'] == 'main-2026-10-30.joint-brief-v1'

@pytest.mark.parametrize('invalid', ['brief', 'script'])
def test_invalid_joint_submission_is_atomic_and_can_be_corrected(cli_env, model_script, invalid):
    task = cli_json('create', '--topic', '联合拒绝', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    bad_brief, bad_script = deepcopy(BRIEF), deepcopy(SCRIPT)
    if invalid == 'brief':
        bad_brief['product_facts'][0]['quote'] = '不存在的材料'
    else:
        bad_script['evidence'][0]['quote'] = '不存在的材料'
    first = {'name': 'submit_script', 'arguments': {'brief': bad_brief, 'script': bad_script}}
    good = {'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}}
    model_script([first, good])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    before = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in before['messages'] if m['role'] == 'tool' and m['name'] == 'submit_script'))
    assert result['ok'] is False and result['started'] is False and result['charged'] is False
    assert invalid in ' '.join(result['details']) and 'quote' in ' '.join(result['details'])
    assert before['brief'] is None and before['briefs'] == [] and before['scripts'] == []
    assert before['attempts'] == before['ledger'] == [] and before['resources']['sim_spent_cents'] == 0
    assert any(m['role'] == 'assistant' and m['tool_calls'] for m in before['messages'])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    after = cli_json('inspect', task['task_id'])
    assert after['brief']['version'] == 1 and len(after['scripts']) == 1
    assert len(after['attempts']) == len(after['ledger']) == 1 and after['resources']['sim_spent_cents'] == 120


def test_joint_submission_rejected_on_budget_or_ambiguous_brief_without_side_effects(cli_env, model_script):
    task = cli_json('create', '--topic', '预算', '--budget', '0.01', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'brief_version': 1, 'script': SCRIPT}},
                  {'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}}])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    results = [json.loads(m['content']) for m in view['messages'] if m['role'] == 'tool' and m['name'] == 'submit_script']
    assert '必须且只能' in ' '.join(results[0]['details'])
    assert results[1]['error'] == 'budget_exceeded'
    assert all(not r['started'] and not r['charged'] for r in results)
    assert view['brief'] is None and not view['scripts'] and not view['attempts'] and not view['ledger']


@pytest.mark.parametrize('fault,expected_started,expected_saved', [
    ('script_after_start', True, False),
    ('after_tool_effect', True, True),
])
def test_joint_submission_recovers_actual_effect_without_charging_twice(cli_env, model_script, fault, expected_started, expected_saved):
    task = cli_json('create', '--topic', '联合恢复', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    path = model_script([{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}},
                         {'name': 'stop_incomplete', 'arguments': {'reason': '测试停止'}}])
    crashed = run_cli_process('run', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': fault})
    assert crashed.returncode == 70
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == 'submit_script'))
    assert result['recovered'] and result['started'] == expected_started
    assert result['ok'] == expected_saved
    assert len(view['briefs']) == len(view['scripts']) == int(expected_saved)
    assert len(view['attempts']) == len(view['ledger']) == 1 and view['resources']['sim_spent_cents'] == 120
    if expected_saved:
        assert result['brief_version'] == view['brief']['version'] == view['scripts'][0]['content']['brief_version']
        assert result['script_ref']['id'] == view['scripts'][0]['output_id']
    else:
        assert result['error'] == 'script_interrupted' and result['charged']


def test_joint_success_does_not_prompt_a_second_script_as_if_only_brief_was_saved(cli_env, model_script):
    task = cli_json('create', '--topic', '连续创作', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    model_script([{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}},
                  {'name': 'stop_incomplete', 'arguments': {'reason': '等待完整方案能力'}}])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    assert not any('[阶段推进] 业务简报已保存' in (m['content'] or '') for m in view['messages'])
    assert len(view['scripts']) == len(view['attempts']) == 1


def test_crash_between_joint_writes_rolls_back_both_but_preserves_started_attempt(cli_env, model_script):
    task = cli_json('create', '--topic', '联合写入故障', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    path = model_script([{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}},
                         {'name': 'stop_incomplete', 'arguments': {'reason': '测试停止'}}])
    crashed = run_cli_process('run', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'joint_after_brief'})
    assert crashed.returncode == 70
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == 'submit_script'))
    assert result['error'] == 'script_interrupted' and result['started'] and result['charged']
    assert result['consumption']['cost_cents'] == 120 and result['consumption']['duration_ms'] > 0
    assert view['brief'] is None and not view['briefs'] and not view['scripts']
    assert len(view['attempts']) == len(view['ledger']) == 1 and view['resources']['sim_spent_cents'] == 120

@pytest.mark.parametrize('joint', [False, True])
@pytest.mark.parametrize('group,field', [('creative_requirements', 'source'), ('suggestions', 'basis'), ('sources', 'used_for')])
def test_brief_rejects_missing_source_in_both_submission_paths(cli_env, model_script, joint, group, field):
    task = cli_json('create', '--topic', '来源', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    brief = deepcopy(BRIEF)
    del brief[group][0][field]
    name = 'submit_script' if joint else 'propose_brief'
    arguments = {'brief': brief, 'script': SCRIPT} if joint else brief
    model_script([{'name': name, 'arguments': arguments}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == name))
    assert not result['ok'] and field in ' '.join(result['details'])
    assert view['brief'] is None and not view['scripts'] and not view['attempts'] and not view['ledger']

@pytest.mark.parametrize('joint', [False, True])
def test_brief_does_not_silently_treat_unspecified_unknown_as_nonblocking(cli_env, model_script, joint):
    task = cli_json('create', '--topic', '未知', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    brief = deepcopy(BRIEF)
    del brief['unknowns'][0]['blocking']
    name = 'submit_script' if joint else 'propose_brief'
    model_script([{'name': name, 'arguments': {'brief': brief, 'script': SCRIPT} if joint else brief}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == name))
    assert not result['ok'] and 'blocking' in ' '.join(result['details'])
    assert view['brief'] is None and not view['scripts'] and not view['attempts']


def test_joint_script_is_usable_for_complete_campaign_snapshot(cli_env, model_script):
    from test_campaign_delivery_new import _campaign

    task = cli_json('create', '--topic', '联合交付', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    prefix = [{'name': 'submit_script', 'arguments': {'brief': BRIEF, 'script': SCRIPT}}]
    model_script(prefix)
    cli_json('run', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    script = view['scripts'][0]
    ref = {'id': script['output_id'], 'version': script['version']}
    model_script(prefix + [{'name': 'submit_campaign', 'arguments': {'script_ref': ref, 'campaign': _campaign()}}])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    result = json.loads(next(m['content'] for m in view['messages'] if m['role'] == 'tool' and m['name'] == 'submit_campaign'))
    assert result['ok'] and result['campaign_ref']['version'] == 1
    campaign = next(x for x in view['outputs'] if x['kind'] == 'campaign')
    assert campaign['content']['script_ref'] == ref and campaign['content']['script'] == SCRIPT
    assert view['brief']['version'] == script['content']['brief_version'] == 1
    assert view['resources']['sim_spent_cents'] == 120 and len(view['attempts']) == 1
