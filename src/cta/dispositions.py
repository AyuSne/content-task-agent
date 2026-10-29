"""Validate and atomically record decisions tied to an explicit reviewed campaign."""

import json
import re

from cta.config import utc_now_iso
from cta.repository import latest_output, latest_review


def _locate_evidence(conn, task, output, evidence, catalog):
    if not isinstance(evidence, dict):
        return '依据必须是对象'
    kind = evidence.get('kind')
    quote = evidence.get('quote')
    if not isinstance(quote, str) or not quote.strip():
        return 'quote 必须非空'
    if kind == 'requirement':
        if set(evidence) != {'kind', 'input_version', 'field_path', 'quote'}:
            return '原始要求依据必须包含 input_version、field_path 和 quote'
        version = evidence['input_version']
        if not isinstance(version, int) or isinstance(version, bool) or version != output['input_version']:
            return 'input_version 必须对应被审方案的输入版本'
        row = conn.execute('SELECT * FROM task_inputs WHERE task_id = ? AND version = ?', (task['id'], version)).fetchone()
        field = evidence['field_path']
        if not isinstance(field, str):
            return 'field_path 无效'
        match = re.fullmatch(r'requirements\[(\d+)\]', field)
        if match:
            requirements = json.loads(row['requirements_json'])
            index = int(match.group(1))
            text = requirements[index] if index < len(requirements) else None
        elif field in ('topic', 'audience'):
            text = row[field]
        else:
            return 'field_path 不是可定位的输入字段'
        if not isinstance(text, str) or quote not in text:
            return 'quote 未在指定输入字段中找到'
        return None
    if kind == 'material':
        if set(evidence) != {'kind', 'material_id', 'material_version', 'quote', 'scope'}:
            return '材料依据必须包含 material_id、material_version、quote 和 scope'
        material = catalog.get(evidence['material_id']) if isinstance(evidence['material_id'], str) else None
        scope = evidence['scope']
        if material is None or evidence['material_version'] != catalog.version:
            return '材料或版本不属于本任务采用资料'
        if quote not in material.text:
            return 'quote 未在材料正文中找到'
        if not isinstance(scope, str) or not scope.strip() or scope not in material.scope:
            return 'scope 未在材料适用范围中找到'
        return None
    return 'kind 必须是 requirement 或 material'


def record_dispositions(conn, task, arguments, catalog):
    def reject(error, message):
        return {'ok': False, 'error': error, 'message': message}

    if set(arguments) != {'review_id', 'campaign_ref', 'decisions'}:
        return reject('invalid_arguments', '需要 review_id、campaign_ref 和 decisions。')
    review = latest_review(conn, task['id'])
    output = latest_output(conn, task['id'])
    ref = arguments['campaign_ref']
    if (not isinstance(ref, dict) or set(ref) != {'id', 'version'}
            or not isinstance(ref.get('id'), str) or not isinstance(ref.get('version'), int)
            or isinstance(ref.get('version'), bool) or output is None or ref != {'id': output['output_id'], 'version': output['version']}):
        return reject('invalid_campaign_ref', 'campaign_ref 必须指向本任务当前方案的 ID 和版本。')
    from cta.service import output_reuse_basis
    if output['input_version'] != task['input_version'] and output_reuse_basis(conn, task, output) is None:
        return reject('invalid_campaign_ref', '当前方案不适用于当前输入版本。')
    if (review is None or review['status'] != 'complete' or arguments['review_id'] != review['review_id']
            or review['output_id'] != output['output_id'] or review['output_version'] != output['version']):
        return reject('review_required', 'review_id 必须指向当前方案的最新完整核验。')
    decisions = arguments['decisions']
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= 64:
        return reject('invalid_arguments', 'decisions 必须是 1 至 64 项的数组。')
    finding_ids = {item['finding_id'] for item in review['findings']}
    seen = set()
    normalized = []
    for index, item in enumerate(decisions):
        location = f'decisions[{index}]'
        if not isinstance(item, dict) or not {'finding_id', 'decision', 'reason'} <= item.keys() or set(item) - {'finding_id', 'decision', 'reason', 'evidence'}:
            return reject('invalid_arguments', f'{location} 需要 finding_id、decision、reason 和可选 evidence。')
        finding_id, decision, reason = item['finding_id'], item['decision'], item['reason']
        if not isinstance(finding_id, str) or not finding_id.strip() or finding_id in seen:
            return reject('invalid_arguments', f'{location}.finding_id 必须非空且批次内唯一。')
        if finding_id not in finding_ids:
            return reject('finding_not_found', f'{location}.finding_id 不属于指定核验。')
        if decision not in ('accepted', 'rejected', 'needs_evidence') or not isinstance(reason, str) or not reason.strip():
            return reject('invalid_arguments', f'{location} 的 decision 或 reason 无效。')
        evidence = item.get('evidence', [])
        if not isinstance(evidence, list) or (decision == 'rejected' and not evidence):
            return reject('invalid_evidence', f'{location}.evidence 驳回须提供可定位依据列表。')
        for evidence_index, entry in enumerate(evidence):
            error = _locate_evidence(conn, task, output, entry, catalog)
            if error:
                return reject('invalid_evidence', f'{location}.evidence[{evidence_index}]: {error}')
        seen.add(finding_id)
        normalized.append({'finding_id': finding_id, 'decision': decision, 'reason': reason.strip(), 'evidence': evidence})
    conn.executemany('''INSERT INTO dispositions (review_id, finding_id, decision, reason, evidence_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(review_id, finding_id) DO UPDATE SET decision=excluded.decision,
                        reason=excluded.reason, evidence_json=excluded.evidence_json, created_at=excluded.created_at''',
                     [(review['review_id'], d['finding_id'], d['decision'], d['reason'],
                       json.dumps(d['evidence'], ensure_ascii=False), utc_now_iso()) for d in normalized])
    return {'ok': True, 'review_id': review['review_id'], 'campaign_ref': ref, 'dispositions': normalized,
            'message': '处置已记录；依据可定位不代表语义支持已验证。采纳的问题须返修并复核。'}
