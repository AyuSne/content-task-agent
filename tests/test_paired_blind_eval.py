import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_blind_package_keeps_missing_cases_and_requires_reviews_before_reveal(tmp_path):
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps({"defaults": {}, "cases": [{"id": "example"}]}), encoding="utf-8")
    paths = []
    for name, text in (("old", "first content"), ("new", "second content")):
        case_dir = tmp_path / name / "example"
        case_dir.mkdir(parents=True)
        case_dir.joinpath("05-inspect.json").write_text(json.dumps({
            "status": "complete", "save": {"verified": True, "output_version": 1},
            "outputs": [{"kind": "campaign", "version": 1, "content": {"activity": text}}],
        }), encoding="utf-8")
        paths.append(case_dir.parent)
    target = tmp_path / "package"
    command = [sys.executable, str(ROOT / "scripts/blind_paired_eval.py")]
    subprocess.run(command + ["prepare", "--cases", str(suite), "--old", str(paths[0]),
                              "--new", str(paths[1]), "--output", str(target)], check=True)
    blind = json.loads((target / "blind" / "example.json").read_text(encoding="utf-8"))
    assert {item["content"]["activity"] for item in blind["candidates"]} == {"first content", "second content"}
    assert "old" not in json.dumps(blind) and "new" not in json.dumps(blind)
    assert subprocess.run(command + ["reveal", "--package", str(target)], capture_output=True, check=False).returncode != 0
    reviews = [{"candidate": item["candidate"], "facts": "ok", "requirements": "ok", "production": "ok",
                "uncertainty": "ok", "remarks": "reviewed"} for item in blind["candidates"]]
    (target / "blind" / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    subprocess.run(command + ["reveal", "--package", str(target)], check=True)
    revealed = json.loads((target / "revealed.json").read_text(encoding="utf-8"))
    assert {item["side"] for item in revealed} == {"old", "new"}
    candidate = revealed[0]["candidate"]
    subprocess.run(command + ["correct", "--package", str(target), "--candidate", candidate,
                              "--side", "new", "--reason", "label correction"], check=True)
    correction = json.loads((target / "mapping-corrections.jsonl").read_text(encoding="utf-8"))
    assert correction["reason"] == "label correction"
    assert json.loads((target / "revealed.json").read_text(encoding="utf-8")) == revealed


def test_missing_candidate_is_retained_without_fabricated_content(tmp_path):
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps({"defaults": {"budget": "20"}, "cases": [{"id": "missing"}]}), encoding="utf-8")
    target = tmp_path / "package"
    subprocess.run([sys.executable, str(ROOT / "scripts/blind_paired_eval.py"), "prepare", "--cases", str(suite),
                    "--old", str(tmp_path / "old"), "--new", str(tmp_path / "new"), "--output", str(target)],
                   check=True)
    blind = json.loads((target / "blind" / "missing.json").read_text(encoding="utf-8"))
    assert blind["input"]["budget"] == "20"
    assert len(blind["candidates"]) == 2
    assert all(not item["available"] and item["content"] is None for item in blind["candidates"])


def test_blind_package_accepts_cases_from_multiple_run_roots(tmp_path):
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps({"defaults": {}, "cases": [{"id": "later"}]}), encoding="utf-8")
    case_dir = tmp_path / "old-second" / "later"
    case_dir.mkdir(parents=True)
    (case_dir / "05-inspect.json").write_text(json.dumps({
        "status": "incomplete", "save": None,
        "outputs": [{"kind": "campaign", "version": 1, "content": {"activity": "draft"}}],
    }), encoding="utf-8")
    target = tmp_path / "package"
    subprocess.run([sys.executable, str(ROOT / "scripts/blind_paired_eval.py"), "prepare", "--cases", str(suite),
                    "--old", str(tmp_path / "old-second"), "--old", str(tmp_path / "old-first"),
                    "--new", str(tmp_path / "new"), "--output", str(target)], check=True)
    blind = json.loads((target / "blind" / "later.json").read_text(encoding="utf-8"))
    assert sum(item["available"] for item in blind["candidates"]) == 1


@pytest.mark.parametrize("inspection", ["{not json", '{"error":"not_found"}'])
def test_blind_package_preserves_unreadable_inspection_as_unavailable(tmp_path, inspection):
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps({"defaults": {}, "cases": [{"id": "broken"}]}), encoding="utf-8")
    case_dir = tmp_path / "old" / "broken"
    case_dir.mkdir(parents=True)
    (case_dir / "05-inspect.json").write_text(inspection, encoding="utf-8")
    target = tmp_path / "package"
    subprocess.run([sys.executable, str(ROOT / "scripts/blind_paired_eval.py"), "prepare", "--cases", str(suite),
                    "--old", str(tmp_path / "old"), "--new", str(tmp_path / "new"), "--output", str(target)],
                   check=True)
    blind = json.loads((target / "blind" / "broken.json").read_text(encoding="utf-8"))
    assert {item["source_error"] for item in blind["candidates"]} == {"missing_inspect", "invalid_inspect"}
    assert all(not item["available"] for item in blind["candidates"])


def test_paired_runner_rejects_a_non_repository_old_baseline(tmp_path):
    output = tmp_path / "result"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_paired_eval.py"),
                             "--old", str(tmp_path), "--new", str(ROOT),
                             "--cases", str(ROOT / "data/eval/2026-10-29.v1.json"),
                             "--materials", str(ROOT / "data/materials/catalog.json"),
                             "--env-file", str(tmp_path / "missing.env"), "--output", str(output)],
                            capture_output=True, check=False)
    assert result.returncode != 0
    assert b"fixed old baseline" in result.stderr
    assert not output.exists()
