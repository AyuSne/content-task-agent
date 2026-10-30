import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_scope_pair_has_one_controlled_difference_and_original_material(tmp_path):
    output = tmp_path / "scope.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/eval_redesign_semantics.py"), "prepare", str(output)],
        cwd=ROOT, check=True,
    )
    pair = json.loads(output.read_text(encoding="utf-8"))["pairs"][0]
    assert pair["evidence"][0]["scope"].startswith("iOS/Android 4.2 及以上")
    assert pair["good"]["evidence"][0]["scope"] == pair["evidence"][0]["scope"]
    assert pair["bad"]["evidence"][0]["scope"] == "所有 iOS 和 Android 版本，包括 4.2 以下"
    pair["bad"]["evidence"][0]["scope"] = pair["good"]["evidence"][0]["scope"]
    assert pair["bad"] == pair["good"]
