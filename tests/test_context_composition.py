import json

from conftest import cli_json


def test_full_actual_request_after_updates_starts_with_stable_prefix_and_retains_tool_history(cli_env, model_script):
    task = cli_json('create', '--topic', '原主题', '--budget', '10', '--deadline', '2026-10-30')
    task_id = task['task_id']
    cli_json('update', task_id, '--audience', '学生')
    cli_json('update', task_id, '--requirement', '当前原始要求')
    model_script([
        {'name': 'read_skill', 'arguments': {'name': 'campaign-creation', 'resource': 'references/evidence-examples.md'}},
        {'name': 'stop_incomplete', 'arguments': {'reason': '等待下一步'}},
    ])
    cli_json('run', task_id, '--max-rounds', '2')
    view = cli_json('inspect', task_id)
    requests = [json.loads(item['meta_json'])['request'] for item in view['llm_requests']]
    first, second = requests
    assert first['messages'][0]['role'] == 'system'
    assert '[Skill目录]' in first['messages'][1]['content']
    assert '[工作知识]' in first['messages'][2]['content']
    assert any('[适用费用能力概览]' in (message.get('content') or '') for message in first['messages'])
    assert any('当前原始要求' in (message.get('content') or '') for message in first['messages'])
    assert any('[状态快照]' in (message.get('content') or '') for message in first['messages'])
    assert len(first['tools']) == 13
    assert all(tool['function']['description'] and tool['function']['parameters'] for tool in first['tools'])
    assert first['tools'] == second['tools']
    call = next(message for message in second['messages'] if message.get('tool_calls'))
    result = next(message for message in second['messages'] if message['role'] == 'tool')
    assert call['tool_calls'][0]['id'] == result['tool_call_id']
    assert 'references/evidence-examples.md' in result['content']
    assert first['mock'] is True and first['adapter'] == 'ScriptedClient' and first['cursor'] == 0
    assert 'stream' not in first and 'thinking' not in first and 'extra_body' not in first
    assert 'sk-test-super-secret-key' not in json.dumps(requests)


def test_script_tool_contract_and_system_prompt_agree_on_material_evidence(cli_env, model_script):
    task = cli_json('create', '--topic', '校园活动', '--budget', '10', '--deadline', '2026-10-30')
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': '仅检查输入'}}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    request = json.loads(cli_json('inspect', task['task_id'])['llm_requests'][0]['meta_json'])['request']
    prompt = request['messages'][0]['content']
    script_tool = next(t for t in request['tools'] if t['function']['name'] == 'submit_script')
    assert '段落 material_ids' not in prompt
    assert '素材表 material_id' in prompt
    schema = script_tool['function']['parameters']['properties']['script']['properties']
    assert 'material_ids' not in schema['segments']['items']['properties']
    assert 'evidence_ids' in schema['segments']['items']['properties']


def test_current_state_identifies_new_human_draft_and_prior_review_and_save_as_historical(cli_env, model_script):
    from pathlib import Path

    from conftest import run_cli
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    cli_json('review', task_id)
    saved = cli_json('save', task_id)
    path = next(Path(item['path']) for item in saved['save']['files'] if item['path'].endswith('campaign-v1.md'))
    path.write_text(path.read_text(encoding='utf-8').replace('演示操作并联系校园', '新稿策略'), encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 0, out
    imported = json.loads(out)['import']
    # Cursor accounts for four original main calls. The next scripted action observes the state.
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': 'unused'}}] * 4 +
                 [{'name': 'stop_incomplete', 'arguments': {'reason': '检查新稿状态'}}])
    cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    request = json.loads(view['llm_requests'][-1]['meta_json'])['request']
    snapshot = next(m['content'] for m in reversed(request['messages']) if (m.get('content') or '').startswith('[状态快照] '))
    state = json.loads(snapshot.split('] ', 1)[1])
    assert state['campaign']['id'] == imported['output_id']
    assert state['campaign']['version'] == imported['output_version']
    assert state['review']['output_version'] == 1 and state['review']['applies_to_current'] is False
    assert state['save']['output_version'] == 1 and state['save']['applies_to_current'] is False
    assert state['status'] != 'complete'


def test_delivery_exports_the_brief_referenced_by_the_campaign_not_a_later_brief(cli_env, model_script):
    from copy import deepcopy
    from pathlib import Path

    from test_brief_loop import BRIEF
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    newer = deepcopy(BRIEF)
    newer['goal'] = '后来提出的另一份简报目标'
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': 'unused'}}] * 4 +
                 [{'name': 'propose_brief', 'arguments': newer}, _review_item(REVIEW_PASS)])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    assert len(cli_json('inspect', task['task_id'])['briefs']) == 2
    cli_json('review', task['task_id'])
    result = cli_json('save', task['task_id'])
    files = result['save']['files']
    brief_file = next(Path(item['path']) for item in files if 'brief-v' in item['path'])
    assert brief_file.name == 'brief-v1.json'
    assert json.loads(brief_file.read_text(encoding='utf-8'))['goal'] == BRIEF['goal']


