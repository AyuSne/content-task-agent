import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_seed_cli_preserves_labelled_pair_and_scripted_review(tmp_path):
    output = tmp_path / "seed"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/eval_redesign_semantics_main.py"), "seed", str(output)],
        cwd=ROOT, env=env, check=True,
    )
    cases = json.loads((output / "cases.json").read_text(encoding="utf-8"))
    assert len(cases) == 2
    for case in cases:
        inspect = json.loads((output / case["id"] / "seed-inspect.json").read_text(encoding="utf-8"))["stdout"]
        assert inspect["reviews"][-1]["findings"][0]["finding_id"] == "F1"
        assert inspect["reviews"][-1]["findings"][0]["severity"] == "must_fix"
        assert all(request["mock"] for request in inspect["llm_requests"])
    bad, good = [json.loads((output / case["id"] / "seed-inspect.json").read_text(encoding="utf-8"))["stdout"]
                 for case in cases]
    assert "保证涨粉" in bad["scripts"][0]["content"]["script"]["segments"][0]["voiceover"]
    assert "保证涨粉" not in good["scripts"][0]["content"]["script"]["segments"][0]["voiceover"]


def test_real_cli_refuses_to_replay_an_interrupted_command(tmp_path):
    case = tmp_path / "actual-defect"
    case.mkdir()
    (case / "real-resume-01-started.json").write_text("{}", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/eval_redesign_semantics_main.py"), "real", str(tmp_path),
         "actual-defect", "--env-file", str(tmp_path / "unused.env")],
        cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=False,
    )
    assert result.returncode != 0
    assert "manual inspection" in result.stderr
    assert not (case / "data" / "real").exists()
