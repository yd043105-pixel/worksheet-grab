import argparse
import copy
import hashlib
import json
import math
import re
import shutil
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pypdf import PdfReader
from PIL import Image as PillowImage

try:
    from tools.lesson_packet.source_evidence import extract_page_evidence, normalize_bounds, sha256_file
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    from source_evidence import extract_page_evidence, normalize_bounds, sha256_file

try:
    from tools.lesson_packet.visuals import validate_reconstructed_visual
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    from visuals import validate_reconstructed_visual

try:
    from tools.lesson_packet.figure_assembly import assemble_composite_figures
    from tools.lesson_packet.source_evidence import extract_page_evidence
    from tools.lesson_packet.visual_ranking import (
        link_candidate_context as link_canonical_candidate_context,
        rank_visual_candidate as rank_canonical_visual_candidate,
    )
    from tools.lesson_packet.visual_review import REQUIRED_CHECKS, render_review_packet
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from figure_assembly import assemble_composite_figures
    from source_evidence import extract_page_evidence
    from visual_ranking import (
        link_candidate_context as link_canonical_candidate_context,
        rank_visual_candidate as rank_canonical_visual_candidate,
    )
    from visual_review import REQUIRED_CHECKS, render_review_packet


LESSON_RE = re.compile(r"\((\d+)차시 분량\)\.pdf$")
DEFAULT_SOURCE_ROOT = Path("C:/Users/user/Desktop/물질과 에너지 교과서")
REUSE_MODES = {"crop", "reconstruct"}
LABEL_CORE = (
    r"(?:그림|표|그래프|Figure|Fig\.?|Table)\s*"
    r"(?:[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*)?[-－–—]?\s*[0-9]+(?:[.-][0-9]+)?"
)
CAPTION_LABEL_RE = re.compile(rf"(?P<label>{LABEL_CORE})", re.IGNORECASE)
REFERENCE_RE = re.compile(rf"(?P<label>{LABEL_CORE})", re.IGNORECASE)
SECTION_NAMES = ("phenomenon", "explanation", "representation", "workedExample", "question")
ROLE_TERMS = {
    "apparatus", "axis", "curve", "data", "diagram", "figure", "graph", "model", "pressure",
    "rate", "table", "장치", "축", "곡선", "자료", "그림", "그래프", "모형", "압력", "속도", "표",
    "실험", "관찰", "관계", "변화", "에너지", "농도", "온도", "부피", "분자", "입자", "삼투", "엔탈피",
}
STOP_TERMS = {
    "a", "an", "and", "are", "as", "at", "by", "figure", "fig", "for", "from", "in", "is",
    "of", "on", "or", "shows", "table", "the", "to", "with", "그림", "표", "그래프",
}
HAZARD_KEYS = ("answerLeakage", "requiredAnnotation", "poorPrintLegibility", "cropLosesMeaning")


def parse_lesson_count(name: str) -> int:
    match = LESSON_RE.search(name)
    if not match:
        raise ValueError(f"lesson count missing: {name}")
    return int(match.group(1))


def _sha256(path: Path) -> str:
    return sha256_file(path)


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


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00ad", "")).strip()


def _caption_label(text: str) -> str | None:
    match = CAPTION_LABEL_RE.search(text)
    if not match:
        return None
    prefix = text[:match.start()]
    remainder = text[match.end():].lstrip()
    starts_with_label = not prefix.strip(" \t|[]()▲△▶▷")
    pipe_delimited = "|" in prefix or remainder.startswith("|")
    if not starts_with_label and not pipe_delimited:
        return None
    if starts_with_label and re.match(r"^(?:shows?|illustrates?|depicts?|is|are)\b", remainder, re.IGNORECASE):
        return None
    if starts_with_label and remainder.startswith(("은", "는", "이", "가", "에서", "의")):
        return None
    return _normalized_text(match.group("label")).rstrip(".:-")


def _label_key(text: str) -> tuple[str, str] | None:
    match = REFERENCE_RE.search(text)
    if not match:
        return None
    label = match.group("label")
    kind_match = re.match(r"\s*(그림|표|그래프|Figure|Fig\.?|Table)", label, re.IGNORECASE)
    numbers = re.findall(r"[0-9]+", label)
    if not kind_match or not numbers:
        return None
    kind = kind_match.group(1).casefold().rstrip(".")
    if kind == "fig":
        kind = "figure"
    if kind in {"그림", "그래프"}:
        kind = "figure"
    elif kind == "표":
        kind = "table"
    return kind, numbers[-1]


def _normalize_bounds(box: tuple[float, float, float, float], width: float, height: float) -> list[float]:
    return list(normalize_bounds(box, width, height))


