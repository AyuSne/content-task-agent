import json

from conftest import cli_json
from test_campaign_delivery_new import _campaign, _result, _script_ready
from test_campaign_draft import _tool
from test_review_save import _review_item
from test_revision_disposition import REVIEW_FAIL


def _reviewed(model_script, report=REVIEW_FAIL):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix + [_review_item(report)])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    campaign_ref = _result(view, 'submit_campaign')[0]['campaign_ref']
    reviewed = cli_json('review', task['task_id'])
    return task['task_id'], prefix, script_ref, campaign_ref, reviewed['review']['review_id']


def test_rejection_requires_locatable_evidence_and_preserves_atomic_batch(cli_env, model_script):
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    common = {'review_id': review_id, 'campaign_ref': campaign_ref}
    decision = {'finding_id': 'F1', 'decision': 'rejected', 'reason': '需求已经支持'}
    model_script(prefix + [_tool('record_dispositions', {**common, 'decisions': [decision]})])
    cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    result = _result(view, 'record_dispositions')[0]
    assert result['ok'] is False and 'decisions[0]' in result['message']
    assert view['dispositions'] == []


def test_valid_requirement_rejection_is_visible_and_can_save_without_new_review(cli_env, model_script):
    from pathlib import Path

    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    evidence = {'kind': 'requirement', 'input_version': 1, 'field_path': 'requirements[0]', 'quote': '突出自动卡点'}
    decision = {'finding_id': 'F1', 'decision': 'rejected', 'reason': '原要求已支持该范围', 'evidence': [evidence]}
    model_script(prefix + [
        _tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref, 'decisions': [decision]}),
        _tool('save_campaign', {'campaign_ref': campaign_ref}),
    ])
    result = cli_json('resume', task_id, '--max-rounds', '2')
    view = cli_json('inspect', task_id)
    assert result['status'] == 'complete'
    assert len(view['reviews']) == 1
    assert view['dispositions'][0]['review_id'] == review_id
    assert view['dispositions'][0]['campaign_ref'] == campaign_ref
    assert view['dispositions'][0]['evidence'] == [evidence]
    save = _result(view, 'save_campaign')[0]['save']
    report = next(item['path'] for item in save['files'] if item['path'].endswith('execution-report.md'))
    content = Path(report).read_text(encoding='utf-8')
    assert 'F1' in content and '原要求已支持该范围' in content and '突出自动卡点' in content
    assert '语义' in content


def test_invalid_second_decision_preserves_previous_disposition_and_material_basis(cli_env, model_script):
    from copy import deepcopy

    report = deepcopy(REVIEW_FAIL)
    report['findings'].append({**report['findings'][0], 'finding_id': 'F2', 'severity': 'optional'})
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script, report)
    material = {'kind': 'material', 'material_id': 'M-001', 'material_version': '2026-10-29.v1',
                'quote': '把用户选中的视频片段对齐到节拍点', 'scope': 'iOS/Android 4.2 及以上'}
    accepted = {'finding_id': 'F1', 'decision': 'rejected', 'reason': '材料原文支持', 'evidence': [material]}
    target = {'review_id': review_id, 'campaign_ref': campaign_ref}
    first = _tool('record_dispositions', {**target, 'decisions': [accepted]})
    bad = {'finding_id': 'F2', 'decision': 'rejected', 'reason': '引用错误', 'evidence': [
        {'kind': 'requirement', 'input_version': 1, 'field_path': 'requirements[8]', 'quote': '不存在'}]}
    second = _tool('record_dispositions', {**target, 'decisions': [
        {'finding_id': 'F1', 'decision': 'accepted', 'reason': '本不应写入'}, bad]})
    model_script(prefix + [first, second])
    cli_json('resume', task_id, '--max-rounds', '2')
    view = cli_json('inspect', task_id)
    results = _result(view, 'record_dispositions')
    assert results[0]['ok'] is True
    assert results[1]['ok'] is False and 'decisions[1].evidence[0]' in results[1]['message']
    assert len(view['dispositions']) == 1
    assert view['dispositions'][0]['decision'] == 'rejected'
    assert view['dispositions'][0]['reason'] == '材料原文支持'
    assert view['dispositions'][0]['evidence'] == [material]


