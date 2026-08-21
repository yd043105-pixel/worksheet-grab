"""Link source visual context and make provisional instructional decisions.

This module only ranks already extracted source evidence.  It does not render
pages, inspect pixels, or perform OCR.
"""

import copy
import math
import re
from collections.abc import Iterable


_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(
    r"(?:그림|표|그래프|Figure|Fig\.?|Table)\s*"
    r"(?:[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*)?[-－–—]?\s*\d+(?:[.-]\d+)?",
    re.IGNORECASE,
)
_STOP_TERMS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is", "of", "on",
    "or", "shows", "the", "to", "with", "그림", "표", "그래프", "figure", "fig", "table",
}
_SECTION_FIELDS = ("sections", "entities", "quantities", "explicitReferences")
_TEXT_ROLES = {"body", "caption", "figureInternal"}
_EMBEDDED_TEXT_STATUSES = {"known", "unread", "none"}
_HAZARD_ALIASES = (
    ("answerLeakage", "answer-leakage", ("answerLeakage", "answer_leakage", "answersPresent", "containsAnswer")),
    ("needsAnnotation", "annotation-needed", ("needsAnnotation", "requiredAnnotation", "annotationNeeded", "requiresAnnotation")),
    ("poorPrint", "poor-print", ("poorPrint", "poorPrintLegibility", "poor_print", "lowResolution", "illegible")),
    ("incompleteCrop", "incomplete-crop", ("incompleteCrop", "cropLosesMeaning", "cropIncomplete", "incomplete_crop")),
)


def _require_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _bounds(value: object, label: str = "bounds") -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must contain four normalized values")
    result = [_finite(item, f"{label} value") for item in value]
    if not (0 <= result[0] < result[2] <= 1 and 0 <= result[1] < result[3] <= 1):
        raise ValueError(f"{label} must be normalized [left, top, right, bottom]")
    return list(value)


def _source_trace(record: dict, label: str, required: bool = True) -> tuple[int | None, str | None]:
    source_page = record.get("sourcePage")
    source_hash = record.get("sourceSha256")
    if source_page is None and source_hash is None and not required:
        return None, None
    if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1:
        raise ValueError(f"{label} sourcePage must be one-based")
    if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
        raise ValueError(f"{label} sourceSha256 must be lowercase 64-hex SHA-256")
    return source_page, source_hash


def _validate_candidate(candidate: dict, label: str = "visual candidate") -> None:
    _require_object(candidate, label)
    if not _nonempty_text(candidate.get("id")):
        raise ValueError(f"{label} id is required")
    _source_trace(candidate, label)
    _bounds(candidate.get("bounds"), f"{label} bounds")


def _validate_page(page: dict) -> tuple[int, str, list[dict]]:
    _require_object(page, "page evidence")
    source_page, source_hash = _source_trace(page, "page evidence")
    text_blocks = page.get("textBlocks", page.get("captionBlocks", []))
    if not isinstance(text_blocks, list):
        raise ValueError("page evidence textBlocks must be a list")
    for index, block in enumerate(text_blocks):
        _require_object(block, f"page textBlocks[{index}]")
        if not _nonempty_text(block.get("id")):
            raise ValueError(f"page textBlocks[{index}] id is required")
        block_page, block_hash = _source_trace(block, f"page textBlocks[{index}]")
        if block_page != source_page:
            raise ValueError(f"page textBlocks[{index}] sourcePage must match page")
        if block_hash != source_hash:
            raise ValueError(f"page textBlocks[{index}] sourceSha256 must match page")
        _bounds(block.get("bounds"), f"page textBlocks[{index}] bounds")
        if block.get("role") not in _TEXT_ROLES:
            raise ValueError(f"page textBlocks[{index}] role must be body, caption, or figureInternal")
    return source_page, source_hash, text_blocks


def _text(block: dict) -> str:
    value = block.get("text", block.get("matchText", ""))
    return value if isinstance(value, str) else ""


def _supported_section(value: object, label: str = "supportedSection") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    return value or None


