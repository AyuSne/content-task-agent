"""Verify archived export hashes without the original absolute paths or SQLite files."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("examples/repair-live"))
    args = parser.parse_args()
    failures, files = [], 0
    for metadata in args.root.glob("*/audit-export.json"):
        record = json.loads(metadata.read_text(encoding="utf-8"))
        root = metadata.parent / "data" / "real" / "exports" / record["task_id"]
        for item in record["files"]:
            path = root / Path(item["path"].replace("\\", "/")).name
            files += 1
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                failures.append(str(path))
    print(json.dumps({"verified_files": files, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures or not files else 0


if __name__ == "__main__":
    raise SystemExit(main())