def test_accepted_finding_blocks_save_until_new_complete_script_campaign_and_review(cli_env, model_script):
    from copy import deepcopy
    from test_direct_script import SCRIPT
    from test_revision_disposition import REVIEW_PASS_V2

    task_id, prefix, script_ref, campaign_ref, review_id = _reviewed(model_script)
    accepted = {'finding_id': 'F1', 'decision': 'accepted', 'reason': '确实需要修正'}
    decision = _tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref,
                                              'decisions': [accepted]})
    model_script(prefix + [decision, _tool('save_campaign', {'campaign_ref': campaign_ref})])
    cli_json('resume', task_id, '--max-rounds', '2')
    view = cli_json('inspect', task_id)
    assert _result(view, 'save_campaign')[0]['error'] == 'unresolved_must_fix'
    assert not any(a['kind'] == 'save_campaign' for a in view['attempts'])
    revised = deepcopy(SCRIPT)
    revised['segments'][0]['voiceover'] = '选择视频片段，按节拍对齐。'
    prefix += [decision, _tool('save_campaign', {'campaign_ref': campaign_ref}),
               _tool('submit_script', {'brief_version': 1, 'script': revised, 'revises_script_ref': script_ref})]
    model_script(prefix)
    cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    new_script = view['scripts'][-1]
    new_ref = {'id': new_script['output_id'], 'version': new_script['version']}
    prefix += [_tool('submit_campaign', {'script_ref': new_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    new_campaign = _result(view, 'submit_campaign')[-1]['campaign_ref']
    assert new_campaign != campaign_ref
    assert [o for o in view['outputs'] if o['kind'] == 'campaign'][0]['content']['script'] == SCRIPT
    assert view['dispositions'][0]['review_id'] == review_id
    model_script(prefix + [_review_item(REVIEW_FAIL), _review_item(REVIEW_PASS_V2)])
    review = cli_json('review', task_id)
    assert review['review']['output_id'] == new_campaign['id']
    model_script(prefix + [_tool('save_campaign', {'campaign_ref': new_campaign})])
    result = cli_json('resume', task_id, '--max-rounds', '1')
    assert result['status'] == 'complete'
    view = cli_json('inspect', task_id)
    assert len(view['reviews']) == 2 and view['saves'][-1]['output_version'] == new_campaign['version']
    assert view['scripts'][0]['content']['script'] == SCRIPT
    assert view['scripts'][1]['content']['script'] == revised
    from pathlib import Path
    saved = _result(view, 'save_campaign')[-1]['save']
    report_path = next(f['path'] for f in saved['files'] if f['path'].endswith('execution-report.md'))
    report_text = Path(report_path).read_text(encoding='utf-8')
    assert REVIEW_FAIL['findings'][0]['issue'] in report_text and REVIEW_PASS_V2['summary'] in report_text
    assert '确实需要修正' in report_text


def test_optional_finding_does_not_require_rewrite_or_disposition(cli_env, model_script):
    from copy import deepcopy
    from test_review_save import REVIEW_PASS

    report = deepcopy(REVIEW_PASS)
    report['findings'] = [{**REVIEW_FAIL['findings'][0], 'severity': 'optional'}]
    task_id, prefix, _, campaign_ref, _ = _reviewed(model_script, report)
    model_script(prefix + [_tool('save_campaign', {'campaign_ref': campaign_ref})])
    result = cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    assert result['status'] == 'complete'
    assert len(view['reviews']) == 1 and len(view['scripts']) == 1
    assert view['dispositions'] == []


def test_stale_review_and_false_reference_are_rejected(cli_env, model_script):
    task_id, prefix, script_ref, campaign_ref, review_id = _reviewed(model_script)
    old_decision = _tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref,
        'decisions': [{'finding_id': 'F1', 'decision': 'accepted', 'reason': '需要修正'}]})
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()}), old_decision]
    model_script(prefix)
    cli_json('resume', task_id, '--max-rounds', '2')
    view = cli_json('inspect', task_id)
    assert _result(view, 'record_dispositions')[0]['error'] == 'invalid_campaign_ref'
    assert view['dispositions'] == []


