"""A model request interrupted in either real execution context keeps unknown usage."""

from conftest import cli_json, run_cli_process
from test_campaign_draft import _create, _tool
from test_campaign_delivery_new import _campaign, _result, _script_ready
from test_review_save import REVIEW_PASS, _review_item


def _assert_request_is_retained(task_id, request_id, used):
    after = cli_json('inspect', task_id)
    request = next(item for item in after['llm_requests'] if item['id'] == request_id)
    assert request['status'] == 'interrupted'
    assert request['prompt_tokens'] is None and request['response_model'] is None
    assert '未知' in request['error']
    assert after['progress']['used_rounds'] >= used
    assert after['resources']['real_model_usage']['unknown_usage_requests'] >= 1


def test_process_death_during_main_request_preserves_unknown_usage(cli_env, model_script):
    script = model_script([{'type': 'crash'}, _tool('stop_incomplete', {'reason': '保留中断请求'})])
    task_id = _create()['task_id']
    crashed = run_cli_process('run', task_id, data_dir=cli_env / 'data', extra_env={'CTA_MODEL_SCRIPT': str(script)})
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json('inspect', task_id)
    pending = next(item for item in before['llm_requests'] if item['purpose'] == 'main_loop_round_1')
    assert pending['status'] == 'running'
    resumed = run_cli_process('resume', task_id, '--max-rounds', '2', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script)})
    assert resumed.returncode == 0, resumed.stderr
    _assert_request_is_retained(task_id, pending['id'], before['progress']['used_rounds'])


def test_process_death_during_review_request_preserves_attempt_and_unknown_usage(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_campaign')[0]['campaign_ref']
    script = model_script(prefix + [_tool('review_campaign', {'campaign_ref': ref}),
                                    {'type': 'crash', 'channel': 'review'},
                                    _tool('stop_incomplete', {'reason': '保留核验中断'})])
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script)})
    assert crashed.returncode == 70, crashed.stderr
    before = cli_json('inspect', task['task_id'])
    pending = next(item for item in before['llm_requests'] if item['purpose'] == 'review')
    assert pending['status'] == 'running'
    resumed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(script)})
    assert resumed.returncode == 0, resumed.stderr
    _assert_request_is_retained(task['task_id'], pending['id'], before['progress']['used_rounds'])
    after = cli_json('inspect', task['task_id'])
    result = _result(after, 'review_campaign')[-1]
    assert not result['ok'] and result['error'] == 'review_interrupted'
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