def test_missing_save_receipt_with_external_edit_can_be_explicitly_imported_and_rechecked(cli_env, model_script):
    from pathlib import Path

    from conftest import run_cli, run_cli_process
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    from test_campaign_draft import _tool
    from test_review_save import REVIEW_PASS, _review_item

    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    prefix += [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    path = model_script(prefix + [_tool('save_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_save_effect'})
    assert crashed.returncode == 70
    before = cli_json('inspect', task['task_id'])
    campaign_file = Path(next(f['path'] for f in before['save']['files'] if f['path'].endswith('campaign-v1.md')))
    original = campaign_file.read_text(encoding='utf-8')
    edited = original.replace('演示操作并联系校园', '外部编辑的新策略')
    assert edited != original
    campaign_file.write_text(edited, encoding='utf-8')
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    unknown = cli_json('inspect', task['task_id'])
    assert unknown['save']['status'] == 'unknown' and campaign_file.read_text(encoding='utf-8') == edited
    assert unknown['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    code, out, _ = run_cli('import-draft', task['task_id'], '--file', str(campaign_file))
    assert code == 0, out
    imported = json.loads(out)['import']
    assert imported['output_version'] == 2
    assert cli_json('inspect', task['task_id'])['status'] != 'complete'
    reviewed = cli_json('review', task['task_id'])
    assert reviewed['review']['output_version'] == 2
    saved = cli_json('save', task['task_id'])
    assert saved['save']['verified'] and saved['save']['output_version'] == 2
    assert campaign_file.read_text(encoding='utf-8') == edited


def test_missing_save_receipt_external_edit_requires_explicit_repair(cli_env, model_script):
    from pathlib import Path

    from conftest import run_cli_process
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    from test_campaign_draft import _tool
    from test_review_save import REVIEW_PASS, _review_item

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
    campaign_file = Path(next(f['path'] for f in before['save']['files'] if f['path'].endswith('campaign-v1.md')))
    original = campaign_file.read_text(encoding='utf-8')
    edited = original.replace('演示操作并联系校园', '未导入的人工修改')
    campaign_file.write_text(edited, encoding='utf-8')
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    unknown = cli_json('inspect', task['task_id'])
    assert unknown['save']['status'] == 'unknown'
    assert campaign_file.read_text(encoding='utf-8') == edited
    ordinary = cli_json('export', task['task_id'])
    assert not ordinary['delivery_complete'] and campaign_file.read_text(encoding='utf-8') == edited
    repaired = cli_json('export', task['task_id'], '--repair')
    assert repaired['verified'] and repaired['delivery_complete']
    assert campaign_file.read_text(encoding='utf-8') == original
    after = cli_json('inspect', task['task_id'])
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_old_handler_tasks_remain_readable_but_do_not_resume_after_context_contract_change(cli_env, tmp_path):
    import io
    import subprocess
    import tarfile

    from conftest import REPO_ROOT, run_cli_process
    archive = subprocess.check_output(['git', 'archive', '804247d'], cwd=REPO_ROOT)
    runtime = tmp_path / 'previous-runtime'
    runtime.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(runtime, filter='data')
    script = tmp_path / 'responses.json'
    script.write_text(json.dumps([{'name': 'stop_incomplete', 'arguments': {'reason': '旧代码'}}]), encoding='utf-8')
    data = tmp_path / 'data'
    env = {'PYTHONPATH': str(runtime / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    result = run_cli_process('create', '--topic', '恢复代码', '--budget', '10', '--deadline', '2026-10-30', data_dir=data, extra_env=env)
    task_id = json.loads(result.stdout)['task_id']
    assert run_cli_process('run', task_id, '--max-rounds', '1', data_dir=data, extra_env=env).returncode == 0
    new_env = {'PYTHONPATH': str(REPO_ROOT / 'src'), 'CTA_MODEL_SCRIPT': str(script)}
    before = run_cli_process('inspect', task_id, data_dir=data, extra_env=new_env)
    assert before.returncode == 0
    resumed = run_cli_process('resume', task_id, data_dir=data, extra_env=new_env)
    assert resumed.returncode == 4
    assert json.loads(resumed.stdout)['error'] == 'incompatible_protocol'
    assert '804247d' in json.loads(resumed.stdout)['message']
    after = run_cli_process('inspect', task_id, data_dir=data, extra_env=new_env)
    assert json.loads(before.stdout)['messages'] == json.loads(after.stdout)['messages']


def test_prior_saved_campaign_does_not_bypass_stale_review_after_material_adoption(cli_env, model_script):
    from conftest import run_cli
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    cli_json('review', task_id)
    saved = cli_json('save', task_id)
    before = cli_json('inspect', task_id)
    cli_json('adopt-material', task_id, '--version', '2026-10-30.v1')
    code, out, _ = run_cli('save', task_id)
    result = json.loads(out)
    assert code == 4 and result['save_error']['code'] == 'review_stale'
    assert result['status'] != 'complete'
    assert result['save']['save_id'] == saved['save']['save_id']
    assert result['save']['verified'] is True
    after = cli_json('inspect', task_id)
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']


def test_captured_request_redacts_special_characters_in_credentials(cli_env, model_script, monkeypatch):
    secret = 'secret-"quoted"-\\value'
    monkeypatch.setenv('DEEPSEEK_API_KEY', secret)
    task = cli_json('create', '--topic', '脱敏', '--budget', '10', '--deadline', '2026-10-30', '--requirement', secret)
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': '完成检查'}}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    meta = json.loads(cli_json('inspect', task['task_id'])['llm_requests'][0]['meta_json'])
    contents = [message.get('content') or '' for message in meta['request']['messages']]
    assert all(secret not in content and json.dumps(secret, ensure_ascii=False)[1:-1] not in content for content in contents)
    assert any('***redacted***' in content for content in contents)


def test_new_pricing_overview_has_no_obsolete_internal_revision_requests_and_old_version_is_retained(cli_env, model_script, monkeypatch):
    task = cli_json('create', '--new', '--topic', '新价目', '--budget', '10', '--deadline', '2026-10-30')
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': '核对费用'}}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    request = json.loads(cli_json('inspect', task['task_id'])['llm_requests'][0]['meta_json'])['request']
    overview = next(m['content'] for m in request['messages'] if (m.get('content') or '').startswith('[适用费用能力概览] '))
    prices = json.loads(overview.split('] ', 1)[1])
    assert prices['version'] == '2026-10-30.v2'
    assert prices['modes']['revision']['max_internal_requests'] is None
    assert prices['modes']['revision']['cost_cents'] == 120
    monkeypatch.setenv('CTA_PRICING_VERSION', '2026-10-30.v1')
    old = cli_json('create', '--new', '--topic', '原价目', '--budget', '10', '--deadline', '2026-10-30')
    assert old['pricing_overview']['version'] == '2026-10-30.v1'
    assert old['pricing_overview']['modes']['revision']['max_internal_requests'] == 2


def test_semantic_change_keeps_history_but_rejects_old_campaign_and_marks_conclusions_inapplicable(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_campaign_draft import _tool
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    cli_json('review', task_id)
    cli_json('save', task_id)
    before = cli_json('inspect', task_id)
    old = next(o for o in before['outputs'] if o['kind'] == 'campaign')
    old_ref = {'id': old['output_id'], 'version': old['version']}
    cli_json('update', task_id, '--audience', '研究生', '--requirement', '不要使用旧要求')
    prefix = [_tool('stop_incomplete', {'reason': 'unused'})] * before['progress']['used_rounds']
    model_script(prefix + [_tool('review_campaign', {'campaign_ref': old_ref}),
                           _tool('stop_incomplete', {'reason': '需要重新创作'})])
    cli_json('resume', task_id, '--max-rounds', '2')
    after = cli_json('inspect', task_id)
    request = json.loads(after['llm_requests'][-1]['meta_json'])['request']
    snapshot = next(m['content'] for m in reversed(request['messages']) if (m.get('content') or '').startswith('[状态快照] '))
    state = json.loads(snapshot.split('] ', 1)[1])
    assert state['input_version'] == 2 and not state['campaign']['input_applicable']
    assert not state['review']['applies_to_current'] and not state['save']['applies_to_current']
    assert '不是产物、核验或保存成功的证据' in state['plan']['interpretation']
    receipts = [json.loads(m['content']) for m in after['messages'] if m['name'] == 'review_campaign' and m['role'] == 'tool']
    assert receipts[-1]['ok'] is False and after['status'] == 'incomplete'
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    assert after['outputs'] == before['outputs'] and after['messages'][:len(before['messages'])] == before['messages']


def test_real_sdk_cli_request_record_matches_local_wire_payload(cli_env, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from conftest import run_cli

    received = []
    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            payload = json.dumps({
                'id': 'local-response', 'object': 'chat.completion', 'created': 0, 'model': 'deepseek-flash',
                'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                    'tool_calls': [{'id': 'local-1', 'type': 'function', 'function': {
                        'name': 'stop_incomplete', 'arguments': '{"reason":"本地验证"}'}}]}}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8},
            }).encode('utf-8')
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        monkeypatch.setenv('DEEPSEEK_BASE_URL', f'http://127.0.0.1:{server.server_port}')
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-only-secret')
        code, out, err = run_cli('--mode', 'real', 'create', '--topic', '本地请求验证', '--budget', '10', '--deadline', '2026-10-30')
        assert code == 0, err
        task_id = json.loads(out)['task_id']
        code, out, err = run_cli('--mode', 'real', 'run', task_id, '--max-rounds', '1')
        assert code == 0, err
        code, out, err = run_cli('--mode', 'real', 'inspect', task_id)
        assert code == 0, err
        request = json.loads(json.loads(out)['llm_requests'][0]['meta_json'])['request']
        assert request['adapter'] == 'DeepSeekClient' and request['mock'] is False
        assert len(received) == 1
        for field in ('model', 'messages', 'tools', 'tool_choice', 'stream'):
            assert request[field] == received[0][field]
        assert request['extra_body']['thinking'] == received[0]['thinking'] == {'type': 'disabled'}
        assert 'local-only-secret' not in json.dumps(request, ensure_ascii=False)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
