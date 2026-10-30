"""Derive a labeled scope pair from the preserved real campaign without modifying it."""

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'evidence/redesign/tool-guidance-paired/new/save-crash-recovery/05-inspect.json'
MATERIALS = ROOT / 'evidence/redesign/tool-guidance-paired/materials.json'
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    facts = json.loads(SOURCE.read_text(encoding='utf-8'))
    catalog = json.loads(MATERIALS.read_text(encoding='utf-8'))
    campaign = next(item['content'] for item in facts['outputs'] if item['kind'] == 'campaign')
    good = copy.deepcopy(campaign)
    for evidence in good['evidence'] + good['script']['evidence']:
        if evidence['id'] == 'E3' and evidence['material_id'] == 'M-001':
            evidence['scope'] = 'iOS/Android 4.2 及以上；复杂变速或弱节拍音乐需要人工调整'
    pair = {
        'id': 'real-campaign-scope',
        'issue': 'E3 仅描述曲风条件，未指明平台版本范围，可能误导',
        'requirements': facts['input']['requirements'],
        'evidence': [{'material_id': item['id'], 'source': item['source'], 'text': item.get('text', ''),
                      'scope': item['scope']} for item in catalog['materials']],
        'bad': campaign,
        'good': good,
        'label_source': {'basis': 'M-001 适用 iOS/Android 4.2 及以上；原 E3 范围只写所有曲风与音乐素材。',
                         'provenance': '真实保存稿派生成对样本，good 只修改方案与脚本的 E3 scope；标签为 AI 判断，需复核。'},
    }
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps({'version': 'redesign-real-scope-v1', 'pairs': [pair]},
                                ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
