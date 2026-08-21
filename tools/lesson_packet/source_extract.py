import argparse
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader


LESSON_RE = re.compile(r"\((\d+)차시 분량\)\.pdf$")


def parse_lesson_count(name: str) -> int:
    match = LESSON_RE.search(name)
    if not match:
        raise ValueError(f"lesson count missing: {name}")
    return int(match.group(1))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_sources(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.rglob("*.pdf"), key=lambda item: item.name):
        if "학습지" in path.relative_to(root).parts:
            continue
        records.append(
            {
                "sourceFile": path.name,
                "sourceStem": path.stem,
                "lessonCount": parse_lesson_count(path.name),
                "pageCount": len(PdfReader(path).pages),
                "sha256": _sha256(path),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("root", type=Path)
    inventory_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "inventory":
        records = inventory_sources(args.root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
