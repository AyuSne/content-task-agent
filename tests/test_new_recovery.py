import json

import pytest
from conftest import cli_json, run_cli_process
from test_campaign_delivery_new import _campaign, _result, _script_ready
from test_campaign_draft import _tool
from test_review_save import REVIEW_PASS, _review_item


def test_interrupted_new_review_does_not_reuse_old_review(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    before = cli_json('inspect', task['task_id'])
    assert _result(before, 'review_campaign')[0]['ok']
    path = model_script(prefix + [_tool('review_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_tool_request'})
    assert crashed.returncode == 70
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'review_campaign')[-1]
    assert result['ok'] is False and result['error'] == 'review_interrupted'
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_interrupted_brief_does_not_claim_previous_brief_in_same_second(cli_env, model_script, monkeypatch):
    from test_local_action_recovery import _fixed_clock, _frozen_cli_process
    _fixed_clock(monkeypatch)
    from test_brief_loop import BRIEF
    task = cli_json('create', '--topic', '简报恢复', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    first = _tool('propose_brief', BRIEF)
    model_script([first])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    before = cli_json('inspect', task['task_id'])
    model_script([first, first])
    crashed = _frozen_cli_process('resume', task['task_id'], '--max-rounds', '1', crash='after_tool_request')
    assert crashed.returncode == 70
    resumed = _frozen_cli_process('resume', task['task_id'], '--max-rounds', '0')
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    assert _result(after, 'propose_brief')[-1]['ok'] is False
    assert len(after['briefs']) == len(before['briefs']) == 1


def test_status_reports_frozen_prompt_after_installed_prompt_changes(cli_env, tmp_path):
    import shutil

    from conftest import REPO_ROOT

    from cta.prompts import PROMPT_VERSION
    runtime = tmp_path / 'runtime'
    for name in ('src', 'data', 'knowledge', '.agents/skills'):
        shutil.copytree(REPO_ROOT / name, runtime / name)
    shutil.copy2(REPO_ROOT / 'pyproject.toml', runtime / 'pyproject.toml')
    script = tmp_path / 'responses.json'
    script.write_text(json.dumps([_tool('stop_incomplete', {'reason': 'done'})]), encoding='utf-8')
    env = {'PYTHONPATH': str(runtime / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    data = tmp_path / 'data'
    create = run_cli_process('create', '--topic', '版本冻结', '--budget', '10', '--deadline', '2026-10-30', data_dir=data, extra_env=env)
    task_id = json.loads(create.stdout)['task_id']
    first = run_cli_process('run', task_id, '--max-rounds', '1', data_dir=data, extra_env=env)
    assert first.returncode == 0, first.stderr
    with (runtime / 'src/cta/prompts.py').open('a', encoding='utf-8') as stream:
        stream.write("\nPROMPT_VERSION = 'future-prompt'\n")
    status = run_cli_process('status', task_id, data_dir=data, extra_env=env)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['versions']['prompt'] == PROMPT_VERSION


def test_changed_handler_protocol_rejects_resume_before_executing_history(cli_env, tmp_path):
    import shutil

    from conftest import REPO_ROOT
    runtime = tmp_path / 'runtime'
    for name in ('src', 'data', 'knowledge', '.agents/skills'):
        shutil.copytree(REPO_ROOT / name, runtime / name)
    shutil.copy2(REPO_ROOT / 'pyproject.toml', runtime / 'pyproject.toml')
    script = tmp_path / 'responses.json'
    script.write_text(json.dumps([_tool('stop_incomplete', {'reason': 'first'}),
                                  _tool('stop_incomplete', {'reason': 'must not execute'})]), encoding='utf-8')
    env = {'PYTHONPATH': str(runtime / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    data = tmp_path / 'data'
    created = run_cli_process('create', '--topic', '处理逻辑', '--budget', '10', '--deadline', '2026-10-30', data_dir=data, extra_env=env)
    task_id = json.loads(created.stdout)['task_id']
    first = run_cli_process('run', task_id, '--max-rounds', '1', data_dir=data, extra_env=env)
    assert first.returncode == 0, first.stderr
    with (runtime / 'src/cta/protocol.py').open('a', encoding='utf-8') as stream:
        stream.write("\nHANDLER_VERSION = 'changed-handler'\n")
    before = json.loads(run_cli_process('inspect', task_id, data_dir=data, extra_env=env).stdout)
    resumed = run_cli_process('resume', task_id, data_dir=data, extra_env=env)
    assert resumed.returncode != 0
    result = json.loads(resumed.stdout)
    assert result['error'] == 'incompatible_protocol' and '恢复' in result['message']
    after = json.loads(run_cli_process('inspect', task_id, data_dir=data, extra_env=env).stdout)
    assert after['messages'] == before['messages']
    assert after['progress']['used_rounds'] == before['progress']['used_rounds']


def test_previous_handler_task_is_readable_but_not_resumed_by_save_protection(cli_env, tmp_path):
    import io
    import subprocess
    import tarfile

    from conftest import REPO_ROOT

    archive = subprocess.check_output(['git', 'archive', '1dfb4c0', 'src', 'fixtures', 'knowledge',
                                       '.agents/skills', 'pyproject.toml'], cwd=REPO_ROOT)
    runtime = tmp_path / 'previous'
    runtime.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(runtime, filter='data')
    script = tmp_path / 'responses.json'
    script.write_text(json.dumps([_tool('stop_incomplete', {'reason': 'first'})]), encoding='utf-8')
    data = cli_env / 'data'
    old_env = {'PYTHONPATH': str(runtime / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    created = run_cli_process('create', '--topic', '版本隔离', '--budget', '10', '--deadline', '2026-10-30',
                              data_dir=data, extra_env=old_env)
    task_id = json.loads(created.stdout)['task_id']
    old_run = run_cli_process('run', task_id, '--max-rounds', '1', data_dir=data, extra_env=old_env)
    assert old_run.returncode == 0, old_run.stderr
    before = cli_json('inspect', task_id)
    resumed = run_cli_process('resume', task_id, data_dir=data, extra_env={'PYTHONPATH': str(REPO_ROOT / 'src')})
    assert resumed.returncode == 4
    result = json.loads(resumed.stdout)
    assert result['error'] == 'incompatible_protocol' and '1dfb4c0' in result['message']
    after = cli_json('inspect', task_id)
    assert after['messages'] == before['messages'] and after['attempts'] == before['attempts']
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_save_after_record_is_reconciled_for_this_call_without_recharge(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'save_after_record'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save']['status'] == 'unknown' and not before['save']['verified']
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert result['recovered'] and result['save']['output_id'] == ref['id']
    assert result['ok'] and result['save']['verified']
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    assert len(result['save']['files']) >= 5 and all(file['ok'] for file in result['save']['files'])



def test_save_after_record_preserves_previously_exported_operator_edit(cli_env, model_script):
    from pathlib import Path

    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    exported = cli_json('export', task['task_id'])
    campaign_file = Path(next(f['path'] for f in exported['files'] if f['path'].endswith('campaign-v1.md')))
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'save_after_record'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save']['status'] == 'unknown' and not before['save']['files']
    campaign_file.write_text('运营的外部修改', encoding='utf-8')
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert not result['ok'] and result['error'] == 'save_unknown'
    assert after['status'] != 'complete' and not after['save']['verified']
    assert campaign_file.read_text(encoding='utf-8') == '运营的外部修改'
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    repaired = cli_json('export', task['task_id'], '--repair')
    assert repaired['verified'] and repaired['delivery_complete']
    assert campaign_file.read_text(encoding='utf-8') != '运营的外部修改'
    assert cli_json('inspect', task['task_id'])['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


@pytest.mark.parametrize("fault", ["after_review_effect", "after_business_result_before_receipt"])
def test_incomplete_review_after_effect_preserves_failure_not_success(cli_env, model_script, fault):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    report = {'verdict': 'incomplete', 'completeness': 'partial', 'summary': '缺资料', 'findings': [], 'evidence_gaps': ['缺资料']}
    path = model_script(prefix + [_tool('review_campaign', {'campaign_ref': ref}), _review_item(report)])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': fault})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'review_campaign')[-1]
    assert not result['ok'] and result['error'] == 'review_incomplete' and result['recovered']
    assert result['review']['output_id'] == ref['id'] and result['review']['status'] == 'incomplete'
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


@pytest.mark.parametrize("fault", ["after_save_effect", "after_business_result_before_receipt"])
def test_save_completed_before_missing_receipt_is_reused_and_verified(cli_env, model_script, fault):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': fault})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save']['verified'] and before['save']['output_id'] == ref['id']
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert result['ok'] and result['recovered'] and result['save']['save_id'] == before['save']['save_id']
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_recovery_does_not_claim_saved_if_delivery_file_was_changed(cli_env, model_script):
    from pathlib import Path
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_save_effect'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    campaign_file = Path(next(f['path'] for f in before['save']['files'] if 'campaign-v1.md' in f['path']))
    campaign_file.write_text('运营的外部修改', encoding='utf-8')
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert result['ok'] is False and result['error'] == 'save_unknown'
    assert after['status'] != 'complete' and campaign_file.read_text(encoding='utf-8') == '运营的外部修改'
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_search_started_but_interrupted_is_not_reexecuted_as_success(cli_env, model_script):
    task = cli_json('create', '--topic', '检索恢复', '--budget', '10', '--deadline', '2026-10-30T12:00:00+08:00')
    path = model_script([_tool('search_materials', {'query': '自动卡点', 'mode': 'basic'})])
    crashed = run_cli_process('run', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'search_after_start'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'search_materials')[-1]
    assert not result['ok'] and result['error'] == 'search_interrupted' and result['recovered']
    assert len(after['attempts']) == 1 and after['attempts'][0]['status'] == 'interrupted'
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_pre_recovery_new_protocol_remains_readable_but_cannot_execute_new_handler(cli_env, tmp_path):
    import io

    # The old branch may later be cleaned; copy the pinned integration snapshot via git archive.
    import subprocess
    import tarfile

    from conftest import REPO_ROOT
    archive = subprocess.check_output(['git', 'archive', 'c5df5f9'], cwd=REPO_ROOT)
    old_root = tmp_path / 'old-runtime'
    old_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old_root, filter='data')
    script = tmp_path / 'responses.json'
    script.write_text(json.dumps([_tool('stop_incomplete', {'reason': 'old state'})]), encoding='utf-8')
    data = tmp_path / 'data'
    old_env = {'PYTHONPATH': str(old_root / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    created = run_cli_process('create', '--topic', '旧新协议快照', '--budget', '10', '--deadline', '2026-10-30', data_dir=data, extra_env=old_env)
    task_id = json.loads(created.stdout)['task_id']
    assert run_cli_process('run', task_id, '--max-rounds', '1', data_dir=data, extra_env=old_env).returncode == 0
    new_env = {'PYTHONPATH': str(REPO_ROOT / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    before_result = run_cli_process('inspect', task_id, data_dir=data, extra_env=new_env)
    assert before_result.returncode == 0, before_result.stderr
    before = json.loads(before_result.stdout)
    resumed = run_cli_process('resume', task_id, data_dir=data, extra_env=new_env)
    assert resumed.returncode == 4
    result = json.loads(resumed.stdout)
    assert result['error'] == 'incompatible_protocol' and 'c5df5f9' in result['message']
    after = json.loads(run_cli_process('inspect', task_id, data_dir=data, extra_env=new_env).stdout)
    assert before['messages'] == after['messages'] and before['attempts'] == after['attempts']


def test_pause_and_resume_keeps_clock_plan_and_adopted_inputs(cli_env, model_script, monkeypatch):
    from test_brief_loop import BRIEF
    from test_direct_script import SCRIPT
    from test_process_resume import _copy_runtime_inputs
    material_path, knowledge_dir = _copy_runtime_inputs(cli_env)
    monkeypatch.setenv('CTA_MATERIALS_PATH', str(material_path))
    monkeypatch.setenv('CTA_KNOWLEDGE_DIR', str(knowledge_dir))
    task = cli_json('create', '--topic', '连续恢复', '--budget', '10', '--deadline', '2026-10-30')
    plan = [{'id': 'write', 'title': '完成脚本', 'status': 'current', 'depends_on': [], 'references': []}]
    resource = 'references/evidence-examples.md'
    path = model_script([_tool('search_materials', {'query': '自动卡点'}), _tool('propose_brief', BRIEF),
                         _tool('update_plan', {'steps': plan}), _tool('read_material', {'material_id': 'M-001'}),
                         _tool('read_skill', {'name': 'campaign-creation', 'resource': resource}),
                         _tool('submit_script', {'brief_version': 1, 'script': SCRIPT})])
    cli_json('run', task['task_id'], '--max-rounds', '3')
    before = cli_json('inspect', task['task_id'])
    assert before['plan']['steps'] == plan and before['brief']['version'] == 1
    original_knowledge = before['knowledge']
    original_materials = before['material_snapshots']
    original_resource = (cli_env / '.agents/skills/campaign-creation' / resource).read_text(encoding='utf-8')
    payload = json.loads(material_path.read_text(encoding='utf-8'))
    for item in payload['materials']:
        if item['id'] == 'M-001':
            item['text'] = 'unadopted material change'
    material_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    (cli_env / '.agents/skills/campaign-creation' / resource).write_text('unadopted resource', encoding='utf-8')
    paused = cli_json('pause', task['task_id'])
    frozen = run_cli_process('run', task['task_id'], '--max-rounds', '3', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert frozen.returncode == 0, frozen.stderr
    during = json.loads(frozen.stdout)
    assert during['progress']['paused'] and during['run']['stop_reason'] == 'paused'
    assert during['progress']['sim_elapsed_ms'] == paused['progress']['sim_elapsed_ms']
    assert during['progress']['used_rounds'] == paused['progress']['used_rounds']
    assert during['resources']['sim_spent_cents'] == paused['resources']['sim_spent_cents']
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '3', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    assert after['plan']['steps'] == plan and after['brief']['version'] == 1
    assert after['knowledge'] == original_knowledge and after['material_snapshots'] == original_materials
    assert '旧资料标记AAA' in _result(after, 'read_material')[-1]['material']['text']
    assert _result(after, 'read_skill')[-1]['content'] == original_resource
    assert after['scripts'][0]['content']['script'] == SCRIPT
    assert after['progress']['used_rounds'] == 6 and after['resources']['sim_spent_cents'] == 140
    assert after['messages'][:len(before['messages'])] == before['messages']


def test_reused_tool_id_is_paired_with_its_own_request_sequence(cli_env, model_script):
    task = cli_json('create', '--topic', '调用配对', '--budget', '10', '--deadline', '2026-10-30')
    first = {'name': 'search_materials', 'id': 'repeat-id', 'arguments': {'query': '自动卡点'}}
    path = model_script([first, first])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_tool_request'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    results = _result(after, 'search_materials')
    assert len(results) == 2 and results[0]['ok'] and not results[1]['ok']
    assert results[1]['error'] == 'interrupted' and results[1]['recovered']
    assert len(after['attempts']) == len(before['attempts']) == 1


def test_save_started_before_record_keeps_charge_and_reports_no_verified_result(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'save_after_start'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save'] is None and before['attempts'][-1]['status'] == 'running'
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert not result['ok'] and result['error'] == 'save_interrupted' and result['recovered']
    assert after['attempts'][-1]['status'] == 'interrupted' and after['save'] is None
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_save_failure_with_missing_receipt_remains_failure(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_save_effect', 'CTA_FAIL_SAVE': '1'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    assert before['save']['status'] == 'failed' and not before['save']['verified']
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'save_campaign')[-1]
    assert not result['ok'] and result['error'] == 'save_failed' and result['recovered']
    assert result['save']['save_id'] == before['save']['save_id'] and after['status'] != 'complete'
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_recover_revised_script_using_brief_from_resource_only_input_version(cli_env, model_script):
    from copy import deepcopy

    from test_direct_script import SCRIPT

    task, prefix, script_ref = _script_ready(model_script)
    task_id = task['task_id']
    cli_json('update', task_id, '--budget', '20')
    revised = deepcopy(SCRIPT)
    revised['segments'][0]['voiceover'] = '资源调整后继续修改表达'
    path = model_script(prefix + [_tool('submit_script', {'brief_version': 1, 'script': revised,
                                                           'revises_script_ref': script_ref})])
    crashed = run_cli_process('resume', task_id, '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_tool_effect'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task_id)
    resumed = run_cli_process('resume', task_id, '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task_id)
    receipt = _result(after, 'submit_script')[-1]
    assert receipt['ok'] and receipt['recovered']
    assert receipt['script_ref']['id'] == before['scripts'][-1]['output_id']
    assert before['scripts'][-1]['input_version'] == 2 and before['brief']['input_version'] == 1
    assert after['scripts'] == before['scripts'] and after['attempts'] == before['attempts']
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
