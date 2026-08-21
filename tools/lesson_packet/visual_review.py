"""Deterministic visual review packets and approval validation.

This module only presents the already extracted source page and candidate crop
side by side.  It does not perform OCR, reconstruction, ranking, or approval.
"""

import math
import re
from pathlib import Path

from PIL import Image as PillowImage, ImageDraw


REQUIRED_CHECKS = (
    "completeFigure",
    "internalLabels",
    "directions",
    "axesUnits",
    "conditions",
    "sourceTrace",
)
VALID_STATUSES = ("pending", "approved", "rejected")
VALID_EMBEDDED_TEXT_STATUSES = ("known", "unread", "none")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEW_FIELDS = (
    "candidateId",
    "reviewer",
    "status",
    "checks",
    "transcribedLabels",
    "notes",
)


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_bounds(value: object, label: str) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        return None
    bounds = [float(item) for item in value]
    if not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1):
        return None
    return bounds


def _candidate_errors(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate-invalid"]

    errors = []
    if not _nonempty_text(candidate.get("id")):
        errors.append("candidate-id-missing")

    source_page = candidate.get("sourcePage")
    if isinstance(source_page, bool) or not isinstance(source_page, int) or source_page < 1:
        errors.append("candidate-source-page-invalid")

    source_hash = candidate.get("sourceSha256")
    if not isinstance(source_hash, str) or SHA256_RE.fullmatch(source_hash) is None:
        errors.append("candidate-source-sha256-invalid")

    if _normalized_bounds(candidate.get("bounds"), "candidate bounds") is None:
        errors.append("candidate-bounds-invalid")

    if "embeddedTextStatus" in candidate:
        status = candidate["embeddedTextStatus"]
        if not isinstance(status, str) or status not in VALID_EMBEDDED_TEXT_STATUSES:
            errors.append("candidate-embedded-text-status-invalid")
    return errors


def _review_trace_errors(candidate: dict, review: dict) -> list[str]:
    """Validate optional copied source trace fields on a review record."""
    trace_fields = ("sourcePage", "sourceSha256", "bounds")
    present = [field for field in trace_fields if field in review]
    if not present:
        return []

    errors = []
    if len(present) != len(trace_fields):
        errors.append("review-source-trace-incomplete")

    candidate_page = candidate.get("sourcePage")
    review_page = review.get("sourcePage")
    if isinstance(review_page, bool) or not isinstance(review_page, int) or review_page < 1:
        errors.append("review-source-page-invalid")
    elif isinstance(candidate_page, int) and not isinstance(candidate_page, bool) and review_page != candidate_page:
        errors.append("review-source-page-mismatch")

    candidate_hash = candidate.get("sourceSha256")
    review_hash = review.get("sourceSha256")
    if not isinstance(review_hash, str) or SHA256_RE.fullmatch(review_hash) is None:
        errors.append("review-source-sha256-invalid")
    elif isinstance(candidate_hash, str) and review_hash != candidate_hash:
        errors.append("review-source-sha256-mismatch")

    candidate_bounds = _normalized_bounds(candidate.get("bounds"), "candidate bounds")
    review_bounds = _normalized_bounds(review.get("bounds"), "review bounds")
    if review_bounds is None:
        errors.append("review-bounds-invalid")
    elif candidate_bounds is not None and review_bounds != candidate_bounds:
        errors.append("review-bounds-mismatch")
    return errors


def validate_review(candidate: dict, review: dict) -> list[str]:
    """Return deterministic errors for a visual review/approval record.

    Required checks are deliberately compared with ``is True`` so truthy
    values such as ``1`` cannot silently approve a source visual.  A review
    remains a gate record: incomplete checks are reported for every status,
    and an approved unread raster additionally requires verified transcription.
    """
    errors = _candidate_errors(candidate)
    if not isinstance(review, dict):
        return errors + ["review-invalid"]

    missing_fields = [field for field in REVIEW_FIELDS if field not in review]
    errors.extend(f"review-field-missing:{field}" for field in missing_fields)

    candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
    review_candidate_id = review.get("candidateId")
    if not _nonempty_text(review_candidate_id):
        if "candidateId" not in missing_fields:
            errors.append("review-candidate-id-missing")
    elif _nonempty_text(candidate_id) and review_candidate_id != candidate_id:
        errors.append("candidate-identity-mismatch")

    if "reviewer" in review and not _nonempty_text(review.get("reviewer")):
        errors.append("reviewer-missing")

    status = review.get("status")
    if status not in VALID_STATUSES:
        errors.append("review-status-invalid")

    checks = review.get("checks")
    if not isinstance(checks, dict):
        errors.append("review-checks-invalid")
    else:
        unknown_checks = sorted(set(checks) - set(REQUIRED_CHECKS), key=str)
        errors.extend(f"review-check-unknown:{name}" for name in unknown_checks)
        errors.extend(
            f"review-check-failed:{name}"
            for name in REQUIRED_CHECKS
            if checks.get(name) is not True
        )

    labels = review.get("transcribedLabels")
    labels_valid = isinstance(labels, list) and all(_nonempty_text(label) for label in labels)
    if not labels_valid:
        errors.append("transcribed-labels-invalid")

    if "notes" in review and not isinstance(review.get("notes"), str):
        errors.append("review-notes-invalid")

    if isinstance(candidate, dict) and candidate.get("embeddedTextStatus") == "unread" and not (labels_valid and labels):
        errors.append("label-transcription-missing")

    if isinstance(candidate, dict) and isinstance(review, dict):
        errors.extend(_review_trace_errors(candidate, review))
    return errors


def _render_bounds(candidate: dict) -> list[float]:
    errors = _candidate_errors(candidate)
    if errors:
        raise ValueError("invalid visual candidate: " + ", ".join(errors))
    return [float(value) for value in candidate["bounds"]]


def _pixel_crop(bounds: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, min(width - 1, math.floor(bounds[0] * width)))
    top = max(0, min(height - 1, math.floor(bounds[1] * height)))
    right = max(left + 1, min(width, math.ceil(bounds[2] * width)))
    bottom = max(top + 1, min(height, math.ceil(bounds[3] * height)))
    return left, top, right, bottom


def render_review_packet(candidate: dict, full_page: Path, out: Path) -> Path:
    """Write a deterministic full-page-overlay plus candidate-crop PNG packet."""
    bounds = _render_bounds(candidate)
    full_page = Path(full_page)
    out = Path(out)
    if not full_page.is_file():
        raise ValueError(f"full source page not found: {full_page}")
    if full_page.resolve() == out.resolve():
        raise ValueError("review packet output must differ from full source page")

    try:
        with PillowImage.open(full_page) as source:
            page = source.convert("RGB")
    except (OSError, ValueError) as error:
        raise ValueError(f"could not open full source page: {full_page}") from error

    crop_box = _pixel_crop(bounds, page.width, page.height)
    overlay = page.copy()
    draw = ImageDraw.Draw(overlay)
    outline_width = max(2, min(6, min(page.size) // 80 or 2))
    outline = (210, 30, 45)
    for offset in range(outline_width):
        draw.rectangle(
            [
                crop_box[0] - offset,
                crop_box[1] - offset,
                crop_box[2] - 1 + offset,
                crop_box[3] - 1 + offset,
            ],
            outline=outline,
        )

    crop = page.crop(crop_box)
    crop_draw = ImageDraw.Draw(crop)
    for offset in range(outline_width):
        crop_draw.rectangle(
            [offset, offset, crop.width - 1 - offset, crop.height - 1 - offset],
            outline=outline,
        )

    gutter = 24
    border = 4
    height = max(overlay.height, crop.height) + border * 2
    width = overlay.width + gutter + crop.width + border * 2
    width = max(width, height + 1)
    packet = PillowImage.new("RGB", (width, height), "white")
    page_y = border + (height - border * 2 - overlay.height) // 2
    crop_y = border + (height - border * 2 - crop.height) // 2
    packet.paste(overlay, (border, page_y))
    packet.paste(crop, (border + overlay.width + gutter, crop_y))

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        packet.save(out, format="PNG", optimize=False, compress_level=9)
    except OSError as error:
        raise ValueError(f"could not write review packet: {out}") from error
    return out


__all__ = ["REQUIRED_CHECKS", "render_review_packet", "validate_review"]
