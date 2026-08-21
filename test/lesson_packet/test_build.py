import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image as PillowImage
from pypdf import PdfReader
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate

from tools.lesson_packet.layout import (
    build_page_templates,
    column_geometry,
    full_width_flowables,
    make_styles,
    two_column_flowables,
)
from tools.lesson_packet.visuals import (
    reconstructed_visual_flowable,
    source_crop_flowable,
)


class LayoutTests(unittest.TestCase):
    def test_body_style_meets_print_floor(self):
        self.assertGreaterEqual(make_styles()["body"].fontSize, 9)

    def test_compact_styles_meet_caption_floor_and_annotations_are_role_specific(self):
        styles = make_styles()
        self.assertGreaterEqual(styles["caption"].fontSize, 8)
        self.assertGreaterEqual(styles["teacher_note"].fontSize, 8)
        self.assertNotEqual(styles["student_annotation"].backColor, styles["teacher_annotation"].backColor)

    def test_two_column_widths_fit_a4_content_box(self):
        left, right, gutter = column_geometry(A4, margins_mm=14)
        self.assertLess(abs((left + right + gutter) - (A4[0] - 28 * mm)), 0.1)
        self.assertAlmostEqual(left, right)

    def test_all_template_frames_are_within_the_a4_14mm_content_box(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = SimpleDocTemplate(str(Path(directory) / "fixture.pdf"), pagesize=A4)
            templates = {template.id: template for template in build_page_templates(doc, role="teacher")}
        margin = 14 * mm
        for template in templates.values():
            for frame in template.frames:
                with self.subTest(template=template.id, frame=frame.id):
                    self.assertGreaterEqual(frame._x1, margin - 0.01)
                    self.assertGreaterEqual(frame._y1, margin - 0.01)
                    self.assertLessEqual(frame._x1 + frame._width, A4[0] - margin + 0.01)
                    self.assertLessEqual(frame._y1 + frame._height, A4[1] - margin + 0.01)
        two_columns = templates["two-column"].frames
        self.assertAlmostEqual(two_columns[0]._x1 + two_columns[0]._width + 6 * mm, two_columns[1]._x1)
        self.assertAlmostEqual(templates["full-width"].frames[0]._width, A4[0] - 28 * mm)

    def test_templates_use_real_named_frames_and_controlled_breaks(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = SimpleDocTemplate(str(Path(directory) / "fixture.pdf"), pagesize=A4)
            templates = build_page_templates(doc, role="teacher")
        by_name = {template.id: template for template in templates}
        self.assertEqual(set(by_name), {"two-column", "full-width"})
        self.assertEqual(len(by_name["two-column"].frames), 2)
        self.assertEqual(len(by_name["full-width"].frames), 1)
        self.assertEqual(type(two_column_flowables(Paragraph("x", make_styles()["body"]))[0]).__name__, "NextPageTemplate")
        self.assertEqual(type(full_width_flowables(Paragraph("x", make_styles()["body"]))[1]).__name__, "PageBreak")

    def test_templates_render_two_pages_without_overflow_or_clipping_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "two-pages.pdf"
            doc = SimpleDocTemplate(str(pdf), pagesize=A4)
            doc.addPageTemplates(build_page_templates(doc, role="student"))
            styles = make_styles()
            doc.build([
                Paragraph("Two columns", styles["body"]),
                *[Paragraph("Column content remains inside a real frame.", styles["body"]) for _ in range(20)],
                *full_width_flowables(Paragraph("Full width", styles["body"])),
                Paragraph("END OF FULL WIDTH CONTENT", styles["body"]),
            ])
            pages = PdfReader(pdf).pages
            self.assertEqual(len(pages), 2)
            self.assertIn("END OF FULL WIDTH CONTENT", pages[1].extract_text() or "")
            for page in pages:
                self.assertAlmostEqual(float(page.mediabox.width), A4[0], places=1)
                self.assertAlmostEqual(float(page.mediabox.height), A4[1], places=1)


class VisualFlowableTests(unittest.TestCase):
    def test_source_crop_flowable_uses_normalized_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = PillowImage.new("L", (100, 80), color=255)
            image.paste(0, (10, 16, 90, 64))
            image.save(root / "page-001.png")
            flowable = source_crop_flowable(
                {"sourcePage": 1, "crop": [0.1, 0.2, 0.9, 0.8]}, root
            )
            self.assertEqual(flowable._lesson_crop_pixels, (10, 16, 90, 64))
            self.assertGreater(flowable.drawWidth, flowable.drawHeight)

    def test_source_crop_flowable_rejects_invalid_normalized_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "normalized"):
                source_crop_flowable(
                    {"sourcePage": 1, "crop": [0.8, 0.1, 0.2, 0.9]}, Path(directory)
                )

    def test_generic_scene_renders_unseen_phase_curve_osmosis_and_hess_compositions(self):
        for visual in (self._phase_curve_visual(), self._osmosis_visual(), self._hess_path_visual()):
            with self.subTest(scene=visual["schema"]["scene"]["canvas"]["id"]):
                flowable = reconstructed_visual_flowable(visual)
                self.assertGreater(flowable.width, 0)
                self.assertGreater(flowable.height, 0)
                self.assertIn("path", flowable._lesson_semantic_ids)
                self.assertNotIn(visual["title"], flowable._lesson_semantic_labels)
                self.assertTrue(renderPDF.drawToString(flowable).startswith(b"%PDF"))

    def test_generic_scene_rejects_unknown_primitives_style_tokens_and_reference_errors(self):
        visual = self._phase_curve_visual()
        unknown_kind = copy.deepcopy(visual)
        unknown_kind["schema"]["scene"]["primitives"][0]["kind"] = "spline"
        with self.assertRaisesRegex(ValueError, "primitive kind"):
            reconstructed_visual_flowable(unknown_kind)

        unknown_field = copy.deepcopy(visual)
        unknown_field["schema"]["scene"]["primitives"][0]["unsupported"] = True
        with self.assertRaisesRegex(ValueError, "unknown field"):
            reconstructed_visual_flowable(unknown_field)

        unsafe_style = copy.deepcopy(visual)
        unsafe_style["schema"]["scene"]["primitives"][0]["style"] = {"stroke": "red"}
        with self.assertRaisesRegex(ValueError, "grayscale"):
            reconstructed_visual_flowable(unsafe_style)

        broken_relation = copy.deepcopy(visual)
        broken_relation["relationships"][0]["to"] = "missing-entity"
        with self.assertRaisesRegex(ValueError, "relationship"):
            reconstructed_visual_flowable(broken_relation)

        broken_invariant = copy.deepcopy(visual)
        broken_invariant["invariants"][0]["refs"] = ["missing-primitive"]
        with self.assertRaisesRegex(ValueError, "invariant"):
            reconstructed_visual_flowable(broken_invariant)

        transformed_outside_canvas = copy.deepcopy(visual)
        transformed_outside_canvas["schema"]["scene"]["primitives"][2] = {
            "kind": "group", "semanticId": "path", "transform": {"translate": [0.5, 0.0]},
            "primitives": [{"kind": "line", "x1": 0.70, "y1": 0.30, "x2": 0.80, "y2": 0.40}],
        }
        with self.assertRaisesRegex(ValueError, "normalized canvas"):
            reconstructed_visual_flowable(transformed_outside_canvas)

    def test_tube_adapter_synthesizes_equation_only_from_validated_relation_terms(self):
        flowable = reconstructed_visual_flowable(self._tube_visual())
        self.assertIn("P_gas = P_atm + rho g h", flowable._lesson_semantic_labels)
        self.assertNotIn("Do not infer a manometer from this title", flowable._lesson_semantic_labels)

    def test_tube_adapter_rejects_free_expressions_and_contradictory_signs_before_display(self):
        free_expression = self._tube_visual()
        free_expression["schema"]["pressureRelation"]["expression"] = "P_gas = P_atm - rho g h"
        with self.assertRaisesRegex(ValueError, "free-form"):
            reconstructed_visual_flowable(free_expression)

        contradictory_sign = self._tube_visual()
        contradictory_sign["schema"]["pressureRelation"]["terms"][0]["operator"] = "-"
        with self.assertRaisesRegex(ValueError, "sign"):
            reconstructed_visual_flowable(contradictory_sign)

    @staticmethod
    def _visual(scene, *, title):
        return {
            "title": title,
            "entities": [{"id": "source", "label": "source state"}, {"id": "target", "label": "target state"}],
            "relationships": [{"id": "rel-1", "kind": "causes", "from": "source", "to": "target", "primitiveIds": ["path"]}],
            "invariants": [{"id": "invariant-1", "kind": "source-grounded", "refs": ["path"]}],
            "constraints": [{"id": "constraint-1", "kind": "ordered", "refs": ["path"]}],
            "schema": {"scene": scene},
        }

    @classmethod
    def _phase_curve_visual(cls):
        return cls._visual({
            "canvas": {"id": "phase-curve", "width": 420, "height": 225},
            "primitives": [
                {"kind": "axis", "semanticId": "x-axis", "x1": 0.12, "y1": 0.15, "x2": 0.90, "y2": 0.15, "label": "temperature"},
                {"kind": "axis", "semanticId": "y-axis", "x1": 0.12, "y1": 0.15, "x2": 0.12, "y2": 0.88, "label": "pressure"},
                {"kind": "path", "semanticId": "path", "points": [[0.18, 0.20], [0.42, 0.46], [0.73, 0.85]], "style": {"stroke": "ink", "strokeWidth": 1.5}},
                {"kind": "marker", "semanticId": "critical", "x": 0.73, "y": 0.85, "label": "critical point"},
                {"kind": "text", "semanticId": "liquid", "x": 0.57, "y": 0.60, "text": "liquid"},
                {"kind": "arrow", "semanticId": "warming", "x1": 0.22, "y1": 0.30, "x2": 0.34, "y2": 0.41, "label": "warming"},
            ],
        }, title="Title must not select a phase curve")

    @classmethod
    def _osmosis_visual(cls):
        return cls._visual({
            "canvas": {"id": "osmosis", "width": 420, "height": 225},
            "primitives": [
                {"kind": "rounded_rect", "semanticId": "container", "x": 0.12, "y": 0.16, "width": 0.76, "height": 0.62, "radius": 0.03},
                {"kind": "line", "semanticId": "membrane", "x1": 0.50, "y1": 0.16, "x2": 0.50, "y2": 0.78, "style": {"dash": "dashed"}},
                {"kind": "repeat", "semanticId": "solutes", "count": 4, "translate": [0.08, 0.0], "primitive": {"kind": "circle", "cx": 0.18, "cy": 0.42, "r": 0.025, "style": {"fill": "light"}}},
                {"kind": "arrow", "semanticId": "water-flow", "x1": 0.74, "y1": 0.50, "x2": 0.55, "y2": 0.50, "label": "water"},
                {"kind": "dimension", "semanticId": "height-difference", "x1": 0.91, "y1": 0.32, "x2": 0.91, "y2": 0.65, "label": "h"},
                {"kind": "text", "semanticId": "solute-label", "x": 0.25, "y": 0.25, "text": "solute side"},
                {"kind": "path", "semanticId": "path", "points": [[0.55, 0.50], [0.64, 0.50], [0.74, 0.50]]},
            ],
        }, title="Title must not select an osmosis apparatus")

    @classmethod
    def _hess_path_visual(cls):
        return cls._visual({
            "canvas": {"id": "hess-path", "width": 420, "height": 225},
            "primitives": [
                {"kind": "rect", "semanticId": "reactants", "x": 0.10, "y": 0.62, "width": 0.20, "height": 0.12},
                {"kind": "text", "semanticId": "reactants-label", "x": 0.20, "y": 0.67, "text": "reactants", "style": {"textAnchor": "middle"}},
                {"kind": "rect", "semanticId": "products", "x": 0.70, "y": 0.62, "width": 0.20, "height": 0.12},
                {"kind": "text", "semanticId": "products-label", "x": 0.80, "y": 0.67, "text": "products", "style": {"textAnchor": "middle"}},
                {"kind": "arrow", "semanticId": "direct-path", "x1": 0.31, "y1": 0.68, "x2": 0.69, "y2": 0.68, "label": "Delta H"},
                {"kind": "connector", "semanticId": "route-down", "x1": 0.20, "y1": 0.62, "x2": 0.20, "y2": 0.30, "style": {"dash": "dashed"}},
                {"kind": "polyline", "semanticId": "path", "points": [[0.20, 0.30], [0.50, 0.18], [0.80, 0.30]], "style": {"stroke": "muted"}},
                {"kind": "arrow", "semanticId": "indirect-path", "x1": 0.50, "y1": 0.18, "x2": 0.79, "y2": 0.30, "label": "Delta H2"},
            ],
        }, title="Title must not select a Hess path")

    @staticmethod
    def _tube_visual():
        return {
            "title": "Do not infer a manometer from this title",
            "entities": [{"id": "gas", "label": "gas"}, {"id": "air", "label": "air"}],
            "relationships": [{"id": "pressure-balance", "kind": "balances", "from": "gas", "to": "air", "primitiveIds": ["tube"]}],
            "invariants": [{"id": "levels", "kind": "relative-levels", "refs": ["tube"]}],
            "schema": {
                "tube": {"shape": "U", "sealedSide": "left", "openSide": "right", "liquidLevels": {"left": 0.30, "right": 0.64}},
                "labels": {"sealed": "P_gas", "open": "P_atm", "liquid": "Hg", "height": "h"},
                "pressureArrows": [{"side": "left", "direction": "down", "label": "P_gas"}, {"side": "right", "direction": "down", "label": "P_atm"}],
                "pressureRelation": {"lhs": "P_gas", "operator": "=", "base": "P_atm", "terms": [{"operator": "+", "value": "rho g h"}]},
            },
        }


if __name__ == "__main__":
    unittest.main()
