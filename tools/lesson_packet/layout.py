"""Print-dense A4 page templates for chemistry lesson packets."""

from __future__ import annotations

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Frame, NextPageTemplate, PageBreak, PageTemplate


MARGIN_MM = 14
COLUMN_GUTTER = 6 * mm

# Every token differs in luminance and is paired with labels/line styles in the
# diagrams, so the packet remains legible on a grayscale copier.
GRAY_TOKENS = {
    "ink": HexColor("#1D1D1D"),
    "muted": HexColor("#545454"),
    "rule": HexColor("#8A8A8A"),
    "panel": HexColor("#E5E5E5"),
    "student_annotation": HexColor("#F2F2F2"),
    "teacher_annotation": HexColor("#D6D6D6"),
    "paper": white,
}


def column_geometry(page_size: tuple[float, float] = A4, margins_mm: float = MARGIN_MM) -> tuple[float, float, float]:
    """Return equal column widths and the gutter for an exact content fit."""
    if margins_mm < 0:
        raise ValueError("margins_mm must be non-negative")
    content_width = page_size[0] - (2 * margins_mm * mm)
    if content_width <= COLUMN_GUTTER:
        raise ValueError("margins leave no room for two columns")
    column_width = (content_width - COLUMN_GUTTER) / 2
    return column_width, column_width, COLUMN_GUTTER


def make_styles() -> dict[str, ParagraphStyle]:
    """Create styles whose print sizes never fall below the design floors."""
    body = ParagraphStyle(
        "LessonBody",
        fontName="Helvetica",
        fontSize=9.4,
        leading=12,
        textColor=GRAY_TOKENS["ink"],
        spaceAfter=3,
    )
    return {
        "body": body,
        "heading": ParagraphStyle(
            "LessonHeading",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=15,
            spaceBefore=2,
            spaceAfter=5,
        ),
        "subheading": ParagraphStyle(
            "LessonSubheading",
            parent=body,
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=12.5,
            spaceBefore=3,
            spaceAfter=3,
        ),
        "caption": ParagraphStyle(
            "LessonCaption",
            parent=body,
            fontSize=8.2,
            leading=10,
            textColor=GRAY_TOKENS["muted"],
        ),
        "student_annotation": ParagraphStyle(
            "StudentAnnotation",
            parent=body,
            fontSize=9,
            leading=11,
            backColor=GRAY_TOKENS["student_annotation"],
            borderColor=GRAY_TOKENS["rule"],
            borderWidth=0.5,
            borderPadding=4,
        ),
        "teacher_annotation": ParagraphStyle(
            "TeacherAnnotation",
            parent=body,
            fontSize=8.2,
            leading=10,
            backColor=GRAY_TOKENS["teacher_annotation"],
            borderColor=GRAY_TOKENS["ink"],
            borderWidth=0.7,
            borderPadding=4,
        ),
        "teacher_note": ParagraphStyle(
            "TeacherNote",
            parent=body,
            fontSize=8.2,
            leading=10,
            textColor=GRAY_TOKENS["ink"],
            backColor=GRAY_TOKENS["teacher_annotation"],
            borderColor=GRAY_TOKENS["rule"],
            borderWidth=0.5,
            borderPadding=4,
        ),
    }


def build_page_templates(doc, role: str) -> list[PageTemplate]:
    """Create genuine ReportLab frames for two-column and full-width pages."""
    if role not in {"student", "teacher"}:
        raise ValueError("role must be 'student' or 'teacher'")

    page_width, page_height = getattr(doc, "pagesize", A4)
    margin = MARGIN_MM * mm
    usable_height = page_height - (2 * margin)
    left_width, right_width, gutter = column_geometry((page_width, page_height))
    frame_options = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}
    two_columns = [
        Frame(margin, margin, left_width, usable_height, id="two-column-left", **frame_options),
        Frame(margin + left_width + gutter, margin, right_width, usable_height, id="two-column-right", **frame_options),
    ]
    full_width = [
        Frame(margin, margin, page_width - (2 * margin), usable_height, id="full-width", **frame_options)
    ]
    return [
        PageTemplate(id="two-column", frames=two_columns),
        PageTemplate(id="full-width", frames=full_width),
    ]


def template_page_break(template_name: str, *flowables):
    """Switch template only at a page boundary, preserving frame invariants."""
    if template_name not in {"two-column", "full-width"}:
        raise ValueError(f"unknown page template: {template_name}")
    return [NextPageTemplate(template_name), PageBreak(), *flowables]


def two_column_flowables(*flowables):
    """Place subsequent material on a new two-column A4 page."""
    return template_page_break("two-column", *flowables)


def full_width_flowables(*flowables):
    """Place diagrams, tables, graphs, or calculations on a full-width page."""
    return template_page_break("full-width", *flowables)
