import json

from conftest import run_cli


def test_public_cli_redacts_configured_key_without_changing_task_input(cli_env, monkeypatch):
    key = 'fake-secret-"quoted"-\\value'
    monkeypatch.setenv('DEEPSEEK_API_KEY', key)
    code, output, _ = run_cli('create', '--topic', '正常主题', '--budget', '10', '--deadline', '2026-10-30', '--requirement', '原文：' + key)
    assert code == 0
    assert key not in output and json.dumps(key, ensure_ascii=False)[1:-1] not in output
    task_id = json.loads(output)['task_id']
    for command in ('status', 'inspect', 'list'):
        args = (command,) if command == 'list' else (command, task_id)
        code, output, _ = run_cli(*args)
        assert code == 0
        assert key not in output and json.dumps(key, ensure_ascii=False)[1:-1] not in output
    # Internal state remains usable for subsequent updates, rather than persisting the placeholder.
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'different-test-key')
    code, output, _ = run_cli('status', task_id)
    assert code == 0
    assert json.loads(output)['input']['requirements'] == ['原文：' + key]


def test_delivered_trace_and_report_redact_secret_preserving_verified_campaign(cli_env, monkeypatch):
    from pathlib import Path

    from conftest import cli_json
    from test_autonomous_delivery import _responsive
    from test_review_save import REVIEW_PASS

    key = 'fake-secret-"quoted"-\\value'
    monkeypatch.setenv('DEEPSEEK_API_KEY', key)
    _responsive(monkeypatch, [REVIEW_PASS])
    created = cli_json('create', '--topic', '业务主题', '--budget', '10', '--deadline', '2026-10-30', '--requirement', '背景：' + key)
    task_id = created['task_id']
    result = cli_json('run', task_id)
    assert result['status'] == 'complete'
    files = result['save']['files']
    for file in files:
        content = Path(file['path']).read_text(encoding='utf-8')
        assert key not in content
        assert json.dumps(key, ensure_ascii=False)[1:-1] not in content
    report = next(Path(file['path']).read_text(encoding='utf-8') for file in files if file['path'].endswith('execution-report.md'))
    trace = next(Path(file['path']).read_text(encoding='utf-8') for file in files if file['path'].endswith('trace.jsonl'))
    assert '***redacted***' in trace
    assert '业务主题' in report
    before = result['resources']['sim_spent_cents']
    result = cli_json('reconcile', task_id)
    assert result['save']['verified'] is True
    assert result['resources']['sim_spent_cents'] == before


def test_campaign_with_configured_credential_is_not_exported_or_claimed_saved(cli_env, model_script, monkeypatch):
    from conftest import cli_json
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    from test_campaign_draft import _tool
    from test_review_save import REVIEW_PASS, _review_item

    key = 'fake-secret-"quoted"-\\value'
    monkeypatch.setenv('DEEPSEEK_API_KEY', key)
    task, prefix, script_ref = _script_ready(model_script)
    campaign = _campaign()
    campaign['activity']['theme'] = '主题 ' + key
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': campaign})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    model_script(prefix + [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS)])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    code, result, _ = run_cli('export', task['task_id'])
    assert code != 0 and json.loads(result)['error'] == 'sensitive_delivery_content'
    before = cli_json('status', task['task_id'])['resources']['sim_spent_cents']
    code, result, _ = run_cli('save', task['task_id'])
    assert code != 0 and json.loads(result)['save_error']['code'] == 'sensitive_delivery_content'
    status = cli_json('status', task['task_id'])
    assert status['status'] != 'complete' and status['resources']['sim_spent_cents'] == before
    assert not list((cli_env / 'data' / 'mock' / 'exports' / task['task_id']).glob('campaign*'))


def test_real_request_with_configured_secret_never_reaches_local_server(cli_env, monkeypatch, model_script):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(True)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        key = 'wire-test-"secret"-\\value'
        monkeypatch.setenv('DEEPSEEK_API_KEY', key)
        monkeypatch.setenv('DEEPSEEK_BASE_URL', f'http://127.0.0.1:{server.server_port}')
        code, result, _ = run_cli('--mode', 'real', 'create', '--topic', '主题', '--budget', '10', '--deadline', '2026-10-30', '--requirement', key)
        assert code == 0
        task_id = json.loads(result)['task_id']
        code, result, _ = run_cli('--mode', 'real', 'run', task_id)
        assert code == 0 and json.loads(result)['run']['stop_reason'] == 'sensitive_model_input'
        code, result, _ = run_cli('--mode', 'real', 'inspect', task_id)
        task = json.loads(result)
        assert code == 0 and not task['llm_requests'] and not task['attempts']
        assert task['progress']['used_rounds'] == 0 and task['resources']['sim_spent_cents'] == 0
        assert calls == []
        # Scripted model is a separate local seam, and does not require a real key.
        monkeypatch.setenv('DEEPSEEK_API_KEY', '')
        model_script([{'name': 'stop_incomplete', 'arguments': {'reason': '停止'}}])
        code, result, _ = run_cli('--mode', 'mock', 'create', '--topic', '主题', '--budget', '10', '--deadline', '2026-10-30', '--requirement', key)
        task_id = json.loads(result)['task_id']
        code, result, _ = run_cli('--mode', 'mock', 'run', task_id)
        assert code == 0 and json.loads(result)['progress']['used_rounds'] == 1
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_review_rejects_sensitive_input_before_charge_or_network(cli_env, monkeypatch, model_script):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from test_brief_loop import BRIEF
    from test_campaign_delivery_new import _campaign
    from test_direct_script import SCRIPT

    from cta.model import ScriptedClient

    calls = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(True)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        key = 'review-test-"secret"-\\value'
        monkeypatch.setenv('DEEPSEEK_API_KEY', key)
        monkeypatch.setenv('DEEPSEEK_BASE_URL', f'http://127.0.0.1:{server.server_port}')
        code, result, _ = run_cli('--mode', 'real', 'create', '--topic', '主题', '--budget', '10', '--deadline', '2026-10-30')
        task_id = json.loads(result)['task_id']
        prefix = [{'name': 'propose_brief', 'arguments': BRIEF},
                  {'name': 'submit_script', 'arguments': {'brief_version': 1, 'script': SCRIPT}}]
        script_path = model_script(prefix)
        monkeypatch.setattr('cta.loop.create_client', lambda config: ScriptedClient(script_path))
        run_cli('--mode', 'real', 'run', task_id, '--max-rounds', '2')
        _, result, _ = run_cli('--mode', 'real', 'inspect', task_id)
        script = json.loads(result)['scripts'][0]
        campaign = _campaign()
        campaign['activity']['theme'] = '主题 ' + key
        model_script(prefix + [{'name': 'submit_campaign', 'arguments': {
            'script_ref': {'id': script['output_id'], 'version': script['version']}, 'campaign': campaign}}])
        run_cli('--mode', 'real', 'resume', task_id, '--max-rounds', '1')
        _, result, _ = run_cli('--mode', 'real', 'status', task_id)
        spent = json.loads(result)['resources']['sim_spent_cents']
        code, result, _ = run_cli('--mode', 'real', 'review', task_id)
        assert code == 4 and json.loads(result)['error'] == 'sensitive_model_input'
        _, result, _ = run_cli('--mode', 'real', 'inspect', task_id)
        view = json.loads(result)
        assert view['resources']['sim_spent_cents'] == spent
        assert not [item for item in view['attempts'] if item['kind'] == 'review_campaign']
        assert not [item for item in view['llm_requests'] if item['purpose'] == 'review']
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