def _line_records(page) -> list[dict]:
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    words = sorted(words, key=lambda word: (round(float(word["top"]), 3), float(word["x0"]), word["text"]))
    lines: list[list[dict]] = []
    for word in words:
        if not lines:
            lines.append([word])
            continue
        current = lines[-1]
        baseline_close = abs(float(word["top"]) - float(current[0]["top"])) <= 2.5
        horizontal_gap = float(word["x0"]) - float(current[-1]["x1"])
        if baseline_close and horizontal_gap <= max(18.0, float(word["bottom"]) - float(word["top"])):
            current.append(word)
        else:
            lines.append([word])

    records = []
    for words_in_line in lines:
        source_text = " ".join(str(word["text"]) for word in words_in_line)
        text = _normalized_text(source_text)
        if not text:
            continue
        records.append(
            {
                "sourceText": source_text,
                "text": text,
                "box": (
                    min(float(word["x0"]) for word in words_in_line),
                    min(float(word["top"]) for word in words_in_line),
                    max(float(word["x1"]) for word in words_in_line),
                    max(float(word["bottom"]) for word in words_in_line),
                ),
                "captionLabel": _caption_label(text),
            }
        )
    return records


def _boxes_horizontally_related(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    overlap = min(first[2], second[2]) - max(first[0], second[0])
    return overlap > 0 or abs(first[0] - second[0]) <= 36


def _cluster_text_blocks(page) -> list[dict]:
    lines = _line_records(page)
    blocks: list[dict] = []
    for line in lines:
        if blocks:
            previous = blocks[-1]
            gap = line["box"][1] - previous["box"][3]
            line_height = line["box"][3] - line["box"][1]
            same_type = bool(line["captionLabel"]) == bool(previous["captionLabel"])
            if (
                same_type
                and -1 <= gap <= max(4.0, line_height * 0.75)
                and _boxes_horizontally_related(previous["box"], line["box"])
            ):
                previous["sourceText"] += "\n" + line["sourceText"]
                previous["text"] = _normalized_text(previous["text"] + " " + line["text"])
                previous["box"] = (
                    min(previous["box"][0], line["box"][0]),
                    min(previous["box"][1], line["box"][1]),
                    max(previous["box"][2], line["box"][2]),
                    max(previous["box"][3], line["box"][3]),
                )
                continue
        blocks.append(dict(line))
    return blocks


def _object_box(obj: dict) -> tuple[float, float, float, float] | None:
    try:
        left = float(obj["x0"])
        right = float(obj["x1"])
        top = float(obj["top"])
        bottom = float(obj["bottom"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        return None
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    if right - left < 1:
        center = (left + right) / 2
        left, right = center - 0.5, center + 0.5
    if bottom - top < 1:
        center = (top + bottom) / 2
        top, bottom = center - 0.5, center + 0.5
    return left, top, right, bottom


def _box_union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _boxes_close(first: tuple[float, float, float, float], second: tuple[float, float, float, float], gap: float = 6) -> bool:
    return not (
        first[2] + gap < second[0]
        or second[2] + gap < first[0]
        or first[3] + gap < second[1]
        or second[3] + gap < first[1]
    )


def _box_intersection_area(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height


def _is_page_furniture(box: tuple[float, float, float, float], width: float, height: float) -> bool:
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    near_edge = box[1] <= height * 0.04 or box[3] >= height * 0.96
    return (box_width >= width * 0.8 and box_height <= 4 and near_edge) or (
        box_height >= height * 0.8 and box_width <= 4
    )


def _box_intersects_page(box: tuple[float, float, float, float], width: float, height: float) -> bool:
    return box[2] > 0 and box[3] > 0 and box[0] < width and box[1] < height


def _vector_groups(page, table_boxes: list[tuple[float, float, float, float]]) -> list[tuple[tuple[float, float, float, float], int]]:
    objects = []
    for obj in list(page.lines) + list(page.rects) + list(page.curves):
        box = _object_box(obj)
        if box is None or _is_page_furniture(box, float(page.width), float(page.height)):
            continue
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if any(table[0] <= center[0] <= table[2] and table[1] <= center[1] <= table[3] for table in table_boxes):
            continue
        objects.append(box)
    objects.sort()
    groups: list[list[tuple[float, float, float, float]]] = []
    for box in objects:
        matching = [index for index, group in enumerate(groups) if _boxes_close(_box_union(group), box)]
        if not matching:
            groups.append([box])
            continue
        target = matching[0]
        groups[target].append(box)
        for index in reversed(matching[1:]):
            groups[target].extend(groups.pop(index))

    result = []
    for group in groups:
        box = _box_union(group)
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        if box_width < 12 or box_height < 12:
            continue
        if box_width * box_height < float(page.width) * float(page.height) * 0.0015:
            continue
        result.append((box, len(group)))
    return sorted(result, key=lambda item: (item[0][1], item[0][0], item[0][3], item[0][2], item[1]))


def _base_region(
    region_id: str,
    source_page: int,
    source_hash: str,
    bounds: list[float],
) -> dict:
    return {
        "id": region_id,
        "sourcePage": source_page,
        "sourceSha256": source_hash,
        "bounds": bounds,
    }


def extract_page_regions(pdf: Path, rendered_pages: Path) -> list[dict]:
    """Separate positioned PDF text/captions from native visual geometry."""
    pdf = Path(pdf)
    rendered_pages = Path(rendered_pages)
    if not pdf.is_file():
        raise ValueError(f"source PDF not found: {pdf}")
    if not rendered_pages.is_dir():
        raise ValueError(f"rendered page directory not found: {rendered_pages}")
    source_hash = _sha256(pdf)
    pages = []
    try:
        document = pdfplumber.open(pdf)
    except Exception as error:
        raise ValueError(f"could not open source PDF: {pdf}") from error
    try:
        for source_page, page in enumerate(document.pages, start=1):
            width = float(page.width)
            height = float(page.height)
            if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
                raise ValueError(f"invalid page dimensions on page {source_page}")
            page_id = f"page-{source_page:03d}"
            blocks = _cluster_text_blocks(page)
            body_blocks = []
            caption_blocks = []
            for block in sorted(blocks, key=lambda item: (item["box"][1], item["box"][0], item["text"])):
                left, top, right, bottom = block["box"]
                if right <= 0 or bottom <= 0 or left >= width or top >= height:
                    continue
                target = caption_blocks if block["captionLabel"] else body_blocks
                kind = "caption" if block["captionLabel"] else "text"
                region = _base_region(
                    f"{page_id}-{kind}-{len(target) + 1:03d}",
                    source_page,
                    source_hash,
                    _normalize_bounds(block["box"], width, height),
                )
                region.update({"text": block["text"], "sourceText": block["sourceText"]})
                if block["captionLabel"]:
                    region["label"] = block["captionLabel"]
                target.append(region)

            table_boxes = []
            table_candidates = []
            try:
                tables = page.find_tables()
            except Exception:
                tables = []
            for table in tables:
                try:
                    box = tuple(float(value) for value in table.bbox)
                except (TypeError, ValueError):
                    continue
                if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
                    continue
                table_boxes.append(box)
                table_candidates.append({"kind": "table", "box": box, "objectCount": len(table.cells)})

            candidates = []
            for image in page.images:
                box = _object_box(image)
                if box is None:
                    continue
                if (box[2] - box[0]) * (box[3] - box[1]) < width * height * 0.0005:
                    continue
                candidates.append({"kind": "nativeImage", "box": box, "objectCount": 1})
            candidates.extend(table_candidates)
            candidates.extend(
                {"kind": "vectorGroup", "box": box, "objectCount": count}
                for box, count in _vector_groups(page, table_boxes)
            )
            candidates = [candidate for candidate in candidates if _box_intersects_page(candidate["box"], width, height)]
            kind_slug = {
                "nativeImage": "native-image",
                "table": "table",
                "vectorGroup": "vector-group",
                "renderedFallback": "rendered-fallback",
            }
            candidates.sort(key=lambda item: (kind_slug[item["kind"]], item["box"][1], item["box"][0], item["box"][3], item["box"][2]))
            visual_candidates = []
            kind_counts: dict[str, int] = {}
            for candidate in candidates:
                slug = kind_slug[candidate["kind"]]
                kind_counts[slug] = kind_counts.get(slug, 0) + 1
                region = _base_region(
                    f"{page_id}-{slug}-{kind_counts[slug]:03d}",
                    source_page,
                    source_hash,
                    _normalize_bounds(candidate["box"], width, height),
                )
                region.update({"kind": candidate["kind"], "objectCount": candidate["objectCount"]})
                visual_candidates.append(region)

            rendered_page = rendered_pages / f"page-{source_page:03d}.png"
            if not rendered_page.is_file():
                raise ValueError(f"rendered page missing: {rendered_page}")
            if not visual_candidates:
                visual_candidates.append(
                    {
                        **_base_region(
                            f"{page_id}-rendered-fallback-001",
                            source_page,
                            source_hash,
                            [0.0, 0.0, 1.0, 1.0],
                        ),
                        "kind": "renderedFallback",
                        "objectCount": 1,
                    }
                )
            visual_candidates.sort(key=lambda item: item["id"])
            pages.append(
                {
                    "id": page_id,
                    "sourcePage": source_page,
                    "sourceSha256": source_hash,
                    "pageWidth": round(width, 6),
                    "pageHeight": round(height, 6),
                    "bounds": [0.0, 0.0, 1.0, 1.0],
                    "renderedPage": rendered_page.name,
                    "textBlocks": body_blocks,
                    "captionBlocks": caption_blocks,
                    "visualCandidates": visual_candidates,
                }
            )
    finally:
        document.close()
    return pages


def _validate_normalized_bounds(value: object, field: str = "bounds") -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value)
        or not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1)
    ):
        raise ValueError(f"{field} must be normalized [left, top, right, bottom]")
    return [float(item) for item in value]


def _normalized_box_distance(first: list[float], second: list[float]) -> float:
    horizontal = max(first[0] - second[2], second[0] - first[2], 0.0)
    vertical = max(first[1] - second[3], second[1] - first[3], 0.0)
    return round(math.hypot(horizontal, vertical), 6)


def link_visual_context(page_regions: list[dict]) -> list[dict]:
    """Attach captions, nearby blocks, and explicit body references to candidates."""
    if not isinstance(page_regions, list):
        raise ValueError("page regions must be a list")
    linked = copy.deepcopy(page_regions)
    for page_index, page in enumerate(linked):
        if not isinstance(page, dict):
            raise ValueError(f"page region {page_index} must be an object")
        for field in ("textBlocks", "captionBlocks", "visualCandidates"):
            if not isinstance(page.get(field), list):
                raise ValueError(f"page region {page_index} {field} must be a list")
        captions = page["captionBlocks"]
        body = page["textBlocks"]
        for candidate in page["visualCandidates"]:
            if not isinstance(candidate, dict):
                raise ValueError("visual candidate must be an object")
            candidate_bounds = _validate_normalized_bounds(candidate.get("bounds"))
            scored_captions = []
            for caption in captions:
                if not isinstance(caption, dict) or not _nonempty_text(caption.get("text")):
                    raise ValueError("caption block must contain text")
                caption_bounds = _validate_normalized_bounds(caption.get("bounds"))
                label = caption.get("label") or _caption_label(caption["text"])
                label_type = str(label or "").lower()
                mismatch = 0.0
                if label_type.startswith(("table", "표")) and candidate.get("kind") != "table":
                    mismatch = 0.5
                elif candidate.get("kind") == "table" and not label_type.startswith(("table", "표")):
                    mismatch = 0.5
                scored_captions.append(
                    (_normalized_box_distance(candidate_bounds, caption_bounds) + mismatch, caption["id"], caption)
                )
            caption = min(scored_captions, default=(None, None, None))[2]
            if caption is not None and _normalized_box_distance(candidate_bounds, caption["bounds"]) <= 0.22:
                caption_id = caption["id"]
                caption_text = caption["text"]
                label = caption.get("label") or _caption_label(caption_text)
            else:
                caption_id = None
                caption_text = ""
                label = None

            nearby = []
            for block in body:
                if not isinstance(block, dict) or not _nonempty_text(block.get("text")):
                    raise ValueError("text block must contain text")
                distance = _normalized_box_distance(candidate_bounds, _validate_normalized_bounds(block.get("bounds")))
                if distance <= 0.35:
                    nearby.append({"id": block["id"], "text": block["text"], "distance": distance})
            nearby.sort(key=lambda item: (item["distance"], item["id"]))
            nearby = nearby[:6]
            references = []
            if label:
                label_key = _label_key(label)
                references = [
                    block
                    for block in body
                    if label_key is not None
                    and any(_label_key(match.group("label")) == label_key for match in REFERENCE_RE.finditer(block["text"]))
                ]
            candidate.update(
                {
                    "captionId": caption_id,
                    "captionText": caption_text,
                    "nearbyTextIds": [item["id"] for item in nearby],
                    "nearbyText": nearby,
                    "bodyReferenceIds": [item["id"] for item in references],
                    "explicitReferences": [item["text"] for item in references],
                    "contextScoreBreakdown": {
                        "explicitLabel": 2.0 if caption_id else 0.0,
                        "proximity": round(max(0.0, 1.0 - (nearby[0]["distance"] * 4)), 3) if nearby else 0.0,
                        "bodyReference": 2.0 if references else 0.0,
                    },
                }
            )
        page["visualCandidates"].sort(key=lambda item: item["id"])
    return linked


def _flatten_text(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, dict):
        result = []
        for key in sorted(value):
            result.extend(_flatten_text(value[key]))
        return result
    if value is None:
        return []
    raise ValueError("lesson evidence text must contain strings, lists, or objects")


def _terms(texts: list[str]) -> set[str]:
    tokens = set()
    for text in texts:
        normalized = _normalized_text(text).casefold()
        tokens.update(token for token in re.findall(r"[^\W_]+", normalized) if len(token) > 1 and token not in STOP_TERMS)
    return tokens


def rank_visual_candidate(candidate: dict, lesson_evidence: dict) -> dict:
    """Return a provisional evidence-backed reuse decision for one source candidate."""
    if not isinstance(candidate, dict):
        raise ValueError("visual candidate must be an object")
    if not isinstance(lesson_evidence, dict):
        raise ValueError("lesson evidence must be an object")
    sections = lesson_evidence.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("lesson evidence sections must be an object")
    unknown_sections = sorted(set(sections) - set(SECTION_NAMES))
    if unknown_sections:
        raise ValueError(f"unsupported lesson evidence section: {unknown_sections[0]}")
    if not _nonempty_text(candidate.get("id")) or not _nonempty_text(candidate.get("kind")):
        raise ValueError("visual candidate id and kind are required")
    source_page = candidate.get("sourcePage")
    if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1:
        raise ValueError("visual candidate sourcePage must be one-based")
    source_hash = candidate.get("sourceSha256")
    if not _nonempty_text(source_hash):
        raise ValueError("visual candidate sourceSha256 is required")
    bounds = _validate_normalized_bounds(candidate.get("bounds"))

    candidate_texts = []
    for field in ("captionText",):
        value = candidate.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"visual candidate {field} must be text")
        if value:
            candidate_texts.append(value)
    for field in ("explicitReferences",):
        value = candidate.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"visual candidate {field} must be a list of text")
        candidate_texts.extend(value)
    nearby = candidate.get("nearbyText", [])
    if not isinstance(nearby, list):
        raise ValueError("visual candidate nearbyText must be a list")
    distances = []
    for item in nearby:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("nearby text entries must be objects with text")
        distance = item.get("distance")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance < 0:
            raise ValueError("nearby text distance must be non-negative")
        candidate_texts.append(item["text"])
        distances.append(float(distance))
    candidate_terms = _terms(candidate_texts)

    section_terms = {}
    for name in SECTION_NAMES:
        section_terms[name] = _terms(_flatten_text(sections.get(name)))
    overlaps = {name: sorted(candidate_terms & terms) for name, terms in section_terms.items()}
    supported_section = max(
        SECTION_NAMES,
        key=lambda name: (len(overlaps[name]), -SECTION_NAMES.index(name)),
    )
    overlap_count = len(overlaps[supported_section])

    caption_text = candidate.get("captionText", "")
    references = candidate.get("explicitReferences", [])
    caption_reference = (1.5 if caption_text.strip() else 0.0) + (1.5 if references else 0.0)
    nearest = min(distances, default=None)
    if nearest is None:
        spatial = 0.0
    elif nearest <= 0.05:
        spatial = 1.0
    elif nearest <= 0.15:
        spatial = 0.75
    elif nearest <= 0.3:
        spatial = 0.5
    else:
        spatial = 0.25
    term_overlap = min(3.0, overlap_count * 0.75)
    role_matches = sorted(candidate_terms & ROLE_TERMS)
    instructional_role = 1.0 if role_matches and overlap_count else 0.0
    score_breakdown = {
        "captionReference": caption_reference,
        "spatialProximity": spatial,
        "termOverlap": term_overlap,
        "instructionalRole": instructional_role,
    }
    score = round(sum(score_breakdown.values()), 3)
    retained = score >= 4.0 and overlap_count > 0 and (caption_reference > 0 or instructional_role > 0)

    reasons = []
    if caption_reference:
        reasons.append("caption/reference evidence links the visual to source explanation")
    if spatial:
        reasons.append(f"nearby explanatory text has normalized distance {nearest:.3f}")
    if term_overlap:
        reasons.append(f"term overlap with {supported_section}: {', '.join(overlaps[supported_section][:8])}")
    if instructional_role:
        reasons.append(f"instructional-role terms: {', '.join(role_matches[:8])}")

    hazards = candidate.get("hazards", {})
    if not isinstance(hazards, dict):
        raise ValueError("visual candidate hazards must be an object")
    malformed_hazards = sorted(key for key, value in hazards.items() if key not in HAZARD_KEYS or not isinstance(value, bool))
    if malformed_hazards:
        raise ValueError(f"visual candidate hazard invalid: {malformed_hazards[0]}")
    active_hazards = [key for key in HAZARD_KEYS if hazards.get(key)]

    if not retained:
        recommendation = "exclude"
        supported = None
        reasons.append("weak explicit evidence; defaulted to exclude")
    elif active_hazards:
        recommendation = "reconstruct"
        supported = supported_section
        reasons.extend(f"{hazard} requires meaning-preserving reconstruction" for hazard in active_hazards)
    else:
        recommendation = "reuse"
        supported = supported_section
        reasons.append("clear relevant source crop has no declared reconstruction hazard")

    ranked = copy.deepcopy(candidate)
    ranked.update(
        {
            "score": score,
            "scoreBreakdown": score_breakdown,
            "recommendation": recommendation,
            "reviewRequired": True,
            "supportedSection": supported,
            "decisionReasons": reasons,
            "sourcePage": source_page,
            "sourceSha256": source_hash,
            "cropBounds": bounds,
        }
    )
    return ranked


