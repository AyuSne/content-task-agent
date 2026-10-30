import hashlib
import json
from pathlib import Path

from conftest import cli_json, run_cli_process
from test_campaign_delivery_new import _new_draft_ready, _result
from test_campaign_draft import _tool
from test_review_dispositions_new import _reviewed
from test_review_save import REVIEW_PASS, _review_item


def test_human_draft_semantic_change_and_interruption_preserve_current_facts(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    cli_json('review', task_id)
    saved = cli_json('save', task_id)
    path = next(Path(item['path']) for item in saved['save']['files'] if item['path'].endswith('campaign-v1.md'))
    original = path.read_text(encoding='utf-8')
    edited = original.replace('演示操作并联系校园', '人工稿放弃旧方向，改用社团活动')
    assert edited != original
    path.write_text(edited, encoding='utf-8')
    imported = cli_json('import-draft', task_id, '--file', str(path))['import']
    for number in range(6):
        cli_json('update', task_id, '--requirement', f'历史要求{number}：旧方向')
    cli_json('update', task_id, '--audience', '研究生', '--requirement', '当前要求：不使用旧方向')
    before = cli_json('inspect', task_id)
    steps = [{'id': 'rework', 'title': '根据当前要求重新创作', 'status': 'current',
              'depends_on': [], 'references': []}]
    prefix = [_tool('stop_incomplete', {'reason': 'unused'})] * before['progress']['used_rounds']
    script = model_script(prefix + [_tool('update_plan', {'steps': steps}),
                                    _tool('stop_incomplete', {'reason': '需要按当前要求重新创作'})])
    crashed = run_cli_process('resume', task_id, '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script), 'CTA_FAULT_CRASH': 'after_tool_effect'})
    assert crashed.returncode == 70
    interrupted = cli_json('inspect', task_id)
    recovered = run_cli_process('resume', task_id, '--max-rounds', '0', data_dir=cli_env / 'data',
                                extra_env={'CTA_MODEL_SCRIPT': str(script)})
    assert recovered.returncode == 0, recovered.stderr
    after_recovery = cli_json('inspect', task_id)
    assert after_recovery['resources']['sim_spent_cents'] == interrupted['resources']['sim_spent_cents']
    assert after_recovery['progress']['used_rounds'] == interrupted['progress']['used_rounds']
    assert _result(after_recovery, 'update_plan')[-1]['recovered'] is True
    assert after_recovery['messages'][:len(before['messages'])] == before['messages']
    cli_json('resume', task_id, '--max-rounds', '1')
    after = cli_json('inspect', task_id)
    request = json.loads(after['llm_requests'][-1]['meta_json'])['request']
    snapshot = next(m['content'] for m in reversed(request['messages']) if (m.get('content') or '').startswith('[状态快照] '))
    state = json.loads(snapshot.split('] ', 1)[1])
    assert state['campaign']['version'] == imported['output_version']
    assert not state['campaign']['input_applicable']
    assert not state['review']['applies_to_current'] and not state['save']['applies_to_current']
    assert state['plan']['steps'] == steps
    content = '\n'.join(m.get('content') or '' for m in request['messages'])
    assert '人工稿放弃旧方向，改用社团活动' in content
    assert '历史要求0：旧方向' in content and '当前要求：不使用旧方向' in content
    assert after['status'] == 'incomplete'
    assert path.read_text(encoding='utf-8') == edited


def test_delivery_views_reconcile_plan_disposition_cost_and_verified_version(cli_env, model_script):
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    before = cli_json('inspect', task_id)
    assert before['status'] != 'complete'
    assert before['save'] is None
    steps = [{'id': 'delivery', 'title': '核对意见并交付', 'status': 'current',
              'depends_on': [], 'references': []}]
    evidence = {'kind': 'requirement', 'input_version': 1, 'field_path': 'requirements[0]', 'quote': '突出自动卡点'}
    decision = {'finding_id': 'F1', 'decision': 'rejected', 'reason': '原要求已支持该范围', 'evidence': [evidence]}
    model_script(prefix + [
        _tool('update_plan', {'steps': steps}),
        _tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref, 'decisions': [decision]}),
        _tool('save_campaign', {'campaign_ref': campaign_ref}),
    ])
    result = cli_json('resume', task_id, '--max-rounds', '3')
    assert result['status'] == 'complete'
    view = cli_json('inspect', task_id)
    save = _result(view, 'save_campaign')[-1]['save']
    assert save['verified'] and save['output_id'] == campaign_ref['id'] and save['output_version'] == campaign_ref['version']
    assert view['plan']['steps'] == steps
    assert view['dispositions'][0]['review_id'] == review_id
    assert view['dispositions'][0]['evidence'] == [evidence]
    assert view['resources']['sim_spent_cents'] == 210
    files = {Path(item['path']).name: Path(item['path']) for item in save['files']}
    campaign_text = files['campaign-v1.md'].read_text(encoding='utf-8')
    report = files['execution-report.md'].read_text(encoding='utf-8')
    assert '校园情境与教程结合' in campaign_text and '待制作' in campaign_text
    for fact in ('核对意见并交付', 'record_dispositions', '原要求已支持该范围', '突出自动卡点',
                 '模拟工具费用', 'pricing:2026-10-30.v2', '已发生 210 分', '产物版本：1', '版本 v1'):
        assert fact in report
    assert review_id in report and save['save_id'] in report
    for attempt in view['attempts']:
        assert attempt['id'] in report
    assert view['save']['save_id'] == save['save_id'] and view['save']['verified']
    assert all(record['ok'] for record in save['files'])
    for record in view['save']['files']:
        assert hashlib.sha256(Path(record['path']).read_bytes()).hexdigest() == record['sha256'], record['path']


