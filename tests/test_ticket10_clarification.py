import json

from conftest import cli_json
from test_campaign_draft import _tool


def test_multiple_questions_are_returned_together_and_pause_the_task(cli_env, model_script):
    task = cli_json('create', '--topic', '集中澄清', '--budget', '10', '--deadline', '2026-10-30')
    questions = [
        {'question': '活动目标是拉新还是促活？', 'impact': '决定内容重点', 'blocking': True},
        {'question': '目标受众是学生还是家长？', 'impact': '决定表达方式', 'blocking': True},
    ]
    model_script([_tool('request_clarification', {'questions': questions})])

    result = cli_json('run', task['task_id'])
    view = cli_json('inspect', task['task_id'])
    receipt = next(json.loads(message['content']) for message in view['messages']
                   if message.get('role') == 'tool' and message.get('name') == 'request_clarification')
    assert result['status'] == 'needs_input'
    assert result['progress']['phase'] == 'awaiting_clarification'
    assert '2 个问题' in result['reason'] and '关键阻塞 2' in result['reason']
    assert receipt['ok'] is True and receipt['questions'] == questions
    assert view['clarifications'][0]['questions'] == questions
    assert not view['attempts'] and view['resources']['sim_spent_cents'] == 0