def _source_lesson_evidence(pages: list[dict]) -> dict:
    sections = {name: [] for name in SECTION_NAMES}
    for page in pages:
        for block in page["textBlocks"]:
            text = block["text"]
            lowered = text.casefold()
            sections["explanation"].append(text)
            if REFERENCE_RE.search(text) or any(term in lowered for term in ("graph", "data", "diagram", "curve", "그래프", "자료", "곡선", "모형")):
                sections["representation"].append(text)
            if "?" in text or any(term in lowered for term in ("question", "calculate", "explain", "구하", "설명하", "문제")):
                sections["question"].append(text)
            if any(term in lowered for term in ("example", "solution", "예제", "풀이", "계산")):
                sections["workedExample"].append(text)
            if any(term in lowered for term in ("observe", "experiment", "phenomen", "관찰", "실험", "현상")):
                sections["phenomenon"].append(text)
    return {"sections": sections}


def _canonical_lesson_evidence(source_stem: str, pages: list[dict]) -> dict:
    texts = [
        block.get("text", block.get("matchText", ""))
        for page in pages
        for block in page.get("textBlocks", [])
        if isinstance(block, dict) and _nonempty_text(block.get("text", block.get("matchText", "")))
    ]
    references = [
        text
        for page in pages
        for block in page.get("textBlocks", [])
        if isinstance(block, dict)
        and block.get("role") in {"body", "caption"}
        and isinstance((text := block.get("text", block.get("matchText", ""))), str)
        and REFERENCE_RE.search(text)
    ]
    return {
        "sections": {source_stem: texts},
        "entities": texts,
        "quantities": texts,
        "explicitReferences": references,
    }


