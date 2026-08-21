"""Assemble complete, caption-anchored figure candidates from page evidence."""

import math
import re
from typing import Iterable

from tools.lesson_packet.source_evidence import _caption_label


DEFAULT_MAX_NORMALIZED_GAP = 0.08
CAPTION_PATTERN = re.compile(
    r"\[?\s*(?:그림|표|그래프|Figure|Fig\.?|Table)\s*"
    r"(?:[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*)?[-－–—]?\s*"
    r"[0-9]+(?:[.-][0-9]+)?",
    re.IGNORECASE,
)
FURNITURE_KINDS = {
    "pagefurniture",
    "page-furniture",
    "header",
    "footer",
    "pageheader",
    "page-footer",
    "page-number",
    "pagenumber",
    "runningheader",
    "runningfooter",
}
RASTER_KINDS = {"nativeimage", "native-image", "raster", "rasterimage", "raster-image"}
VALID_TEXT_ROLES = {"body", "caption", "figureInternal"}


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _bounds(value: object, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must contain four normalized values")
    result = tuple(_finite(item, f"{label} value") for item in value)
    if not (0 <= result[0] < result[2] <= 1 and 0 <= result[1] < result[3] <= 1):
        raise ValueError(f"{label} must be normalized [left, top, right, bottom]")
    return result


def _union(boxes: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    values = list(boxes)
    if not values:
        raise ValueError("cannot union empty bounds")
    return (
        min(box[0] for box in values),
        min(box[1] for box in values),
        max(box[2] for box in values),
        max(box[3] for box in values),
    )


def _intersects(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1])


def _distance(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    horizontal = max(first[0] - second[2], second[0] - first[2], 0.0)
    vertical = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(horizontal, vertical)


def _sort_key(record: dict, box: tuple[float, float, float, float]) -> tuple[float, float, float, float, str]:
    return box[1], box[0], box[3], box[2], str(record.get("id", ""))


def _kind(record: dict) -> str:
    return str(record.get("kind", "")).replace("_", "-").casefold()


def _is_furniture(record: dict) -> bool:
    return _kind(record) in FURNITURE_KINDS


def _caption_is_parseable(caption: dict) -> bool:
    text = caption.get("text", caption.get("matchText", ""))
    return isinstance(text, str) and bool(CAPTION_PATTERN.search(text)) and _caption_label(text) is not None


def _max_gap(page: dict) -> float:
    config = page.get("figureAssemblyConfig", {})
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("figureAssemblyConfig must be an object")
    value = config.get("maxNormalizedGap", page.get("maxNormalizedGap", DEFAULT_MAX_NORMALIZED_GAP))
    gap = _finite(value, "maxNormalizedGap")
    if gap < 0 or gap > 1:
        raise ValueError("maxNormalizedGap must be between 0 and 1")
    return gap


def _validate_page(page: dict) -> tuple[list[dict], list[dict], list[dict], float]:
    if not isinstance(page, dict):
        raise ValueError("page evidence must be an object")
    source_page = page.get("sourcePage")
    if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1:
        raise ValueError("page sourcePage must be one-based")
    source_hash = page.get("sourceSha256")
    if not isinstance(source_hash, str) or not source_hash.strip():
        raise ValueError("page sourceSha256 is required")
    text_blocks = page.get("textBlocks")
    atoms = page.get("visualAtoms")
    furniture = page.get("pageFurniture", [])
    if not isinstance(text_blocks, list):
        raise ValueError("page textBlocks must be a list")
    if not isinstance(atoms, list):
        raise ValueError("page visualAtoms must be a list")
    if not isinstance(furniture, list):
        raise ValueError("page pageFurniture must be a list")

    def validate_records(records: list[dict], label: str) -> None:
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"{label}[{index}] must be an object")
            if not isinstance(record.get("id"), str) or not record["id"].strip():
                raise ValueError(f"{label}[{index}] id is required")
            if record.get("sourcePage") != source_page:
                raise ValueError(f"{label}[{index}] sourcePage must match page")
            if record.get("sourceSha256") != source_hash:
                raise ValueError(f"{label}[{index}] sourceSha256 must match page")
            _bounds(record.get("bounds"), f"{label}[{index}] bounds")

    validate_records(text_blocks, "textBlocks")
    validate_records(atoms, "visualAtoms")
    validate_records(furniture, "pageFurniture")
    for index, block in enumerate(text_blocks):
        role = block.get("role")
        if role not in VALID_TEXT_ROLES:
            raise ValueError(f"textBlocks[{index}] role must be body, caption, or figureInternal")
    return text_blocks, atoms, furniture, _max_gap(page)


def _caption_records(text_blocks: list[dict]) -> list[tuple[dict, tuple[float, float, float, float]]]:
    captions = []
    for block in text_blocks:
        if block.get("role") != "caption":
            continue
        if not _caption_is_parseable(block):
            continue
        captions.append((block, _bounds(block["bounds"], f"caption {block['id']} bounds")))
    return sorted(captions, key=lambda item: _sort_key(item[0], item[1]))


def _connection_corridor(current: tuple[float, float, float, float], candidate: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return _union((current, candidate))


def _blocked_connection(
    current: tuple[float, float, float, float],
    candidate: tuple[float, float, float, float],
    current_caption: dict,
    captions: list[tuple[dict, tuple[float, float, float, float]]],
    barriers: list[tuple[dict, tuple[float, float, float, float]]],
) -> bool:
    corridor = _connection_corridor(current, candidate)
    for barrier, barrier_box in barriers:
        if _intersects(corridor, barrier_box) and not _intersects(current, barrier_box) and not _intersects(candidate, barrier_box):
            return True
    for caption, caption_box in captions:
        if caption is current_caption:
            continue
        if _intersects(corridor, caption_box) and not _intersects(current, caption_box) and not _intersects(candidate, caption_box):
            return True
    return False


def _owned_by_caption(
    atom_box: tuple[float, float, float, float],
    caption: dict,
    caption_box: tuple[float, float, float, float],
    captions: list[tuple[dict, tuple[float, float, float, float]]],
    caption_order: dict[str, int],
) -> bool:
    distance = _distance(atom_box, caption_box)
    for other, other_box in captions:
        if other is caption:
            continue
        other_distance = _distance(atom_box, other_box)
        if other_distance + 1e-9 < distance:
            return False
        if abs(other_distance - distance) <= 1e-9 and caption_order[other["id"]] < caption_order[caption["id"]]:
            return False
    return True


def _select_atoms(
    caption: dict,
    caption_box: tuple[float, float, float, float],
    atoms: list[dict],
    captions: list[tuple[dict, tuple[float, float, float, float]]],
    barriers: list[tuple[dict, tuple[float, float, float, float]]],
    caption_order: dict[str, int],
    max_gap: float,
) -> list[tuple[dict, tuple[float, float, float, float]]]:
    eligible = []
    for atom in atoms:
        if _is_furniture(atom):
            continue
        atom_box = _bounds(atom["bounds"], f"visual atom {atom['id']} bounds")
        if _owned_by_caption(atom_box, caption, caption_box, captions, caption_order):
            eligible.append((atom, atom_box))
    if not eligible:
        return []
    reachable = [
        (atom, box)
        for atom, box in eligible
        if _distance(box, caption_box) <= max_gap
        and not _blocked_connection(caption_box, box, caption, captions, barriers)
    ]
    if not reachable:
        return []
    anchor_distance = min(_distance(box, caption_box) for _, box in reachable)
    selected = [
        (atom, box)
        for atom, box in reachable
        if _distance(box, caption_box) <= max_gap and _distance(box, caption_box) <= anchor_distance + max_gap * 0.25 + 1e-9
    ]
    if not selected:
        return []

    selected_ids = {atom["id"] for atom, _ in selected}
    current = _union(box for _, box in selected)
    while True:
        additions = []
        for atom, atom_box in eligible:
            if atom["id"] in selected_ids or _distance(current, atom_box) > max_gap:
                continue
            if _blocked_connection(current, atom_box, caption, captions, barriers):
                continue
            additions.append((
                _distance(current, atom_box),
                _sort_key(atom, atom_box),
                atom,
                atom_box,
            ))
        if not additions:
            break
        _, _, atom, atom_box = min(additions)
        selected.append((atom, atom_box))
        selected_ids.add(atom["id"])
        current = _union((current, atom_box))
    return sorted(selected, key=lambda item: _sort_key(item[0], item[1]))


ATTACHED_TEXT_GAP = 0.035
PANEL_GAP = 0.05


def _body_barrier_ids(
    text_blocks: list[dict],
    atoms: list[dict],
    furniture: list[dict],
    max_gap: float,
) -> set[str]:
    """Identify body blocks that geometrically separate visual regions."""
    atom_boxes = [
        _bounds(atom["bounds"], f"visual atom {atom['id']} bounds")
        for atom in atoms
        if not _is_furniture(atom)
    ]
    barrier_ids = {
        block["id"]
        for block in text_blocks
        if block.get("role") == "body" and block.get("isBarrier") is True
    }
    for block in text_blocks:
        if block.get("role") != "body" or block["id"] in barrier_ids:
            continue
        block_box = _bounds(block["bounds"], f"body text block {block['id']} bounds")
        for index, first in enumerate(atom_boxes):
            for second in atom_boxes[index + 1:]:
                if _distance(first, second) > max_gap:
                    continue
                corridor = _connection_corridor(first, second)
                if _intersects(corridor, block_box) and not _intersects(first, block_box) and not _intersects(second, block_box):
                    barrier_ids.add(block["id"])
                    break
            if block["id"] in barrier_ids:
                break
        if block["id"] in barrier_ids:
            continue
        if atom_boxes and all(_distance(block_box, atom_box) > ATTACHED_TEXT_GAP for atom_box in atom_boxes):
            barrier_ids.add(block["id"])
    return barrier_ids


def _classify_text(
    text_blocks: list[dict],
    selected_atoms: list[tuple[dict, tuple[float, float, float, float]]],
    body_barrier_ids: set[str],
) -> tuple[list[str], tuple[float, float, float, float], set[str]]:
    atom_boxes = [box for _, box in selected_atoms]
    crop = _union(atom_boxes)
    internal: list[tuple[dict, tuple[float, float, float, float]]] = []
    for block in text_blocks:
        if block.get("role") == "caption":
            continue
        block_box = _bounds(block["bounds"], f"text block {block['id']} bounds")
        if block["id"] in body_barrier_ids:
            continue
        intersects_atom = any(_intersects(block_box, atom_box) for atom_box in atom_boxes)
        inside_crop = _intersects(block_box, crop)
        attached_label = any(_distance(block_box, atom_box) <= ATTACHED_TEXT_GAP for atom_box in atom_boxes)
        if intersects_atom or inside_crop or attached_label:
            internal.append((block, block_box))
            crop = _union((crop, block_box))
    internal.sort(key=lambda item: _sort_key(item[0], item[1]))
    internal_ids = {block["id"] for block, _ in internal}
    for block, _ in internal:
        block["role"] = "figureInternal"
    return [block["id"] for block, _ in internal], crop, internal_ids


def _related_text_ids(
    text_blocks: list[dict],
    crop: tuple[float, float, float, float],
    internal_ids: set[str],
    max_gap: float,
) -> list[str]:
    related = []
    for block in text_blocks:
        if block.get("role") != "body" or block["id"] in internal_ids:
            continue
        block_box = _bounds(block["bounds"], f"text block {block['id']} bounds")
        if _distance(block_box, crop) <= max_gap:
            related.append((block, block_box))
    return [block["id"] for block, _ in sorted(related, key=lambda item: _sort_key(item[0], item[1]))]


def _supported_section(page: dict) -> str | None:
    context = page.get("context")
    if not isinstance(context, dict):
        context = page.get("lessonContext")
    values = [page.get("supportedSection"), page.get("section")]
    if isinstance(context, dict):
        values.extend((context.get("supportedSection"), context.get("section")))
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError("supportedSection must be text")
        if value.strip():
            return value.strip()
    return None


def _panel_metadata(
    candidate_id: str,
    selected_atoms: list[tuple[dict, tuple[float, float, float, float]]],
    text_blocks: list[dict],
    internal_ids: set[str],
    source_page: int,
    source_hash: str,
    max_gap: float,
) -> list[dict]:
    remaining = list(selected_atoms)
    components: list[list[tuple[dict, tuple[float, float, float, float]]]] = []
    panel_gap = min(PANEL_GAP, max_gap)
    while remaining:
        seed = min(remaining, key=lambda item: _sort_key(item[0], item[1]))
        remaining.remove(seed)
        component = [seed]
        changed = True
        while changed:
            changed = False
            for item in list(remaining):
                if any(_intersects(item[1], member[1]) or _distance(item[1], member[1]) <= panel_gap for member in component):
                    component.append(item)
                    remaining.remove(item)
                    changed = True
        components.append(sorted(component, key=lambda item: _sort_key(item[0], item[1])))
    components.sort(key=lambda component: _sort_key(component[0][0], component[0][1]))

    text_by_id = {block["id"]: block for block in text_blocks if block["id"] in internal_ids}
    panels = []
    for index, component in enumerate(components, start=1):
        atom_boxes = [box for _, box in component]
        panel_bounds = _union(atom_boxes)
        panel_text = []
        for block in text_by_id.values():
            text_box = _bounds(block["bounds"], f"text block {block['id']} bounds")
            if _intersects(text_box, panel_bounds) or _distance(text_box, panel_bounds) <= ATTACHED_TEXT_GAP:
                panel_text.append((block, text_box))
        panel_text.sort(key=lambda item: _sort_key(item[0], item[1]))
        panel_bounds = _union((panel_bounds, *(box for _, box in panel_text)))
        panels.append(
            {
                "id": f"{candidate_id}-panel-{index:03d}",
                "sourcePage": source_page,
                "sourceSha256": source_hash,
                "bounds": [round(value, 6) for value in panel_bounds],
                "visualAtomIds": [atom["id"] for atom, _ in component],
                "internalTextIds": [block["id"] for block, _ in panel_text],
            }
        )
    return panels


def _embedded_text_status(selected_atoms: list[tuple[dict, tuple[float, float, float, float]]], internal_ids: list[str]) -> str:
    statuses = {str(atom.get("embeddedTextStatus", "")).casefold() for atom, _ in selected_atoms}
    if "unread" in statuses or any(_kind(atom) in RASTER_KINDS for atom, _ in selected_atoms):
        return "unread"
    if "known" in statuses or internal_ids:
        return "known"
    return "none"


def assemble_composite_figures(page: dict) -> list[dict]:
    """Return deterministic, complete figure candidates for one canonical page."""
    text_blocks, atoms, furniture, max_gap = _validate_page(page)
    captions = _caption_records(text_blocks)
    caption_order = {caption["id"]: index for index, (caption, _) in enumerate(captions)}
    body_barrier_ids = _body_barrier_ids(text_blocks, atoms, furniture, max_gap)
    barriers = [(record, _bounds(record["bounds"], f"page furniture {record['id']} bounds")) for record in furniture]
    barriers.extend(
        (atom, _bounds(atom["bounds"], f"visual atom {atom['id']} bounds"))
        for atom in atoms
        if _is_furniture(atom)
    )
    barriers.extend(
        (block, _bounds(block["bounds"], f"body text block {block['id']} bounds"))
        for block in text_blocks
        if block["id"] in body_barrier_ids
    )
    supported_section = _supported_section(page)
    candidates = []
    for index, (caption, caption_box) in enumerate(captions, start=1):
        selected_atoms = _select_atoms(caption, caption_box, atoms, captions, barriers, caption_order, max_gap)
        if not selected_atoms:
            continue
        internal_ids, crop, internal_id_set = _classify_text(text_blocks, selected_atoms, body_barrier_ids)
        related_ids = _related_text_ids(text_blocks, crop, internal_id_set, max_gap)
        candidate_id = f"visual-candidate-{index:03d}"
        candidates.append(
            {
                "id": candidate_id,
                "sourcePage": page["sourcePage"],
                "sourceSha256": page["sourceSha256"],
                "bounds": [round(value, 6) for value in crop],
                "captionId": caption["id"],
                "relatedTextIds": related_ids,
                "internalTextIds": internal_ids,
                "visualAtomIds": [atom["id"] for atom, _ in selected_atoms],
                "embeddedTextStatus": _embedded_text_status(selected_atoms, internal_ids),
                "reviewRequired": True,
                "supportedSection": supported_section,
                "retained": supported_section is not None,
                "reviewStatus": "pending" if supported_section is not None else "pending-context",
                "panels": _panel_metadata(
                    candidate_id,
                    selected_atoms,
                    text_blocks,
                    internal_id_set,
                    page["sourcePage"],
                    page["sourceSha256"],
                    max_gap,
                ),
            }
        )
    return candidates