def _context_section(source: dict) -> str | None:
    for key in ("supportedSection", "section"):
        section = _supported_section(source.get(key), key)
        if section:
            return section
    for key in ("lessonContext", "context"):
        context = source.get(key)
        if isinstance(context, dict):
            for nested_key in ("supportedSection", "section"):
                section = _supported_section(context.get(nested_key), nested_key)
                if section:
                    return section
    return None


def _id_list(candidate: dict, field: str) -> list[str]:
    value = candidate.get(field, [])
    if not isinstance(value, list) or any(not _nonempty_text(item) for item in value):
        raise ValueError(f"visual candidate {field} must be a list of text ids")
    return list(value)


def _index_text(text_blocks: list[dict]) -> dict[str, dict]:
    indexed = {}
    for block in text_blocks:
        block_id = block["id"]
        if block_id in indexed:
            raise ValueError(f"duplicate page text block id: {block_id}")
        indexed[block_id] = block
    return indexed


def _resolve(
    indexed: dict[str, dict],
    ids: list[str],
    field: str,
    expected_role: str | None = None,
) -> list[dict]:
    missing = sorted(set(ids) - set(indexed))
    if missing:
        raise ValueError(f"visual candidate {field} references unknown text id: {missing[0]}")
    if expected_role is not None:
        wrong_role = sorted(
            block_id for block_id in ids if indexed[block_id].get("role") != expected_role
        )
        if wrong_role:
            raise ValueError(
                f"visual candidate {field} must reference {expected_role} text: {wrong_role[0]}"
            )
    return [copy.deepcopy(indexed[item]) for item in ids]


def _reference_key(text: str) -> tuple[str, str] | None:
    match = _REFERENCE_RE.search(text)
    if not match:
        return None
    words = _TOKEN_RE.findall(match.group(0).casefold())
    numbers = [word for word in words if word.isdigit()]
    if not numbers:
        return None
    kind = "table" if words and words[0] in {"표", "table"} else "figure"
    return kind, numbers[-1]


def _unique_texts(values: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value.replace("\u00ad", "")).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def link_candidate_context(page: dict, candidate: dict) -> dict:
    """Attach page text records to a composite visual candidate.

    The returned candidate is a deep copy.  Source page, source hash, and crop
    bounds are validated and retained verbatim; linked text is source text, not
    OCR output.
    """
    source_page, source_hash, text_blocks = _validate_page(page)
    _require_object(candidate, "visual candidate")
    result = copy.deepcopy(candidate)
    if "sourcePage" not in result:
        result["sourcePage"] = source_page
    if "sourceSha256" not in result:
        result["sourceSha256"] = source_hash
    _validate_candidate(result)
    if result["sourcePage"] != source_page or result["sourceSha256"] != source_hash:
        raise ValueError("visual candidate source trace must match page")

    indexed = _index_text(text_blocks)
    caption_ids = []
    if result.get("captionId") is not None:
        if not _nonempty_text(result["captionId"]):
            raise ValueError("visual candidate captionId must be text")
        caption_ids = [result["captionId"]]
    related_ids = _id_list(result, "relatedTextIds")
    internal_ids = _id_list(result, "internalTextIds")
    caption_records = _resolve(indexed, caption_ids, "captionId", expected_role="caption")
    related_records = _resolve(indexed, related_ids, "relatedTextIds", expected_role="body")
    internal_records = _resolve(indexed, internal_ids, "internalTextIds", expected_role="figureInternal")

    if caption_records:
        caption = caption_records[0]
        if caption.get("role") not in {None, "caption"}:
            raise ValueError("visual candidate captionId must reference a caption")
        result["captionText"] = _text(caption)
    result["relatedText"] = related_records
    result["internalText"] = internal_records

    existing_references = result.get("explicitReferences", [])
    if not isinstance(existing_references, list) or any(not isinstance(item, str) for item in existing_references):
        raise ValueError("visual candidate explicitReferences must be a list of text")
    reference_key = _reference_key(result.get("captionText", ""))
    body_references = []
    for block in text_blocks:
        if block.get("role") not in {None, "body"}:
            continue
        if reference_key is not None and _reference_key(_text(block)) == reference_key:
            body_references.append(block)
    body_references.sort(key=lambda block: (str(block.get("id")), _text(block)))
    result["bodyReferenceIds"] = [block["id"] for block in body_references]
    result["explicitReferences"] = _unique_texts(
        [*existing_references, *[_text(block) for block in body_references]]
    )
    result["supportedSection"] = (
        _supported_section(result.get("supportedSection"), "supportedSection")
        or _context_section(page)
    )
    return result