def _safe_candidate_id(source_stem: str, source_page: int, local_id: str) -> str:
    safe_stem = re.sub(r"[^\w.-]+", "-", source_stem, flags=re.UNICODE).strip("-._")
    if not safe_stem:
        safe_stem = hashlib.sha256(source_stem.encode("utf-8")).hexdigest()[:12]
    safe_local = re.sub(r"[^\w.-]+", "-", local_id, flags=re.UNICODE).strip("-._")
    return f"{safe_stem}-p{source_page:03d}-{safe_local}"


def _pixel_crop(bounds: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, min(width - 1, math.floor(bounds[0] * width)))
    top = max(0, min(height - 1, math.floor(bounds[1] * height)))
    right = max(left + 1, min(width, math.ceil(bounds[2] * width)))
    bottom = max(top + 1, min(height, math.ceil(bounds[3] * height)))
    return left, top, right, bottom


def _write_candidate_crop(rendered_page: Path, bounds: list[float], output: Path) -> None:
    try:
        with PillowImage.open(rendered_page) as source:
            image = source.convert("RGB")
    except (OSError, ValueError) as error:
        raise ValueError(f"could not open rendered page: {rendered_page}") from error
    crop = image.crop(_pixel_crop(bounds, image.width, image.height))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        crop.save(output, format="PNG", optimize=False, compress_level=9)
    except OSError as error:
        raise ValueError(f"could not write candidate crop: {output}") from error


