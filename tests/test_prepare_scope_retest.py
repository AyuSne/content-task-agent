import json
import subprocess
import sys
from pathlib import Path


def test_scope_pair_is_reproducible_and_never_overwrites(tmp_path):
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / 'pair.json'
    command = [sys.executable, str(root / 'scripts/prepare_scope_retest.py'), '--output', str(target)]
    first = subprocess.run(command, cwd=root, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    expected = json.loads((root / 'data/eval/redesign-real-scope-v1.json').read_text(encoding='utf-8'))
    assert json.loads(target.read_text(encoding='utf-8')) == expected
    before = target.read_bytes()
    repeated = subprocess.run(command, cwd=root, capture_output=True, check=False)
    assert repeated.returncode != 0
    assert target.read_bytes() == before
