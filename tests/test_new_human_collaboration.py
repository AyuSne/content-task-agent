import json
from pathlib import Path

from conftest import cli_json, run_cli, run_cli_process
from test_campaign_delivery_new import _new_draft_ready
from test_review_save import REVIEW_PASS, _review_item


def test_import_edited_new_campaign_preserves_script_and_requires_new_review(cli_env, model_script):
    task = _new_draft_ready(model_script, [_review_item(REVIEW_PASS), _review_item(REVIEW_PASS)])
    task_id = task['task_id']
    cli_json('review', task_id)
    cli_json('save', task_id)
    before = cli_json('inspect', task_id)
    saved = before['save']
    path = next(Path(item['path']) for item in saved['files'] if item['path'].endswith('campaign-v1.md'))
    original = path.read_text(encoding='utf-8')
    edited = original.replace('演示操作并联系校园', '以校园社团活动呈现操作流程')
    assert edited != original
    path.write_text(edited, encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 0, out
    result = json.loads(out)
    assert result['import']['output_version'] == 2
    after = cli_json('inspect', task_id)
    campaigns = [o for o in after['outputs'] if o['kind'] == 'campaign']
    assert len(campaigns) == 2 and campaigns[-1]['status'] == 'pending_review'
    assert campaigns[-1]['content']['strategy']['rationale'] == '以校园社团活动呈现操作流程'
    assert campaigns[-1]['content']['script'] == campaigns[0]['content']['script']
    assert campaigns[-1]['content']['script_ref'] == campaigns[0]['content']['script_ref']
    from copy import deepcopy
    expected = deepcopy(campaigns[0]['content'])
    expected['strategy']['rationale'] = '以校园社团活动呈现操作流程'
    assert campaigns[-1]['content'] == expected
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    assert path.read_text(encoding='utf-8') == edited
    reviewed = cli_json('review', task_id)
    assert reviewed['review']['output_version'] == 2
    complete = cli_json('save', task_id)
    assert complete['status'] == 'complete' and complete['save']['output_version'] == 2


def test_import_script_edit_creates_unbilled_version_and_injects_complete_snapshot(cli_env, model_script):
    from test_campaign_delivery_new import _campaign, _script_ready
    from test_campaign_draft import _tool
    task, prefix, script_ref = _script_ready(model_script)
    task_id = task['task_id']
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task_id, '--max-rounds', '1')
    before = cli_json('inspect', task_id)
    export = cli_json('export', task_id)
    path = next(Path(item['path']) for item in export['files'] if item['path'].endswith('campaign-v1.md'))
    text = path.read_text(encoding='utf-8')
    edited = text.replace('让片段对齐节拍', '选择片段后再对齐节拍')
    assert edited != text
    path.write_text(edited, encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 0, out
    after = cli_json('inspect', task_id)
    assert len(after['scripts']) == 2
    old, new = after['scripts']
    campaigns = [o for o in after['outputs'] if o['kind'] == 'campaign']
    assert old['content']['script']['segments'][0]['voiceover'] == '让片段对齐节拍'
    assert new['content']['script']['segments'][0]['voiceover'] == '选择片段后再对齐节拍'
    assert new['content']['source'] == 'human_import' and new['content']['revises_script_ref'] == script_ref
    assert campaigns[-1]['content']['script_ref'] == {'id': new['output_id'], 'version': new['version']}
    assert campaigns[-1]['content']['script'] == new['content']['script']
    assert len(after['attempts']) == len(before['attempts'])
    assert after['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    model_script(prefix + [_tool('stop_incomplete', {'reason': '人工稿检查'})])
    resumed = run_cli_process('resume', task_id, '--max-rounds', '1', data_dir=cli_env / 'data')
    assert resumed.returncode == 0, resumed.stderr
    view = cli_json('inspect', task_id)
    imported = [m for m in view['messages'] if '[人工稿导入]' in (m['content'] or '')]
    assert len(imported) == 1 and '选择片段后再对齐节拍' in imported[0]['content']
    payload = json.loads(imported[0]['content'].split('] ', 1)[1])
    assert payload['campaign'] == campaigns[-1]['content']
    assert not any('[待核验人工稿]' in (m['content'] or '') for m in view['messages'])
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 4 and json.loads(out)['error'] == 'stale_base'


def test_resource_updates_reuse_script_and_brief_but_semantic_change_rejects_them(cli_env, model_script):
    from copy import deepcopy

    from test_campaign_delivery_new import _campaign, _result, _script_ready
    from test_campaign_draft import _tool
    from test_direct_script import SCRIPT
    task, prefix, ref = _script_ready(model_script)
    task_id = task['task_id']
    before = cli_json('inspect', task_id)
    cli_json('update', task_id, '--budget', '20')
    cli_json('update', task_id, '--budget', '21')
    model_script(prefix + [_tool('submit_campaign', {'script_ref': ref, 'campaign': _campaign()})])
    cli_json('resume', task_id, '--max-rounds', '1')
    view = cli_json('inspect', task_id)
    assert _result(view, 'submit_campaign')[-1]['ok'] is True
    assert view['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']
    revised = deepcopy(SCRIPT)
    revised['segments'][0]['voiceover'] = '资源更新后修订表达'
    script_call = _tool('submit_script', {'brief_version': 1, 'script': revised, 'revises_script_ref': ref})
    model_script(prefix + [_tool('submit_campaign', {'script_ref': ref, 'campaign': _campaign()}), script_call])
    cli_json('resume', task_id, '--max-rounds', '1')
    assert _result(cli_json('inspect', task_id), 'submit_script')[-1]['ok'] is True
    cli_json('update', task_id, '--audience', '研究生')
    model_script(prefix + [_tool('submit_campaign', {'script_ref': ref, 'campaign': _campaign()}), script_call,
                           _tool('submit_campaign', {'script_ref': ref, 'campaign': _campaign()})])
    cli_json('resume', task_id, '--max-rounds', '1')
    result = _result(cli_json('inspect', task_id), 'submit_campaign')[-1]
    assert result['ok'] is False and result['error'] == 'invalid_campaign'


def test_saved_resource_update_import_and_new_review_keep_old_evidence(cli_env, model_script):
    task = _new_draft_ready(model_script, [_review_item(REVIEW_PASS), _review_item(REVIEW_PASS)])
    task_id = task['task_id']
    cli_json('review', task_id)
    cli_json('save', task_id)
    old = cli_json('inspect', task_id)
    updated = cli_json('update', task_id, '--budget', '20')
    assert updated['change']['kind'] == 'resource'
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    edit_path = cli_env / 'resource-edit.md'
    edit_path.write_text(path.read_text(encoding='utf-8').replace('演示操作并联系校园', '校园场景下的具体教程'), encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(edit_path))
    assert code == 0, out
    new = cli_json('inspect', task_id)
    campaigns = [o for o in new['outputs'] if o['kind'] == 'campaign']
    assert campaigns[-1]['input_version'] == 2 and campaigns[-1]['status'] == 'pending_review'
    assert new['imports'][-1]['base_output_id'] == campaigns[0]['output_id']
    assert new['resources']['sim_spent_cents'] == old['resources']['sim_spent_cents']
    code, out, _ = run_cli('save', task_id)
    assert code != 0 and json.loads(out)['save_error']['code'] == 'review_stale'
    reviewed = cli_json('review', task_id)
    assert reviewed['review']['output_version'] == 2
    final = cli_json('save', task_id)
    assert final['save']['verified'] and final['save']['output_version'] == 2



def test_invalid_edited_campaign_rolls_back_script_and_preserves_original(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    text = path.read_text(encoding='utf-8')
    text = text.replace('让片段对齐节拍', '更换一条旁白')
    head, tail = text.split('## 证据关联', 1)
    text = head + '## 证据关联' + tail.replace('（版本 2026-10-29.v1）', '（版本 不存在）')
    path.write_text(text, encoding='utf-8')
    before = cli_json('inspect', task_id)
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 4 and json.loads(out)['error'] == 'invalid_campaign'
    after = cli_json('inspect', task_id)
    assert after['scripts'] == before['scripts'] and after['outputs'] == before['outputs']
    assert after['attempts'] == before['attempts'] and after['imports'] == []
    assert path.read_text(encoding='utf-8') == text


def test_input_update_preserves_usage_and_raw_context_and_semantic_old_export_is_stale(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    original = cli_json('inspect', task_id)
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    updated = cli_json('update', task_id, '--audience', '研究生', '--requirement', '新增要求原文')
    assert updated['change']['kind'] == 'semantic'
    assert updated['progress']['used_rounds'] == original['progress']['used_rounds']
    assert updated['progress']['max_rounds'] == original['progress']['max_rounds']
    assert updated['progress']['sim_elapsed_ms'] == original['progress']['sim_elapsed_ms']
    assert updated['resources']['sim_spent_cents'] == original['resources']['sim_spent_cents']
    view = cli_json('inspect', task_id)
    update = next(m for m in reversed(view['messages']) if '[输入更新]' in (m['content'] or ''))
    payload = json.loads(update['content'].split('] ', 1)[1])
    assert payload['input_version'] == 2 and payload['raw_input']['requirements'] == ['新增要求原文']
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 4 and json.loads(out)['error'] == 'stale_input'
    assert len(cli_json('inspect', task_id)['outputs']) == len(original['outputs'])


def test_unimported_edit_is_preserved_until_explicit_repair(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    cli_json('review', task_id)
    saved = cli_json('save', task_id)
    path = next(Path(item['path']) for item in saved['save']['files'] if item['path'].endswith('campaign-v1.md'))
    original = path.read_text(encoding='utf-8')
    edited = original.replace('演示操作并联系校园', '未导入的外部修改')
    path.write_text(edited, encoding='utf-8')
    before = cli_json('inspect', task_id)
    for _ in range(2):
        report = cli_json('export', task_id)
        assert not report['verified'] and not report['delivery_complete']
        assert path.read_text(encoding='utf-8') == edited
    state = cli_json('inspect', task_id)
    assert state['status'] != 'complete' and state['save']['status'] == 'unknown'
    assert next(o for o in state['outputs'] if o['kind'] == 'campaign')['content']['strategy']['rationale'] == '演示操作并联系校园'
    repaired = cli_json('export', task_id, '--repair')
    assert repaired['verified'] and repaired['delivery_complete']
    assert path.read_text(encoding='utf-8') == original
    final = cli_json('inspect', task_id)
    assert len(final['attempts']) == len(before['attempts'])
    assert final['resources']['sim_spent_cents'] == before['resources']['sim_spent_cents']



def test_import_rejects_foreign_missing_or_ambiguous_input_without_new_versions(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    original = path.read_text(encoding='utf-8')
    for edit, expected in [
        (original.rsplit('<!-- cta:', 1)[0], 'missing_metadata'),
        (original.replace('## 素材用途', '## 删除素材用途'), 'missing_section'),
        (original.replace('## 创作策略', '## 创作策略\n\n- 选定：伪造额外方向'), 'invalid_campaign'),
        (original.replace(task_id, 'foreign-task'), 'wrong_task'),
        (original.replace('- 受众：18-24 岁大学生，重点是社团招新负责人', '- 受众：高中生'), 'semantic_change_requires_update'),
    ]:
        assert edit != original
        path.write_text(edit, encoding='utf-8')
        code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
        assert code == 4 and json.loads(out)['error'] == expected
        view = cli_json('inspect', task_id)
        assert len([o for o in view['outputs'] if o['kind'] == 'campaign']) == 1
        assert len(view['scripts']) == 1 and view['imports'] == []


def test_import_round_trips_multiline_and_delimiter_characters(cli_env, model_script):
    from copy import deepcopy

    from test_brief_loop import BRIEF
    from test_campaign_delivery_new import _campaign
    from test_campaign_draft import _create, _tool
    from test_direct_script import SCRIPT
    task = _create()
    script = deepcopy(SCRIPT)
    script['title'] = '校园：节奏；剪辑\n第二行'
    script['segments'][0]['visual'] = '第一步｜选择；第二步：对齐\n随后展示结果'
    prefix = [_tool('propose_brief', BRIEF), _tool('submit_script', {'brief_version': 1, 'script': script})]
    model_script(prefix)
    cli_json('run', task['task_id'], '--max-rounds', '2')
    ref = cli_json('inspect', task['task_id'])['scripts'][0]
    campaign = _campaign()
    campaign['strategy']['rationale'] = '先展示步骤；再说明原因\n保留操作细节'
    model_script(prefix + [_tool('submit_campaign', {'script_ref': {'id': ref['output_id'], 'version': ref['version']}, 'campaign': campaign})])
    cli_json('resume', task['task_id'], '--max-rounds', '1')
    exported = cli_json('export', task['task_id'])
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    code, out, _ = run_cli('import-draft', task['task_id'], '--file', str(path))
    assert code == 0, out
    result = [o for o in cli_json('inspect', task['task_id'])['outputs'] if o['kind'] == 'campaign'][-1]['content']
    assert result['script'] == script
    assert result['strategy']['rationale'] == campaign['strategy']['rationale']


def test_import_rejects_unstructured_extra_text_without_discarding_it(cli_env, model_script):
    task = _new_draft_ready(model_script, _review_item(REVIEW_PASS))
    task_id = task['task_id']
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    source = path.read_text(encoding='utf-8').replace('## 活动概述', '额外的人工要求不能丢失。\n\n## 活动概述')
    path.write_text(source, encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 4 and json.loads(out)['error'] == 'invalid_campaign'
    assert path.read_text(encoding='utf-8') == source
    assert len([o for o in cli_json('inspect', task_id)['outputs'] if o['kind'] == 'campaign']) == 1


def test_agent_continues_after_resource_update_and_import_to_reviewed_saved_version(cli_env, model_script):
    from test_campaign_delivery_new import _campaign, _result, _script_ready
    from test_campaign_draft import _tool

    task, prefix, script_ref = _script_ready(model_script)
    task_id = task['task_id']
    prefix += [_tool('submit_campaign', {'script_ref': script_ref, 'campaign': _campaign()})]
    model_script(prefix)
    cli_json('resume', task_id, '--max-rounds', '1')
    cli_json('update', task_id, '--budget', '20')
    exported = cli_json('export', task_id)
    path = next(Path(item['path']) for item in exported['files'] if item['path'].endswith('campaign-v1.md'))
    path.write_text(path.read_text(encoding='utf-8').replace('演示操作并联系校园', '结合新资源进行校园教程'), encoding='utf-8')
    code, out, _ = run_cli('import-draft', task_id, '--file', str(path))
    assert code == 0, out
    imported = json.loads(out)['import']
    ref = {'id': imported['output_id'], 'version': imported['output_version']}
    model_script(prefix + [_tool('review_campaign', {'campaign_ref': ref}), _review_item(REVIEW_PASS),
                           _tool('save_campaign', {'campaign_ref': ref})])
    result = cli_json('resume', task_id, '--max-rounds', '2')
    assert result['status'] == 'complete' and result['save']['verified']
    view = cli_json('inspect', task_id)
    assert _result(view, 'review_campaign')[-1]['review']['output_id'] == ref['id']
    assert _result(view, 'save_campaign')[-1]['save']['output_id'] == ref['id']
    messages = [m['content'] or '' for m in view['messages']]
    assert any('结合新资源进行校园教程' in message and '[人工稿导入]' in message for message in messages)
    assert any('[输入更新]' in message and '"input_version": 2' in message for message in messages)
