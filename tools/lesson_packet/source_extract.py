import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader

from tools.lesson_packet.visuals import validate_reconstructed_visual


LESSON_RE = re.compile(r"\((\d+)차시 분량\)\.pdf$")
DEFAULT_SOURCE_ROOT = Path("C:/Users/user/Desktop/물질과 에너지 교과서")
REUSE_MODES = {"crop", "reconstruct"}


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


def render_source_pages(pdf: Path, out_dir: Path, dpi: int = 144) -> list[Path]:
    """Render every PDF page to a one-based PNG sequence and record its source hash."""
    if dpi <= 0:
        raise ValueError("dpi must be positive")

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered_pages = []
    document = pdfium.PdfDocument(str(pdf))
    try:
        for page_number in range(1, len(document) + 1):
            page = document[page_number - 1]
            try:
                bitmap = page.render(scale=dpi / 72)
                try:
                    image = bitmap.to_pil()
                    output = out_dir / f"page-{page_number:03d}.png"
                    image.save(output, format="PNG")
                    rendered_pages.append(output)
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()

    metadata = {
        "sourceFile": pdf.name,
        "sourceSha256": _sha256(pdf),
        "pageCount": len(rendered_pages),
        "dpi": dpi,
        "pages": [page.name for page in rendered_pages],
    }
    (out_dir / "rendered-pages.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return rendered_pages


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(_nonempty_text(item) for item in value)


def validate_visual_map(entry: dict, rendered_metadata: dict | None = None) -> list[str]:
    """Return deterministic visual-map errors, including stale render source hashes."""
    if not isinstance(entry, dict):
        return ["visual-invalid"]
    errors = []
    source_page = entry.get("sourcePage")
    if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1:
        errors.append("source-page-must-be-one-based")

    reuse_mode = entry.get("reuseMode")
    if not isinstance(reuse_mode, str) or reuse_mode not in REUSE_MODES:
        errors.append("reuse-mode-invalid")

    crop = entry.get("crop")
    if crop is not None:
        if (
            not isinstance(crop, list)
            or len(crop) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in crop)
        ):
            errors.append("crop-invalid")
        elif not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
            errors.append("crop-out-of-bounds")
    elif reuse_mode == "crop":
        errors.append("crop-missing")

    semantic_fields = {
        "id": "visual-id-missing",
        "figureLabel": "figure-label-missing",
        "purpose": "purpose-missing",
    }
    for field, error in semantic_fields.items():
        if field in entry and not _nonempty_text(entry[field]):
            errors.append(error)
    if reuse_mode == "reconstruct":
        schema = entry.get("schema")
        if not isinstance(schema, dict) or not isinstance(schema.get("scene"), dict):
            errors.append("reconstruct-scene-missing")
        for field in ("entities", "relationships", "invariants"):
            value = entry.get(field)
            if not isinstance(value, list) or not value or any(not isinstance(item, dict) for item in value):
                errors.append(f"{field}-invalid")
        try:
            validate_reconstructed_visual(entry, require_scene=True)
        except ValueError:
            errors.append("reconstruct-invalid")
    else:
        for field in ("axes", "units"):
            if field in entry and not _string_list(entry[field]):
                errors.append(f"{field}-invalid")

    if rendered_metadata is not None:
        if not isinstance(rendered_metadata, dict):
            return errors + ["rendered-metadata-invalid"]
        rendered_hash = rendered_metadata.get("sourceSha256")
        visual_hash = entry.get("sourceSha256")
        if not _nonempty_text(rendered_hash) or not _nonempty_text(visual_hash):
            errors.append("source-hash-missing")
        elif visual_hash != rendered_hash:
            errors.append("source-hash-stale")
        page_count = rendered_metadata.get("pageCount")
        if isinstance(source_page, int) and not isinstance(source_page, bool) and isinstance(page_count, int):
            if not 1 <= source_page <= page_count:
                errors.append("source-page-out-of-range")
    return errors


def _find_source_pdf(source_root: Path, source_file: str) -> Path:
    matches = [
        path
        for path in source_root.rglob(source_file)
        if "학습지" not in path.relative_to(source_root).parts
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one source PDF named {source_file!r}, found {len(matches)}")
    return matches[0]


def render_all_sources(index_path: Path, out_dir: Path, source_root: Path = DEFAULT_SOURCE_ROOT) -> list[dict]:
    records = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("source index must contain a list")

    results = []
    for record in records:
        pdf = _find_source_pdf(source_root, record["sourceFile"])
        rendered = render_source_pages(pdf, out_dir / record["sourceStem"])
        if len(rendered) != record["pageCount"]:
            raise ValueError(f"rendered page count mismatch for {record['sourceFile']}")
        metadata_path = rendered[0].parent / "rendered-pages.json" if rendered else out_dir / record["sourceStem"] / "rendered-pages.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["sourceSha256"] != record["sha256"]:
            raise ValueError(f"source hash mismatch for {record['sourceFile']}")
        results.append({"sourceFile": record["sourceFile"], "renderedPages": len(rendered)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory_parser = subparsers.add_parser("inventory")
    inventory_parser.add_argument("root", type=Path)
    inventory_parser.add_argument("--out", type=Path, required=True)
    render_parser = subparsers.add_parser("render-all")
    render_parser.add_argument("--index", type=Path, required=True)
    render_parser.add_argument("--out", type=Path, required=True)
    render_parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    args = parser.parse_args()

    if args.command == "inventory":
        records = inventory_sources(args.root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif args.command == "render-all":
        for result in render_all_sources(args.index, args.out, args.source_root):
            print(f"{result['sourceFile']}: {result['renderedPages']} pages")


if __name__ == "__main__":
    main()