def _flatten_strings(value: object, label: str, *, include_keys: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) or isinstance(value, tuple):
        result = []
        for item in value:
            result.extend(_flatten_strings(item, label, include_keys=include_keys))
        return result
    if isinstance(value, dict):
        result = []
        for key in sorted(value, key=str):
            if include_keys and isinstance(key, str):
                result.append(key)
            result.extend(_flatten_strings(value[key], label, include_keys=include_keys))
        return result
    if value is None:
        return []
    raise ValueError(f"{label} must contain text, lists, or objects")


def _terms(values: Iterable[str]) -> set[str]:
    terms = set()
    for value in values:
        normalized = value.replace("\u00ad", "").casefold()
        terms.update(
            token for token in _TOKEN_RE.findall(normalized)
            if len(token) > 1 and token not in _STOP_TERMS
        )
    return terms


def _candidate_texts(candidate: dict) -> list[str]:
    def candidate_values(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            result = []
            for item in value:
                result.extend(candidate_values(item))
            return result
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                return [value["text"]]
            if isinstance(value.get("matchText"), str):
                return [value["matchText"]]
            result = []
            for key in sorted(value, key=str):
                item = value[key]
                if isinstance(item, (str, list, tuple, dict)):
                    result.extend(candidate_values(item))
            return result
        return []

    fields = (
        "captionText", "caption", "explicitReferences", "nearbyText", "relatedText", "internalText",
        "text", "entities", "quantities", "purpose", "instructionalRole",
    )
    values = []
    for field in fields:
        if field == "caption" and "captionText" in candidate:
            continue
        if field not in candidate:
            continue
        values.extend(candidate_values(candidate[field]))
    return values


def _section_records(sections: object) -> list[tuple[str, list[str]]]:
    if isinstance(sections, dict):
        return [(str(key), _flatten_strings(sections[key], f"lesson evidence sections[{key}]", include_keys=False)) for key in sorted(sections, key=str)]
    if isinstance(sections, list):
        records = []
        for index, item in enumerate(sections):
            if not isinstance(item, dict):
                raise ValueError(f"lesson evidence sections[{index}] must be an object")
            name = item.get("id", item.get("name", item.get("section")))
            if not _nonempty_text(name):
                raise ValueError(f"lesson evidence sections[{index}] name is required")
            records.append((name.strip(), _flatten_strings(item, f"lesson evidence sections[{index}]")))
        return sorted(records, key=lambda item: item[0])
    raise ValueError("lesson evidence sections must be an object or list")


def _validate_lesson_evidence(lesson_evidence: dict) -> tuple[list[tuple[str, list[str]]], dict[str, list[str]]]:
    _require_object(lesson_evidence, "lesson evidence")
    sections = _section_records(lesson_evidence.get("sections"))
    values = {}
    for field in _SECTION_FIELDS[1:]:
        if field not in lesson_evidence:
            raise ValueError(f"lesson evidence {field} is required")
        values[field] = _flatten_strings(lesson_evidence[field], f"lesson evidence {field}")
    return sections, values


def _matching_terms(candidate_terms: set[str], values: Iterable[str]) -> set[str]:
    return candidate_terms & _terms(values)


def _active_hazards(candidate: dict) -> list[str]:
    nested = candidate.get("hazards", {})
    if not isinstance(nested, dict):
        raise ValueError("visual candidate hazards must be an object")
    allowed_keys = {
        alias
        for _, _, aliases in _HAZARD_ALIASES
        for alias in aliases
    }
    unknown_keys = sorted((str(key) for key in nested if key not in allowed_keys))
    if unknown_keys:
        raise ValueError(f"visual candidate hazard invalid: {unknown_keys[0]}")
    for key, value in nested.items():
        if not isinstance(value, bool):
            raise ValueError(f"visual candidate hazard {key} must be boolean")

    active = []
    for canonical, reason, aliases in _HAZARD_ALIASES:
        value = False
        for key in aliases:
            if key in candidate:
                if not isinstance(candidate[key], bool):
                    raise ValueError(f"visual candidate hazard {key} must be boolean")
                value = value or candidate[key]
            if key in nested:
                value = value or nested[key]
        if value:
            active.append(reason)

    if "embeddedTextStatus" in candidate:
        embedded_status = candidate["embeddedTextStatus"]
        if not isinstance(embedded_status, str) or embedded_status not in _EMBEDDED_TEXT_STATUSES:
            raise ValueError("visual candidate embeddedTextStatus must be known, unread, or none")
    else:
        embedded_status = None
    label_status = candidate.get("labelStatus", candidate.get("pixelTextStatus", ""))
    if not isinstance(label_status, str):
        raise ValueError("visual candidate label status must be text")
    if embedded_status == "unread" or label_status == "unread":
        active.append("label-transcription-required")
    return active


def _decorative(candidate: dict) -> bool:
    if any(candidate.get(key) is True for key in ("decorative", "isDecorative", "decorativePortrait")):
        return True
    kind = str(candidate.get("kind", "")).casefold().replace("_", "-")
    return kind in {"portrait", "decorativeportrait", "decorative-portrait"}


def rank_visual_candidate(candidate: dict, lesson_evidence: dict) -> dict:
    """Return a deterministic provisional ``reuse``, ``reconstruct``, or ``exclude`` decision."""
    _validate_candidate(candidate)
    sections, values = _validate_lesson_evidence(lesson_evidence)
    result = copy.deepcopy(candidate)
    candidate_texts = _candidate_texts(candidate)
    candidate_terms = _terms(candidate_texts)

    section_overlaps = {
        name: sorted(_matching_terms(candidate_terms, section_values))
        for name, section_values in sections
    }
    explicit_overlap = _matching_terms(candidate_terms, values["explicitReferences"])
    entity_overlap = _matching_terms(candidate_terms, values["entities"])
    quantity_overlap = _matching_terms(candidate_terms, values["quantities"])

    requested_section = _supported_section(candidate.get("supportedSection"), "supportedSection")
    section_names = {name for name, _ in sections}
    ranked_sections = sorted(
        section_overlaps,
        key=lambda name: (-len(section_overlaps[name]), name),
    )
    declared_section_missing = requested_section is not None and requested_section not in section_names
    selected_section = requested_section if not declared_section_missing else None
    if requested_section is None and ranked_sections and section_overlaps[ranked_sections[0]]:
        selected_section = ranked_sections[0]
    selected_overlap = section_overlaps.get(selected_section, []) if selected_section else []

    decision_reasons = []
    if explicit_overlap:
        decision_reasons.append("explicit-reference")
    if entity_overlap:
        decision_reasons.append("entity-overlap")
    if quantity_overlap:
        decision_reasons.append("quantity-overlap")
    if selected_overlap:
        decision_reasons.append("section-overlap")
    if _decorative(candidate):
        decision_reasons.append("decorative-visual")

    evidence_items = bool(explicit_overlap or entity_overlap or quantity_overlap or selected_overlap)
    if not evidence_items:
        decision_reasons.append("title-only-evidence" if candidate.get("title") or candidate.get("captionText") else "no-instructional-evidence")

    blocking_reasons = _active_hazards(candidate)
    if selected_section is None:
        blocking_reasons.append("supported-section-missing")
    if not evidence_items:
        blocking_reasons.append("non-title-evidence-missing")
    if _decorative(candidate):
        blocking_reasons.append("decorative-visual")

    if _decorative(candidate) or selected_section is None or not evidence_items:
        decision = "exclude"
        supported = None
    elif blocking_reasons and any(reason != "supported-section-missing" for reason in blocking_reasons):
        decision = "reconstruct"
        supported = selected_section
    else:
        decision = "reuse"
        supported = selected_section

    result.update(
        {
            "decision": decision,
            "decisionReasons": decision_reasons,
            "blockingReasons": blocking_reasons,
            "supportedSection": supported,
            "reviewRequired": True,
            "reviewStatus": "pending",
        }
    )
    return result


__all__ = ["link_candidate_context", "rank_visual_candidate"]
