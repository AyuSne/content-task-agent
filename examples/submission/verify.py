"""Verify the submitted copies without the original machine or database."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent


def main():
    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["copies"]:
        path = (ROOT / item["destination"]).resolve()
        assert path.is_relative_to(DIRECTORY)
        content = path.read_bytes()
        assert len(content) == item["bytes"], str(path)
        assert hashlib.sha256(content).hexdigest() == item["sha256"], str(path)
    events = 0
    for path in DIRECTORY.rglob("trace.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)
                events += 1
    normal = json.loads((DIRECTORY / "live-normal/result.json").read_text(encoding="utf-8"))
    assert normal["status"] == "complete" and normal["save"]["verified"]
    for item in normal["save"]["files"]:
        name = item["path"].replace("\\", "/").rsplit("/", 1)[-1]
        assert hashlib.sha256((DIRECTORY / "live-normal" / name).read_bytes()).hexdigest() == item["sha256"]
    for result in manifest["offline"]["results"]:
        assert result["real_requests"] == 0
        actual = json.loads((DIRECTORY / "offline" / result["case"] / "result.json").read_text(encoding="utf-8"))
        assert actual["status"] == result["status"]
    print(f"核对通过：{len(manifest['copies'])} 份原样归档文件，{events} 条 Trace，6 组离线场景。")


if __name__ == "__main__":
    main()
