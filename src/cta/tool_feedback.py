"""Provide durable, advisory feedback for recurring tool errors."""

import json

from cta.repository import append_message


def parse_arguments(raw):
    try:
        parsed = json.loads(raw or '{}')
    except json.JSONDecodeError as exc:
        return {}, {'error': 'invalid_arguments', 'message':
                    f'工具参数不是合法 JSON：{exc.msg}，位置 {exc.pos}（从 0 开始）；本轮未执行任何业务动作。'}
    if not isinstance(parsed, dict):
        return {}, {'error': 'invalid_arguments', 'message': '工具参数必须是 JSON 对象。'}
    return parsed, None


def _category(name, result):
    if result.get('ok') is not False or not result.get('error'):
        return None
    error = result['error']
    if name == 'submit_campaign' and error in ('invalid_arguments', 'invalid_campaign'):
        return '方案参数或结构错误'
    return error


def append_repeated_call_reminder(conn, task_id, name, result):
    if _category(name, result) is not None:
        return
    rows = conn.execute(
        "SELECT role, tool_calls_json, meta_json FROM messages WHERE task_id = ? AND agent = 'main' "
        "AND role IN ('assistant', 'user') ORDER BY seq DESC", (task_id,)
    )
    first = None
    count = 0
    for row in rows:
        if row['role'] == 'user':
            if json.loads(row['meta_json']).get('kind') in ('input_update', 'material_adoption', 'knowledge_adoption'):
                break
            continue
        if not row['tool_calls_json']:
            continue
        calls = json.loads(row['tool_calls_json'])
        if len(calls) != 1:
            break
        function = calls[0]['function']
        try:
            arguments = json.loads(function['arguments'] or '{}')
        except json.JSONDecodeError:
            arguments = function['arguments']
        key = (function['name'], json.dumps(arguments, ensure_ascii=False, sort_keys=True))
        if first is None:
            first = key
        if key != first or function['name'] != name:
            break
        count += 1
    if count not in (3, 5, 8):
        return
    append_message(conn, task_id, 'user',
                   f'[工具重复提醒] {name} 已连续使用相同参数 {count} 次；请检查已有结果，'
                   '不要无进展地重复调用，可选择有依据的其他行动或完成任务。',
                   meta={'kind': 'repeated_tool_reminder', 'tool': name, 'count': count})


def append_repeated_error_reminder(conn, task_id, name, result):
    category = _category(name, result)
    if category is None:
        return
    count = 0
    rows = conn.execute(
        "SELECT role, name, content, meta_json FROM messages WHERE task_id = ? AND agent = 'main' "
        "AND role IN ('tool', 'user') ORDER BY seq DESC", (task_id,)
    )
    for row in rows:
        if row['role'] == 'user':
            if json.loads(row['meta_json']).get('kind') in ('input_update', 'material_adoption', 'knowledge_adoption'):
                break
            continue
        if row['name'] != name:
            continue
        previous = json.loads(row['content'])
        if _category(name, previous) != category:
            break
        count += 1
    if count not in (3, 5, 8):
        return
    task = conn.execute('SELECT max_rounds, used_rounds FROM tasks WHERE id = ?', (task_id,)).fetchone()
    remaining = task['max_rounds'] - task['used_rounds']
    guidance = '请检查最近工具结果，修正具体原因或选择符合任务条件的其他行动；不要重复无效调用。'
    if name == 'submit_campaign' and category == '方案参数或结构错误':
        guidance += ' submit_campaign 参数顶层直接包含 script_ref 和 campaign 对象，不要套 arguments 字符串。'
    append_message(conn, task_id, 'user',
                   f'[工具纠错提醒] {name} 的{category}已出现 {count} 次；剩余主轮数 {remaining}。{guidance}',
                   meta={'kind': 'tool_error_reminder', 'tool': name, 'category': category, 'count': count})
