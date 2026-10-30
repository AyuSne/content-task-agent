import json

from conftest import cli_json, run_cli_process


def test_invalid_json_returns_position_in_next_request_without_executing(cli_env, model_script):
    task = cli_json('create', '--topic', '参数纠错', '--budget', '10', '--deadline', '2026-10-30')
    model_script([
        {'name': 'submit_campaign', 'arguments': '{"script_ref": {}} trailing'},
        {'name': 'stop_incomplete', 'arguments': {'reason': '检查反馈'}},
    ])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    receipt = next(message for message in view['messages']
                   if message.get('role') == 'tool' and message.get('name') == 'submit_campaign')
    error = json.loads(receipt['content'])
    assert error['error'] == 'invalid_arguments'
    assert 'Extra data' in error['message'] and '位置 19' in error['message']
    request = json.loads(view['llm_requests'][1]['meta_json'])['request']
    assert any(message.get('role') == 'tool' and message.get('content') == receipt['content']
               for message in request['messages'])
    assert view['progress']['used_rounds'] == 2
    assert view['resources']['sim_spent_cents'] == 0
    assert not view['attempts']


def test_wrapped_campaign_arguments_are_rejected_with_expected_top_level_fields(cli_env, model_script):
    task = cli_json('create', '--topic', '参数包装', '--budget', '10', '--deadline', '2026-10-30')
    model_script([
        {'name': 'submit_campaign', 'arguments': {'arguments': '{"script_ref":'}},
        {'name': 'stop_incomplete', 'arguments': {'reason': '检查反馈'}},
    ])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    receipt = next(message for message in view['messages']
                   if message.get('role') == 'tool' and message.get('name') == 'submit_campaign')
    result = json.loads(receipt['content'])
    assert result['error'] == 'invalid_campaign'
    assert 'arguments' in str(result) and 'script_ref' in str(result) and 'campaign' in str(result)
    assert '不要' in str(result)
    assert not view['outputs'] and not view['attempts']
    assert view['resources']['sim_spent_cents'] == 0


def test_repeated_format_failures_across_resume_and_other_tool_trigger_guidance(cli_env, model_script):
    task = cli_json('create', '--topic', '重复错误', '--budget', '10', '--deadline', '2026-10-30')
    model_script([
        {'name': 'submit_campaign', 'arguments': '{"script_ref":'},
        {'name': 'read_skill', 'arguments': {'name': 'campaign-creation'}},
        {'name': 'submit_campaign', 'arguments': {'arguments': 'broken'}},
        {'name': 'submit_campaign', 'arguments': '{"campaign":'},
        {'name': 'stop_incomplete', 'arguments': {'reason': '检查提醒'}},
    ])
    cli_json('run', task['task_id'], '--max-rounds', '3')
    cli_json('resume', task['task_id'], '--max-rounds', '2')
    view = cli_json('inspect', task['task_id'])
    request = json.loads(view['llm_requests'][4]['meta_json'])['request']
    reminder = next(message['content'] for message in request['messages']
                    if (message.get('content') or '').startswith('[工具纠错提醒]'))
    assert 'submit_campaign' in reminder and '3 次' in reminder
    assert 'script_ref' in reminder and 'campaign' in reminder
    assert '剩余' in reminder and '不要' in reminder
    assert view['progress']['used_rounds'] == 5
    assert view['resources']['sim_spent_cents'] == 0
    assert len([message for message in view['messages'] if message.get('role') == 'tool']) == 5