def test_actual_request_has_consistent_system_tools_and_loaded_skill(cli_env, model_script):
    task = cli_json('create', '--topic', '组合输入核对', '--budget', '10', '--deadline', '2026-10-30')
    model_script([_tool('read_skill', {'name': 'campaign-creation', 'resource': 'references/evidence-examples.md'}),
                  _tool('stop_incomplete', {'reason': '完成请求核对'})])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    requests = [json.loads(item['meta_json'])['request'] for item in view['llm_requests']]
    first, second = requests
    names = {tool['function']['name'] for tool in first['tools']}
    assert names == {'read_skill', 'update_plan', 'review_campaign', 'save_campaign', 'search_materials',
                     'read_material', 'estimate_cost', 'propose_brief', 'submit_script', 'submit_campaign',
                     'request_clarification', 'record_dispositions', 'stop_incomplete'}
    assert first['tools'] == second['tools']
    assert all(tool['function']['description'] and tool['function']['parameters'] for tool in first['tools'])
    system = first['messages'][0]['content']
    skill = next(message['content'] for message in first['messages'] if (message.get('content') or '').startswith('[工作知识]'))
    skill_source = Path('.agents/skills/campaign-creation/SKILL.md').read_text(encoding='utf-8')
    assert skill_source.strip() in skill
    assert '已有依据充分时直接创作' in system and '信息充分时直接创作' in skill
    assert '不另调用模型' in next(tool['function']['description'] for tool in first['tools']
                              if tool['function']['name'] == 'submit_script')
    assert '接收不等于核验或交付' in next(tool['function']['description'] for tool in first['tools']
                                   if tool['function']['name'] == 'submit_campaign')
    assert any('[Skill目录]' in (message.get('content') or '') for message in first['messages'])
    assert any('[适用费用能力概览]' in (message.get('content') or '') for message in first['messages'])
    assert any('[状态快照]' in (message.get('content') or '') for message in first['messages'])
    reference = Path('.agents/skills/campaign-creation/references/evidence-examples.md').read_text(encoding='utf-8')
    reference_result = next(json.loads(message['content']) for message in second['messages']
                            if message['role'] == 'tool' and message.get('name') == 'read_skill')
    assert reference_result['content'].strip() == reference.strip()