def _normalized_bounds_valid(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        return False
    return 0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1


def _audit_representatives(manifests: list[dict]) -> list[dict]:
    specifications = (
        ("gas-pressure-boyle-j-tube", ("1-1-1.",), ("boyle", "보일", "j자", "j tube", "j-")),
        ("osmosis-figures-13-14", ("2-2-3.",), ("그림 - 13", "그림 - 14", "figure 13", "figure 14")),
        ("reaction-rate-graph", ("4-1-1.",), ("반응 속도", "reaction rate", "농도 그래프")),
        ("enthalpy-graphs", ("3-1-1.",), ("엔탈피", "enthalpy", "그림 - 6", "그림 - 7")),
        ("activation-energy-graphs", ("4-1-3.",), ("활성화 에너지", "activation energy", "그림 - 7")),
    )
    results = []
    for representative_id, stem_prefixes, terms in specifications:
        matching = [manifest for manifest in manifests if manifest["sourceStem"].startswith(stem_prefixes)]
        pages = [page for manifest in matching for page in manifest["pages"]]
        candidates = [candidate for page in pages for candidate in page.get("visualCandidates", [])]
        caption_texts = [
            block.get("text", "")
            for page in pages
            for block in page.get("textBlocks", [])
            if block.get("role") == "caption"
        ]
        term_text = " ".join(
            [*caption_texts]
            + [
                block.get("text", "")
                for page in pages
                for block in page.get("textBlocks", [])
                if isinstance(block, dict)
            ]
        ).casefold()
        matched_terms = [term for term in terms if term.casefold() in term_text]
        candidate_ids = [
            candidate["id"]
            for candidate in candidates
            if any(term.casefold() in str(candidate.get("captionText", "")).casefold() for term in terms)
        ]
        fragmentation = []
        neighbor_merges = []
        axis_omissions = []
        decorative_retention = []
        for page in pages:
            captions = [
                block for block in page.get("textBlocks", []) if block.get("role") == "caption"
            ]
            for candidate in page.get("visualCandidates", []):
                selected = set(candidate.get("visualAtomIds", []))
                other_atoms = [
                    atom for atom in page.get("visualAtoms", [])
                    if atom.get("id") not in selected
                    and _normalized_bounds_valid(atom.get("bounds"))
                    and _normalized_bounds_valid(candidate.get("bounds"))
                    and not (
                        atom["bounds"][2] <= candidate["bounds"][0]
                        or atom["bounds"][0] >= candidate["bounds"][2]
                        or atom["bounds"][3] <= candidate["bounds"][1]
                        or atom["bounds"][1] >= candidate["bounds"][3]
                    )
                ]
                if other_atoms:
                    fragmentation.append(candidate["id"])
                for caption in captions:
                    if caption.get("id") == candidate.get("captionId"):
                        continue
                    if _normalized_bounds_valid(caption.get("bounds")) and _normalized_bounds_valid(candidate.get("bounds")):
                        if not (
                            caption["bounds"][2] <= candidate["bounds"][0]
                            or caption["bounds"][0] >= candidate["bounds"][2]
                            or caption["bounds"][3] <= candidate["bounds"][1]
                            or caption["bounds"][1] >= candidate["bounds"][3]
                        ):
                            neighbor_merges.append(candidate["id"])
                candidate_text = " ".join(
                    [str(candidate.get("captionText", ""))]
                    + [str(item.get("text", "")) for item in candidate.get("internalText", [])]
                ).casefold()
                if any(token in candidate_text for token in ("graph", "그래프", "축", "axis")):
                    if not any(token in candidate_text for token in ("kpa", "atm", "mol", "시간", "time", "pressure", "압력", "농도", "concentration")):
                        axis_omissions.append(candidate["id"])
                if candidate.get("decision") != "exclude" and (
                    candidate.get("decorative") is True
                    or str(candidate.get("kind", "")).casefold() in {"portrait", "decorativeportrait", "decorative-portrait"}
                ):
                    decorative_retention.append(candidate["id"])
        blocker = None
        if not matching:
            blocker = "source-manifest-missing"
        elif not matched_terms:
            blocker = "representative-terms-not-found"
        elif not candidate_ids:
            blocker = "caption-complete-candidate-missing"
        results.append(
            {
                "id": representative_id,
                "sourceStems": [manifest["sourceStem"] for manifest in matching],
                "matchedTerms": matched_terms,
                "candidateIds": sorted(candidate_ids),
                "fragmentationDetected": sorted(set(fragmentation)),
                "axisOmissionsDetected": sorted(set(axis_omissions)),
                "neighborMergesDetected": sorted(set(neighbor_merges)),
                "decorativePortraitRetention": sorted(set(decorative_retention)),
                "blocker": blocker,
            }
        )
    return results


def _audit_manifest(manifest: dict, expected_hash: str, expected_page_count: int, out_dir: Path) -> list[str]:
    errors = []
    if manifest.get("sourceSha256") != expected_hash:
        errors.append(f"{manifest.get('sourceStem')}:manifest-source-hash")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != expected_page_count:
        return errors + [f"{manifest.get('sourceStem')}:page-count"]
    for index, page in enumerate(pages, start=1):
        if page.get("sourcePage") != index:
            errors.append(f"{manifest.get('sourceStem')}:page-number:{index}")
        if page.get("sourceSha256") != expected_hash:
            errors.append(f"{manifest.get('sourceStem')}:page-source-hash:{index}")
        for field in ("bounds",):
            if not _normalized_bounds_valid(page.get(field)):
                errors.append(f"{manifest.get('sourceStem')}:page-{field}:{index}")
        for record in [*page.get("textBlocks", []), *page.get("visualAtoms", []), *page.get("visualCandidates", [])]:
            if record.get("sourcePage") != index or record.get("sourceSha256") != expected_hash:
                errors.append(f"{manifest.get('sourceStem')}:record-provenance:{record.get('id')}")
            if not _normalized_bounds_valid(record.get("bounds")):
                errors.append(f"{manifest.get('sourceStem')}:record-bounds:{record.get('id')}")
        for candidate in page.get("visualCandidates", []):
            if not candidate.get("captionId") or not _nonempty_text(candidate.get("captionText")):
                errors.append(f"{manifest.get('sourceStem')}:candidate-caption:{candidate.get('id')}")
            if candidate.get("reviewRequired") is not True or candidate.get("reviewStatus") != "pending":
                errors.append(f"{manifest.get('sourceStem')}:candidate-review-gate:{candidate.get('id')}")
            if candidate.get("decision") != "exclude":
                if not _nonempty_text(candidate.get("supportedSection")):
                    errors.append(f"{manifest.get('sourceStem')}:candidate-section:{candidate.get('id')}")
                if not isinstance(candidate.get("decisionReasons"), list) or not candidate["decisionReasons"]:
                    errors.append(f"{manifest.get('sourceStem')}:candidate-reasons:{candidate.get('id')}")
            if candidate.get("embeddedTextStatus") == "unread" and candidate.get("decision") == "reconstruct":
                if candidate.get("reconstructionBlocked") is not True or "label-transcription-required" not in candidate.get("blockingReasons", []):
                    errors.append(f"{manifest.get('sourceStem')}:unread-reconstruction:{candidate.get('id')}")
            for field in ("cropPath", "reviewPacketPath"):
                if not isinstance(candidate.get(field), str) or Path(candidate[field]).is_absolute() or not (out_dir / candidate[field]).is_file():
                    errors.append(f"{manifest.get('sourceStem')}:artifact:{candidate.get('id')}:{field}")
    return errors


def extract_all_candidates(
    index_path: Path,
    pages_root: Path,
    out_dir: Path,
    source_root: Path = DEFAULT_SOURCE_ROOT,
) -> list[dict]:
    try:
        records = json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read source index: {index_path}") from error
    if not isinstance(records, list):
        raise ValueError("source index must contain a list")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "crops"
    review_dir = out_dir / "review"
    for directory in (crops_dir, review_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    results = []
    manifests = []
    reviews = []
    source_hash_map = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"source index record {index} must be an object")
        for field in ("sourceFile", "sourceStem", "sha256"):
            if not _nonempty_text(record.get(field)):
                raise ValueError(f"source index record {index} missing {field}")
        page_count = record.get("pageCount")
        if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
            raise ValueError(f"source index record {index} pageCount invalid")
        pdf = _find_source_pdf(Path(source_root), record["sourceFile"])
        source_hash = _sha256(pdf)
        if source_hash != record["sha256"]:
            raise ValueError(f"source hash mismatch for {record['sourceFile']}")
        rendered_dir = Path(pages_root) / record["sourceStem"]
        pages = extract_page_evidence(pdf, rendered_dir)
        if len(pages) != page_count:
            raise ValueError(f"source page count mismatch for {record['sourceFile']}")
        for page in pages:
            page["supportedSection"] = record["sourceStem"]
            page["context"] = {"supportedSection": record["sourceStem"]}
        evidence = _canonical_lesson_evidence(record["sourceStem"], pages)
        for page in pages:
            candidates = assemble_composite_figures(page)
            ranked_candidates = []
            for local_candidate in candidates:
                local_id = local_candidate["id"]
                local_candidate["localCandidateId"] = local_id
                local_candidate["id"] = _safe_candidate_id(record["sourceStem"], page["sourcePage"], local_id)
                text_by_id = {
                    block["id"]: block
                    for block in page.get("textBlocks", [])
                    if isinstance(block, dict) and _nonempty_text(block.get("id"))
                }
                local_candidate["relatedTextIds"] = [
                    text_id
                    for text_id in local_candidate.get("relatedTextIds", [])
                    if text_by_id.get(text_id, {}).get("role") in {None, "body"}
                ]
                local_candidate["internalTextIds"] = [
                    text_id
                    for text_id in local_candidate.get("internalTextIds", [])
                    if text_by_id.get(text_id, {}).get("role") == "figureInternal"
                ]
                linked = link_canonical_candidate_context(page, local_candidate)
                ranked = rank_canonical_visual_candidate(linked, evidence)
                ranked["reconstructionBlocked"] = (
                    "label-transcription-required" in ranked.get("blockingReasons", [])
                )
                safe_id = ranked["id"]
                crop_path = Path("crops") / f"{safe_id}.png"
                review_path = Path("review") / f"{safe_id}.png"
                rendered_page = rendered_dir / page["renderedPage"]
                _write_candidate_crop(rendered_page, ranked["bounds"], out_dir / crop_path)
                render_review_packet(ranked, rendered_page, out_dir / review_path)
                ranked["cropPath"] = crop_path.as_posix()
                ranked["reviewPacketPath"] = review_path.as_posix()
                ranked_candidates.append(ranked)
                reviews.append(
                    {
                        "candidateId": ranked["id"],
                        "sourceStem": record["sourceStem"],
                        "sourcePage": ranked["sourcePage"],
                        "sourceSha256": ranked["sourceSha256"],
                        "bounds": ranked["bounds"],
                        "status": "pending",
                        "checks": {name: False for name in REQUIRED_CHECKS},
                        "transcribedLabels": [],
                        "notes": "",
                    }
                )
            page["visualCandidates"] = ranked_candidates
        manifest = {
            "sourceFile": record["sourceFile"],
            "sourceStem": record["sourceStem"],
            "sourceSha256": source_hash,
            "pageCount": len(pages),
            "pages": pages,
        }
        output_path = out_dir / f"{record['sourceStem']}.json"
        output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifests.append(manifest)
        source_hash_map[record["sourceStem"]] = source_hash
        retained = sum(
            candidate["decision"] != "exclude"
            for page in pages
            for candidate in page["visualCandidates"]
        )
        results.append({"sourceFile": record["sourceFile"], "pages": len(pages), "retained": retained})
    audit_errors = []
    for record, manifest in zip(records, manifests):
        audit_errors.extend(
            _audit_manifest(manifest, record["sha256"], record["pageCount"], out_dir)
        )
    all_candidates = [
        candidate
        for manifest in manifests
        for page in manifest["pages"]
        for candidate in page["visualCandidates"]
    ]
    audit = {
        "manifestCount": len(manifests),
        "pageCount": sum(manifest["pageCount"] for manifest in manifests),
        "candidateCount": len(all_candidates),
        "retainedCandidateCount": sum(candidate["decision"] != "exclude" for candidate in all_candidates),
        "reviewCount": len(reviews),
        "cropCount": len(list(crops_dir.glob("*.png"))),
        "reviewPacketCount": len(list(review_dir.glob("*.png"))),
        "sourceHashMap": source_hash_map,
        "invariants": {
            "allSourceHashesValid": not any("hash" in error for error in audit_errors),
            "allPageCountsValid": not any("page-count" in error or "page-number" in error for error in audit_errors),
            "allBoundsNormalized": not any("bounds" in error for error in audit_errors),
            "allProvenanceValid": not any("provenance" in error for error in audit_errors),
            "allCandidatesCaptionComplete": not any("candidate-caption" in error for error in audit_errors),
            "allRetainedReviewGated": not any("candidate-review-gate" in error for error in audit_errors),
            "allCandidatesReviewRequired": not any("candidate-review-gate" in error for error in audit_errors),
            "allRetainedDecisionReasonsPresent": not any("candidate-reasons" in error for error in audit_errors),
            "allRetainedSupportedSectionsPresent": not any("candidate-section" in error for error in audit_errors),
            "allUnreadReconstructionsBlocked": not any("unread-reconstruction" in error for error in audit_errors),
            "allArtifactsPresent": not any("artifact" in error for error in audit_errors),
            "deterministicSerialization": True,
        },
        "errors": audit_errors,
        "representatives": _audit_representatives(manifests),
    }
    (out_dir / "reviews.json").write_text(
        json.dumps(reviews, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
    candidates_parser = subparsers.add_parser("candidates-all")
    candidates_parser.add_argument("--index", type=Path, required=True)
    candidates_parser.add_argument("--pages", type=Path, required=True)
    candidates_parser.add_argument("--out", type=Path, required=True)
    candidates_parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
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
    elif args.command == "candidates-all":
        for result in extract_all_candidates(args.index, args.pages, args.out, args.source_root):
            print(f"{result['sourceFile']}: {result['pages']} pages, {result['retained']} retained")


if __name__ == "__main__":
    main()