def test_first_request_explains_tool_use_and_campaign_structure(cli_env, model_script):
    task = cli_json('create', '--topic', '工具前馈', '--budget', '10', '--deadline', '2026-10-30')
    model_script([{'name': 'stop_incomplete', 'arguments': {'reason': '检查输入'}}])
    cli_json('run', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    request = json.loads(view['llm_requests'][0]['meta_json'])['request']
    system = request['messages'][0]['content']
    for name in ('read_skill', 'search_materials', 'read_material', 'estimate_cost', 'update_plan',
                 'propose_brief', 'submit_script', 'submit_campaign', 'review_campaign',
                 'record_dispositions', 'save_campaign', 'request_clarification', 'stop_incomplete'):
        assert name in system
    assert '工具使用前馈' in system
    assert '不要包在 arguments 字符串' in system
    assert '每次调用后检查' in system
    assert len(request['tools']) == 13
    tool = next(item['function'] for item in request['tools'] if item['function']['name'] == 'submit_campaign')
    assert 'script_ref' in tool['description'] and 'arguments' in tool['description']
    assert set(tool['parameters']['required']) == {'script_ref', 'campaign'}
    example = next(line.strip() for line in system.splitlines() if line.strip().startswith('{"script_ref"'))
    parsed = json.loads(example)
    assert set(parsed) == {'script_ref', 'campaign'}
    assert set(parsed['campaign']) == set(tool['parameters']['properties']['campaign']['required'])


def test_interrupted_invalid_call_recovers_error_and_repeated_failure_reminder(cli_env, model_script):
    task = cli_json('create', '--topic', '错误恢复', '--budget', '10', '--deadline', '2026-10-30')
    path = model_script([
        {'name': 'submit_campaign', 'arguments': '{"script_ref":'},
        {'name': 'submit_campaign', 'arguments': {'arguments': 'broken'}},
        {'name': 'submit_campaign', 'arguments': '{"campaign":'},
    ])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    crashed = run_cli_process('resume', task['task_id'], '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': 'after_tool_request'})
    assert crashed.returncode == 70
    recovered = run_cli_process('resume', task['task_id'], '--max-rounds', '0', data_dir=cli_env / 'data',
                                extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert recovered.returncode == 0
    view = cli_json('inspect', task['task_id'])
    result = next(message for message in reversed(view['messages'])
                  if message.get('role') == 'tool' and message.get('name') == 'submit_campaign')
    assert '位置' in json.loads(result['content'])['message']
    assert any((message.get('content') or '').startswith('[工具纠错提醒]') and '3 次' in message['content']
               for message in view['messages'])
    assert view['progress']['used_rounds'] == 3 and view['resources']['sim_spent_cents'] == 0


def test_new_input_resets_the_repeated_error_count_without_resetting_rounds(cli_env, model_script):
    task = cli_json('create', '--topic', '更新后纠错', '--budget', '10', '--deadline', '2026-10-30')
    model_script([
        {'name': 'submit_campaign', 'arguments': '{"a":'},
        {'name': 'submit_campaign', 'arguments': {'arguments': 'bad'}},
        {'name': 'submit_campaign', 'arguments': '{"b":'},
        {'name': 'submit_campaign', 'arguments': {'arguments': 'bad again'}},
        {'name': 'stop_incomplete', 'arguments': {'reason': '结束'}},
    ])
    cli_json('run', task['task_id'], '--max-rounds', '2')
    cli_json('update', task['task_id'], '--budget', '20')
    cli_json('resume', task['task_id'], '--max-rounds', '3')
    view = cli_json('inspect', task['task_id'])
    assert not any((message.get('content') or '').startswith('[工具纠错提醒]') for message in view['messages'])
    assert view['progress']['used_rounds'] == 5
    assert view['input']['budget_cents'] == 2000


def test_repeated_errors_remain_bounded_by_original_round_limit(cli_env, model_script):
    task = cli_json('create', '--topic', '原有上限', '--budget', '10', '--deadline', '2026-10-30')
    model_script([{'name': 'submit_campaign', 'arguments': '{"broken":'} for _ in range(24)])
    result = cli_json('run', task['task_id'])
    view = cli_json('inspect', task['task_id'])
    reminders = [message['content'] for message in view['messages']
                 if (message.get('content') or '').startswith('[工具纠错提醒]')]
    assert len(reminders) == 3
    assert all(any(f'{count} 次' in reminder for reminder in reminders) for count in (3, 5, 8))
    assert result['status'] == 'incomplete'
    assert view['progress']['used_rounds'] == view['progress']['max_rounds'] == 24
    assert len(view['llm_requests']) == 24 and not view['attempts']
    assert view['resources']['sim_spent_cents'] == 0
    resumed = cli_json('resume', task['task_id'])
    assert resumed['progress']['used_rounds'] == 24


def test_repeated_successful_identical_tool_calls_receive_advisory_reminder(cli_env, model_script):
    task = cli_json('create', '--topic', '重复读取', '--budget', '10', '--deadline', '2026-10-30')
    call = {'name': 'read_skill', 'arguments': {'name': 'campaign-creation'}}
    model_script([call, call, call, {'name': 'stop_incomplete', 'arguments': {'reason': '检查提醒'}}])
    cli_json('run', task['task_id'], '--max-rounds', '4')
    view = cli_json('inspect', task['task_id'])
    request = json.loads(view['llm_requests'][3]['meta_json'])['request']
    assert any((message.get('content') or '').startswith('[工具重复提醒]')
               and 'read_skill' in message['content'] and '3 次' in message['content']
               for message in request['messages'])
    assert len([message for message in view['messages'] if message.get('name') == 'read_skill'
                and message.get('role') == 'tool']) == 3
    assert view['progress']['used_rounds'] == 4 and view['resources']['sim_spent_cents'] == 0


def test_reviewer_receives_explicit_scope_guidance_and_material_scope(cli_env, model_script):
    from test_campaign_delivery_new import _new_draft_ready
    from test_review_save import REVIEW_PASS, _review_item

    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    cli_json('review', task['task_id'])
    view = cli_json('inspect', task['task_id'])
    messages = view['reviewer_messages']
    instruction = next(message['content'] for message in messages if message['role'] == 'system')
    assert '逐条检查' in instruction and 'scope' in instruction
    assert '平台' in instruction and '能力限制' in instruction
    context = next(message['content'] for message in messages
                   if (message.get('content') or '').startswith('[待核验方案] '))
    payload = json.loads(context.split('] ', 1)[1])
    material = next(item for item in payload['evidence'] if item['material_id'] == 'M-001')
    assert 'iOS/Android 4.2' in material['scope']
    assert payload['campaign']['script']['evidence'][0]['scope']
