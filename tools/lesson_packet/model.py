"""Typed lesson loading and deterministic pedagogical contract validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.lesson_packet.source_extract import validate_visual_map


CAUSAL_KEYS = ("phenomenon", "change", "cause", "representation", "application")


@dataclass(frozen=True)
class Source:
    source_file: str
    sha256: str
    lesson_count: int


@dataclass(frozen=True)
class Meta:
    title: str
    subject: str
    standard_codes: tuple[str, ...]


@dataclass(frozen=True)
class Lesson:
    source: Source
    meta: Meta
    periods: tuple[dict[str, Any], ...]
    visuals: tuple[dict[str, Any], ...]
    teacherNotes: tuple[dict[str, Any], ...]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_causal_chain(chain: dict) -> list[str]:
    return [f"causal-chain-missing:{key}" for key in CAUSAL_KEYS if not str(chain.get(key, "")).strip()]


def _validate_requirements(items: list[Any], declared: set[str]) -> list[str]:
    errors: list[str] = []
    for item in items:
        for requirement in _items(_mapping(item).get("requires")):
            if not _is_present(requirement):
                errors.append("unsupported-requirement-invalid")
            elif requirement not in declared:
                errors.append(f"unsupported-requirement:{requirement}")
    return errors


def _validate_practice_item(item: Any, prefix: str, declared: set[str]) -> list[str]:
    practice = _mapping(item)
    errors: list[str] = []
    if "prompt" not in practice or (isinstance(practice.get("prompt"), str) and not practice["prompt"].strip()):
        errors.append(f"{prefix}-prompt-missing")
    elif not isinstance(practice["prompt"], str):
        errors.append(f"{prefix}-prompt-invalid")

    if "requires" not in practice:
        errors.append(f"{prefix}-requires-missing")
    elif not isinstance(practice["requires"], list):
        errors.append(f"{prefix}-requires-invalid")
    elif not practice["requires"]:
        errors.append(f"{prefix}-requires-missing")
    else:
        errors.extend(_validate_requirements([practice], declared))
    return errors


def validate_lesson_dict(data: dict) -> list[str]:
    """Return stable error IDs for every missing teaching prerequisite."""
    if not isinstance(data, dict):
        return ["lesson-invalid"]

    errors: list[str] = []
    visual_ids: set[str] = set()
    for visual in _items(data.get("visuals")):
        if not isinstance(visual, dict):
            errors.append("visual-invalid")
            continue
        visual_errors = validate_visual_map(visual)
        visual_id = visual.get("id")
        errors.extend(f"visual:{visual_id if _is_present(visual_id) else 'unknown'}:{error}" for error in visual_errors)
        if not visual_errors and _is_present(visual_id) and isinstance(visual.get("sourcePage"), int) and visual["sourcePage"] > 0:
            visual_ids.add(visual_id)
    periods = _items(data.get("periods"))
    if not periods:
        return ["periods-empty"]

    for number, raw_period in enumerate(periods, start=1):
        prefix = f"period-{number}"
        period = _mapping(raw_period)
        if not _is_present(period.get("question")):
            errors.append(f"{prefix}-question-empty")
        if not _items(period.get("objectives")):
            errors.append(f"{prefix}-objectives-empty")
        if not _items(period.get("observations")):
            errors.append(f"{prefix}-observations-empty")
        phenomenon = _mapping(period.get("phenomenon"))
        if not _is_present(phenomenon.get("body")):
            errors.append(f"{prefix}-phenomenon-empty")
        if not _is_present(phenomenon.get("visualId")) or phenomenon["visualId"] not in visual_ids:
            errors.append(f"{prefix}-phenomenon-source-missing")

        concepts = _items(period.get("concepts"))
        if not concepts:
            errors.append(f"{prefix}-concepts-empty")
        concept_ids: set[str] = set()
        seen_concept_ids: set[str] = set()
        for concept in concepts:
            concept_data = _mapping(concept)
            concept_id = concept_data.get("id")
            term = concept_data.get("term")
            explanation = concept_data.get("explanation")
            has_id = _is_present(concept_id)
            has_term = _is_present(term)
            has_explanation = _is_present(explanation)
            if "id" not in concept_data or (isinstance(concept_id, str) and not concept_id.strip()):
                errors.append(f"{prefix}-concept-id-missing")
            elif not isinstance(concept_id, str):
                errors.append(f"{prefix}-concept-id-invalid")
            elif concept_id in seen_concept_ids:
                errors.append(f"{prefix}-concepts-duplicate-id:{concept_id}")
            else:
                seen_concept_ids.add(concept_id)
                if has_term and has_explanation:
                    concept_ids.add(concept_id)
            if "term" not in concept_data or (isinstance(term, str) and not term.strip()):
                errors.append(f"{prefix}-concept-term-missing")
            elif not isinstance(term, str):
                errors.append(f"{prefix}-concept-term-invalid")
            if "explanation" not in concept_data or (isinstance(explanation, str) and not explanation.strip()):
                errors.append(f"{prefix}-concept-explanation-missing")
            elif not isinstance(explanation, str):
                errors.append(f"{prefix}-concept-explanation-invalid")
        if concepts and len(concept_ids) < 2:
            errors.append(f"{prefix}-concepts-insufficient")

        errors.extend(validate_causal_chain(_mapping(period.get("causalChain"))))

        representations = _items(period.get("representations"))
        if not representations:
            errors.append(f"{prefix}-representations-empty")
        for representation in representations:
            if not _is_present(_mapping(representation).get("body")):
                errors.append(f"{prefix}-representation-body-missing")
        errors.extend(_validate_requirements(representations, concept_ids))
        representation_ids = {
            representation.get("id")
            for representation in representations
            if isinstance(representation, dict) and _is_present(representation.get("id"))
        }
        declared = concept_ids | representation_ids

        worked = _mapping(period.get("workedExample"))
        if not worked:
            errors.append(f"{prefix}-worked-example-empty")
        errors.extend(_validate_practice_item(worked, f"{prefix}-worked-example", declared))

        for field, error_name in (
            ("guidedPractice", "guided-practice"),
            ("independentPractice", "independent-practice"),
            ("exitCheck", "exit-check"),
        ):
            items = _items(period.get(field))
            if not items:
                errors.append(f"{prefix}-{error_name}-empty")
            for item in items:
                errors.extend(_validate_practice_item(item, f"{prefix}-{error_name}", declared))

    return errors


def lesson_from_dict(data: dict) -> Lesson:
    source = _mapping(data.get("source"))
    meta = _mapping(data.get("meta"))
    return Lesson(
        source=Source(
            source_file=str(source.get("sourceFile", "")),
            sha256=str(source.get("sha256", "")),
            lesson_count=int(source.get("lessonCount", 0)),
        ),
        meta=Meta(
            title=str(meta.get("title", "")),
            subject=str(meta.get("subject", "")),
            standard_codes=tuple(str(code) for code in _items(meta.get("standardCodes"))),
        ),
        periods=tuple(_items(data.get("periods"))),
        visuals=tuple(_items(data.get("visuals"))),
        teacherNotes=tuple(_items(data.get("teacherNotes"))),
    )


def validate_lesson(lesson: Lesson) -> list[str]:
    return validate_lesson_dict(
        {
            "source": {
                "sourceFile": lesson.source.source_file,
                "sha256": lesson.source.sha256,
                "lessonCount": lesson.source.lesson_count,
            },
            "meta": {
                "title": lesson.meta.title,
                "subject": lesson.meta.subject,
                "standardCodes": list(lesson.meta.standard_codes),
            },
            "periods": list(lesson.periods),
            "visuals": list(lesson.visuals),
            "teacherNotes": list(lesson.teacherNotes),
        }
    )


def load_lesson(path: Path) -> Lesson:
    with path.open(encoding="utf-8") as stream:
        return lesson_from_dict(json.load(stream))
