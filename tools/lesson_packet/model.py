"""Typed lesson loading and deterministic pedagogical contract validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    return bool(str(value or "").strip())


def validate_causal_chain(chain: dict) -> list[str]:
    return [f"causal-chain-missing:{key}" for key in CAUSAL_KEYS if not str(chain.get(key, "")).strip()]


def _validate_requirements(items: list[Any], declared: set[str]) -> list[str]:
    errors: list[str] = []
    for item in items:
        for requirement in _items(_mapping(item).get("requires")):
            if requirement not in declared:
                errors.append(f"unsupported-requirement:{requirement}")
    return errors


def validate_lesson_dict(data: dict) -> list[str]:
    """Return stable error IDs for every missing teaching prerequisite."""
    if not isinstance(data, dict):
        return ["lesson-invalid"]

    errors: list[str] = []
    visual_ids = {
        visual.get("id")
        for visual in _items(data.get("visuals"))
        if isinstance(visual, dict)
        and _is_present(visual.get("id"))
        and isinstance(visual.get("sourcePage"), int)
        and visual["sourcePage"] > 0
    }
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
        if phenomenon.get("visualId") not in visual_ids:
            errors.append(f"{prefix}-phenomenon-source-missing")

        concepts = _items(period.get("concepts"))
        if not concepts:
            errors.append(f"{prefix}-concepts-empty")
        elif len(concepts) < 2:
            errors.append(f"{prefix}-concepts-insufficient")
        concept_ids = {
            concept.get("id")
            for concept in concepts
            if isinstance(concept, dict) and _is_present(concept.get("id"))
        }
        for concept in concepts:
            if not _is_present(_mapping(concept).get("explanation")):
                errors.append(f"{prefix}-concept-explanation-missing")

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
        else:
            errors.extend(_validate_requirements([worked], declared))

        for field, error_name in (
            ("guidedPractice", "guided-practice"),
            ("independentPractice", "independent-practice"),
            ("exitCheck", "exit-check"),
        ):
            items = _items(period.get(field))
            if not items:
                errors.append(f"{prefix}-{error_name}-empty")
            errors.extend(_validate_requirements(items, declared))

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
