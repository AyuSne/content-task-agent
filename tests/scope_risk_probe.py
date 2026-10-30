"""Explicit, non-default probe documenting an unresolved semantic scope risk."""

import copy

from conftest import cli_json
from test_brief_loop import BRIEF
from test_campaign_delivery_new import _campaign, _result
from test_campaign_draft import _create, _tool
from test_direct_script import SCRIPT
from test_review_save import REVIEW_PASS, _review_item


def test_scope_probe(cli_env, model_script):
    task = _create()
    script = copy.deepcopy(SCRIPT)
    script['evidence'][0]['scope'] = '所有 iOS 和 Android 版本，包括 4.2 以下'
    brief = copy.deepcopy(BRIEF)
    brief['product_facts'][0]['scope'] = script['evidence'][0]['scope']
    prefix = [_tool('propose_brief', brief), _tool('submit_script', {'brief_version': 1, 'script': script})]
    model_script(prefix)
    cli_json('run', task['task_id'], '--max-rounds', '2')
    ref = _result(cli_json('inspect', task['task_id']), 'submit_script')[-1]['script_ref']
    campaign = _campaign()
    campaign['evidence'][0]['scope'] = script['evidence'][0]['scope']
    model_script(prefix + [_tool('submit_campaign', {'script_ref': ref, 'campaign': campaign}), _review_item(REVIEW_PASS)])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    assert cli_json('review', task['task_id'])['review']['verdict'] == 'pass'
    assert cli_json('save', task['task_id'])['status'] == 'complete'