def test_invalid_material_version_scope_and_requirement_quote_are_rejected(cli_env, model_script):
    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    common = {'review_id': review_id, 'campaign_ref': campaign_ref}
    material = {'kind': 'material', 'material_id': 'M-001', 'material_version': '2026-10-29.v1',
                'quote': '把用户选中的视频片段对齐到节拍点', 'scope': 'iOS/Android 4.2 及以上'}
    cases = [{**material, 'material_version': 'wrong'}, {**material, 'scope': '不存在的范围'},
             {'kind': 'requirement', 'input_version': 1, 'field_path': 'requirements[0]', 'quote': '不存在'},
             {'kind': 'requirement', 'input_version': 2, 'field_path': 'requirements[0]', 'quote': '突出自动卡点'}]
    calls = [_tool('record_dispositions', {**common, 'decisions': [
        {'finding_id': 'F1', 'decision': 'rejected', 'reason': '说明', 'evidence': [e]}]}) for e in cases]
    model_script(prefix + calls)
    cli_json('resume', task_id, '--max-rounds', '4')
    view = cli_json('inspect', task_id)
    assert all(not result['ok'] and 'decisions[0].evidence[0]' in result['message']
               for result in _result(view, 'record_dispositions'))
    assert len(_result(view, 'record_dispositions')) == 4 and view['dispositions'] == []


import pytest


@pytest.mark.parametrize('point,committed', [('after_tool_request', False), ('before_local_tool_receipt', False),
                                             ('after_tool_effect', True)])
def test_disposition_recovery_uses_exact_receipt_without_replay(cli_env, model_script, point, committed):
    from conftest import run_cli_process

    task_id, prefix, _, campaign_ref, review_id = _reviewed(model_script)
    evidence = {'kind': 'requirement', 'input_version': 1, 'field_path': 'requirements[0]', 'quote': '突出自动卡点'}
    decision = {'finding_id': 'F1', 'decision': 'rejected', 'reason': '按要求处理', 'evidence': [evidence]}
    path = model_script(prefix + [_tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref,
                                                                 'decisions': [decision]})])
    crashed = run_cli_process('resume', task_id, '--max-rounds', '1', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path), 'CTA_FAULT_CRASH': point})
    assert crashed.returncode == 70
    before = cli_json('inspect', task_id)
    resumed = run_cli_process('resume', task_id, '--max-rounds', '0', data_dir=cli_env / 'data',
                              extra_env={'CTA_MODEL_SCRIPT': str(path)})
    assert resumed.returncode == 0, resumed.stderr
    after = cli_json('inspect', task_id)
    receipt = _result(after, 'record_dispositions')[-1]
    assert receipt['recovered'] is True and receipt['ok'] == committed
    assert len(after['dispositions']) == (1 if committed else 0)
    assert after['dispositions'] == before['dispositions']
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    assert after['progress']['used_rounds'] == before['progress']['used_rounds']
    calls = [call['id'] for m in after['messages'] if m['role'] == 'assistant' for call in m['tool_calls']]
    results = [m['tool_call_id'] for m in after['messages'] if m['role'] == 'tool']
    assert calls == results


def test_original_requirement_quote_can_locate_raw_deadline(cli_env, model_script):
    task, prefix, script_ref = _script_ready(model_script)
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix + [_review_item(REVIEW_FAIL)])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    view = cli_json('inspect', task['task_id'])
    campaign_ref = _result(view, 'submit_campaign')[0]['campaign_ref']
    review_id = cli_json('review', task['task_id'])['review']['review_id']
    raw = view['input_versions'][0]['raw']
    view = cli_json('inspect', task['task_id'])
    main_input = next(m['content'] for m in view['messages'] if '[任务输入]' in (m['content'] or ''))
    review_input = next(m['content'] for m in view['reviewer_messages'] if '[待核验方案]' in (m['content'] or ''))
    assert raw['deadline'] in main_input and raw['deadline'] in review_input
    evidence = {'kind': 'requirement', 'input_version': 1, 'field_path': 'deadline', 'quote': raw['deadline']}
    model_script(prefix + [_tool('record_dispositions', {'review_id': review_id, 'campaign_ref': campaign_ref,
        'decisions': [{'finding_id': 'F1', 'decision': 'rejected', 'reason': '按原始截止取舍', 'evidence': [evidence]}]})])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    assert _result(cli_json('inspect', task['task_id']), 'record_dispositions')[0]['ok'] is True
