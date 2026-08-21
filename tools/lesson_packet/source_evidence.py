"""Immutable, deterministic evidence extracted directly from source PDF pages."""

import hashlib
import math
import re
from pathlib import Path
from typing import Iterable

import pdfplumber


LABEL_CORE = (
    r"(?:그림|표|그래프|Figure|Fig\.?|Table)\s*"
    r"(?:[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*)?[-－–—]?\s*[0-9]+(?:[.-][0-9]+)?"
)
CAPTION_LABEL_RE = re.compile(rf"(?P<label>{LABEL_CORE})", re.IGNORECASE)


def _path(value: object, label: str) -> Path:
    try:
        return Path(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a path") from error


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest for an existing regular file."""
    path = _path(path, "source file")
    if not path.is_file():
        raise ValueError(f"source file not found: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ValueError(f"could not read source file: {path}") from error
    return digest.hexdigest()


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def normalize_bounds(bounds, page_width, page_height) -> tuple[float, float, float, float]:
    """Clip a PDF-space rectangle to a page and return normalized bounds."""
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise ValueError("bounds must contain [left, top, right, bottom]")
    left, top, right, bottom = (_finite_number(value, "bounds value") for value in bounds)
    width = _finite_number(page_width, "page width")
    height = _finite_number(page_height, "page height")
    if width <= 0 or height <= 0:
        raise ValueError("page dimensions must be positive")
    left = max(0.0, min(left, width))
    right = max(0.0, min(right, width))
    top = max(0.0, min(top, height))
    bottom = max(0.0, min(bottom, height))
    if left >= right or top >= bottom:
        raise ValueError("bounds must have positive area within the page")
    return tuple(round(value, 6) for value in (left / width, top / height, right / width, bottom / height))


def _match_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


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
    return _match_text(match.group("label")).rstrip(".:-")


def _word_box(word: object) -> tuple[float, float, float, float] | None:
    if not isinstance(word, dict) or not isinstance(word.get("text"), str):
        return None
    try:
        box = tuple(float(word[key]) for key in ("x0", "top", "x1", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in box) or box[0] >= box[2] or box[1] >= box[3]:
        return None
    return box


def _line_records(page, source_page: int) -> list[dict]:
    try:
        extracted = page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False,
        )
    except Exception as error:
        raise ValueError(f"could not extract positioned text on page {source_page}") from error
    if not isinstance(extracted, list):
        raise ValueError(f"positioned text is invalid on page {source_page}")
    words = []
    for word in extracted:
        box = _word_box(word)
        if box is not None:
            words.append({"text": word["text"], "box": box})
    words.sort(key=lambda word: (round(word["box"][1], 3), word["box"][0], word["text"]))

    lines: list[list[dict]] = []
    for word in words:
        if not lines:
            lines.append([word])
            continue
        current = lines[-1]
        baseline_close = abs(word["box"][1] - current[0]["box"][1]) <= 2.5
        horizontal_gap = word["box"][0] - current[-1]["box"][2]
        height = word["box"][3] - word["box"][1]
        if baseline_close and horizontal_gap <= max(18.0, height):
            current.append(word)
        else:
            lines.append([word])

    records = []
    for line in lines:
        text = " ".join(word["text"] for word in line)
        if not _match_text(text):
            continue
        records.append(
            {
                "text": text,
                "matchText": _match_text(text),
                "box": _box_union(word["box"] for word in line),
            }
        )
    return records


def _boxes_horizontally_related(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    overlap = min(first[2], second[2]) - max(first[0], second[0])
    return overlap > 0 or abs(first[0] - second[0]) <= 36


def _cluster_text_blocks(page, source_page: int) -> list[dict]:
    blocks: list[dict] = []
    for line in _line_records(page, source_page):
        role = "caption" if _caption_label(line["matchText"]) else "body"
        if blocks:
            previous = blocks[-1]
            gap = line["box"][1] - previous["box"][3]
            line_height = line["box"][3] - line["box"][1]
            if (
                role == previous["role"]
                and -1 <= gap <= max(4.0, line_height * 0.75)
                and _boxes_horizontally_related(previous["box"], line["box"])
            ):
                previous["text"] += "\n" + line["text"]
                previous["matchText"] = _match_text(previous["text"])
                previous["box"] = _box_union((previous["box"], line["box"]))
                continue
        blocks.append({**line, "role": role})
    return blocks


def _box_union(boxes: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    values = list(boxes)
    return (
        min(box[0] for box in values),
        min(box[1] for box in values),
        max(box[2] for box in values),
        max(box[3] for box in values),
    )


def _object_box(obj: object) -> tuple[float, float, float, float] | None:
    if not isinstance(obj, dict):
        return None
    try:
        left, top, right, bottom = (float(obj[key]) for key in ("x0", "top", "x1", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        return None
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    if right - left < 1:
        midpoint = (left + right) / 2
        left, right = midpoint - 0.5, midpoint + 0.5
    if bottom - top < 1:
        midpoint = (top + bottom) / 2
        top, bottom = midpoint - 0.5, midpoint + 0.5
    return left, top, right, bottom


def _boxes_close(first: tuple[float, float, float, float], second: tuple[float, float, float, float], gap: float = 6) -> bool:
    return not (
        first[2] + gap < second[0]
        or second[2] + gap < first[0]
        or first[3] + gap < second[1]
        or second[3] + gap < first[1]
    )


def _box_intersects_page(box: tuple[float, float, float, float], width: float, height: float) -> bool:
    return box[2] > 0 and box[3] > 0 and box[0] < width and box[1] < height


def _vector_groups(page, table_boxes: list[tuple[float, float, float, float]]) -> list[tuple[tuple[float, float, float, float], int]]:
    objects = []
    for obj in list(page.lines) + list(page.rects) + list(page.curves):
        box = _object_box(obj)
        if box is None:
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
        result.append((box, len(group)))
    return sorted(result, key=lambda item: (item[0][1], item[0][0], item[0][3], item[0][2], item[1]))


def _page_dimensions(page, source_page: int) -> tuple[float, float]:
    try:
        width = float(page.width)
        height = float(page.height)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"invalid page dimensions on page {source_page}") from error
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError(f"invalid page dimensions on page {source_page}")
    return width, height


def _base_record(record_id: str, source_page: int, source_hash: str, bounds: tuple[float, float, float, float]) -> dict:
    return {
        "id": record_id,
        "sourcePage": source_page,
        "sourceSha256": source_hash,
        "bounds": bounds,
    }


def _text_blocks(page, source_page: int, source_hash: str, width: float, height: float) -> list[dict]:
    blocks = []
    role_counts = {"body": 0, "caption": 0}
    for block in sorted(_cluster_text_blocks(page, source_page), key=lambda item: (item["box"][1], item["box"][0], item["box"][3], item["box"][2], item["matchText"])):
        if not _box_intersects_page(block["box"], width, height):
            continue
        role_counts[block["role"]] += 1
        slug = "caption" if block["role"] == "caption" else "text"
        blocks.append(
            {
                **_base_record(
                    f"page-{source_page:03d}-{slug}-{role_counts[block['role']]:03d}",
                    source_page,
                    source_hash,
                    normalize_bounds(block["box"], width, height),
                ),
                "text": block["text"],
                "matchText": block["matchText"],
                "role": block["role"],
            }
        )
    return blocks


def _visual_atoms(page, source_page: int, source_hash: str, width: float, height: float) -> list[dict]:
    table_boxes = []
    atoms = []
    try:
        tables = page.find_tables()
    except Exception:
        tables = []
    for table in tables:
        try:
            box = tuple(float(value) for value in table.bbox)
        except (AttributeError, TypeError, ValueError):
            continue
        if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
            continue
        table_boxes.append(box)
        atoms.append({"kind": "table", "box": box, "objectCount": len(table.cells)})
    for image in page.images:
        box = _object_box(image)
        if box is not None:
            atoms.append({"kind": "nativeImage", "box": box, "objectCount": 1})
    atoms.extend(
        {"kind": "vectorGroup", "box": box, "objectCount": count}
        for box, count in _vector_groups(page, table_boxes)
    )
    atoms = [atom for atom in atoms if _box_intersects_page(atom["box"], width, height)]
    atoms.sort(key=lambda atom: (atom["box"][1], atom["box"][0], atom["box"][3], atom["box"][2], atom["kind"], atom["objectCount"]))
    counts: dict[str, int] = {}
    slugs = {"nativeImage": "native-image", "table": "table", "vectorGroup": "vector-group"}
    records = []
    for atom in atoms:
        slug = slugs[atom["kind"]]
        counts[slug] = counts.get(slug, 0) + 1
        records.append(
            {
                **_base_record(
                    f"page-{source_page:03d}-{slug}-{counts[slug]:03d}",
                    source_page,
                    source_hash,
                    normalize_bounds(atom["box"], width, height),
                ),
                "kind": atom["kind"],
                "objectCount": atom["objectCount"],
            }
        )
    return records


def _extract_one_page(page, rendered_page: Path, source_hash: str, source_page: int) -> dict:
    if not rendered_page.is_file():
        raise ValueError(f"rendered page missing: {rendered_page}")
    width, height = _page_dimensions(page, source_page)
    return {
        "id": f"page-{source_page:03d}",
        "sourcePage": source_page,
        "sourceSha256": source_hash,
        "pageSize": (round(width, 6), round(height, 6)),
        "bounds": (0.0, 0.0, 1.0, 1.0),
        "renderedPage": rendered_page.name,
        "textBlocks": _text_blocks(page, source_page, source_hash, width, height),
        "visualAtoms": _visual_atoms(page, source_page, source_hash, width, height),
    }


def extract_page_evidence(pdf: Path, rendered_pages: Path) -> list[dict]:
    """Extract source-bound page evidence without figure or instructional classification."""
    pdf = _path(pdf, "source PDF")
    rendered_pages = _path(rendered_pages, "rendered page directory")
    if not pdf.is_file():
        raise ValueError(f"source PDF not found: {pdf}")
    if not rendered_pages.is_dir():
        raise ValueError(f"rendered page directory not found: {rendered_pages}")
    source_hash = sha256_file(pdf)
    try:
        document = pdfplumber.open(pdf)
    except Exception as error:
        raise ValueError(f"could not open source PDF: {pdf}") from error
    try:
        return [
            _extract_one_page(page, rendered_pages / f"page-{index:03d}.png", source_hash, index)
            for index, page in enumerate(document.pages, start=1)
        ]
    except ValueError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"could not extract source evidence from {pdf}") from error
    finally:
        document.close()
