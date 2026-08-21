import copy
import tempfile
import unittest
from pathlib import Path

from PIL import Image as PillowImage
from pypdf import PdfReader
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Ellipse, Line
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

    def test_source_crop_flowable_rejects_non_object_visual_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "visual must be an object"):
                source_crop_flowable([], Path(directory))

    def test_reconstruction_rejects_non_positive_group_scales(self):
        for scale in (0, [0, 0.5]):
            visual = self._phase_curve_visual()
            visual["schema"]["scene"]["primitives"][2] = {
                "kind": "group", "semanticId": "path",
                "transform": {"translate": [0.5, 0.5], "scale": scale},
                "primitives": [{"kind": "line", "x1": 0.2, "y1": 0.2, "x2": 0.8, "y2": 0.8}],
            }
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

    def test_reconstruction_caps_nesting_depth_and_nested_repeat_expansion(self):
        nested_groups = {"kind": "line", "x1": 0.2, "y1": 0.2, "x2": 0.8, "y2": 0.8}
        for _ in range(9):
            nested_groups = {"kind": "group", "primitives": [nested_groups]}
        too_deep = self._phase_curve_visual()
        too_deep["schema"]["scene"]["primitives"][2] = {
            "kind": "group", "semanticId": "path", "primitives": [nested_groups]
        }

        too_many = self._phase_curve_visual()
        too_many["schema"]["scene"]["primitives"][2] = {
            "kind": "repeat", "semanticId": "path", "count": 101,
            "translate": [0, 0],
            "primitive": {
                "kind": "repeat", "count": 100, "translate": [0, 0],
                "primitive": {"kind": "circle", "cx": 0.5, "cy": 0.5, "r": 0.01},
            },
        }

        for name, visual in (("depth", too_deep), ("repeat-budget", too_many)):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

    def test_reconstruction_scales_oversized_scene_proportionally_into_content_box(self):
        visual = self._phase_curve_visual()
        visual["schema"]["scene"]["canvas"].update({"width": 1000, "height": 800})

        drawing = reconstructed_visual_flowable(visual)

        from tools.lesson_packet.visuals import MAX_VISUAL_HEIGHT, MAX_VISUAL_WIDTH

        self.assertLessEqual(drawing.width, MAX_VISUAL_WIDTH)
        self.assertLessEqual(drawing.height, MAX_VISUAL_HEIGHT)
        self.assertAlmostEqual(drawing.width / drawing.height, 1000 / 800)

    def test_reconstruction_rejects_style_on_group_and_repeat_nodes(self):
        for kind, node in (
            ("group", {"kind": "group", "style": {}, "primitives": [{"kind": "line", "x1": 0.2, "y1": 0.2, "x2": 0.8, "y2": 0.8}]}),
            ("repeat", {"kind": "repeat", "style": {}, "count": 2, "translate": [0, 0], "primitive": {"kind": "circle", "cx": 0.5, "cy": 0.5, "r": 0.01}}),
        ):
            visual = self._phase_curve_visual()
            node["semanticId"] = "path"
            visual["schema"]["scene"]["primitives"][2] = node
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

    def test_pressure_and_arrow_labels_reject_equation_syntax(self):
        visual = self._tube_visual()
        visual["schema"]["pressureArrows"][0]["label"] = "P_atm = P_gas - rho g h"

        with self.assertRaisesRegex(ValueError, "label contains forbidden equation syntax"):
            reconstructed_visual_flowable(visual)

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
        with self.assertRaisesRegex(ValueError, "canvas"):
            reconstructed_visual_flowable(transformed_outside_canvas)

    def test_visible_bounds_include_strokes_arrowheads_labels_markers_and_repeats(self):
        cases = {}

        thick_line = self._phase_curve_visual()
        thick_line["schema"]["scene"]["primitives"][2] = {
            "kind": "line", "semanticId": "path", "x1": 0.003, "y1": 0.30,
            "x2": 0.40, "y2": 0.30, "style": {"strokeWidth": 5},
        }
        cases["line-width"] = thick_line

        arrowhead = self._phase_curve_visual()
        arrowhead["schema"]["scene"]["primitives"][2] = {
            "kind": "arrow", "semanticId": "path", "x1": 0.70, "y1": 0.50,
            "x2": 0.995, "y2": 0.50, "label": "direction",
        }
        cases["arrowhead"] = arrowhead

        text = self._phase_curve_visual()
        text["schema"]["scene"]["primitives"][2] = {
            "kind": "text", "semanticId": "path", "x": 0.99, "y": 0.50,
            "text": "outside", "style": {"textAnchor": "start"},
        }
        cases["text"] = text

        marker = self._phase_curve_visual()
        marker["schema"]["scene"]["primitives"][2] = {
            "kind": "marker", "semanticId": "path", "x": 0.99, "y": 0.50,
            "marker": "dot", "size": 20,
        }
        cases["marker-radius"] = marker

        circle = self._phase_curve_visual()
        circle["schema"]["scene"]["primitives"][2] = {
            "kind": "circle", "semanticId": "path", "cx": 0.99, "cy": 0.50, "r": 0.02,
        }
        cases["circle-radius"] = circle

        ellipse = self._phase_curve_visual()
        ellipse["schema"]["scene"]["primitives"][2] = {
            "kind": "ellipse", "semanticId": "path", "cx": 0.50, "cy": 0.99,
            "rx": 0.02, "ry": 0.02,
        }
        cases["ellipse-radius"] = ellipse

        dimension = self._phase_curve_visual()
        dimension["schema"]["scene"]["primitives"][2] = {
            "kind": "dimension", "semanticId": "path", "x1": 0.40, "y1": 0.97,
            "x2": 0.60, "y2": 0.97, "label": "dimension label",
        }
        cases["dimension-label"] = dimension

        repeated = self._phase_curve_visual()
        repeated["schema"]["scene"]["primitives"][2] = {
            "kind": "repeat", "semanticId": "path", "count": 3,
            "translate": [0.10, 0],
            "primitive": {"kind": "marker", "x": 0.78, "y": 0.50, "size": 20},
        }
        cases["transformed-repeat"] = repeated

        for name, visual in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "canvas"):
                    reconstructed_visual_flowable(visual)

    def test_axis_ticks_and_optional_labels_are_rendered_as_artifacts(self):
        visual = self._phase_curve_visual()
        visual["schema"]["scene"]["primitives"][0]["ticks"] = [
            {"position": 0.25, "label": "cold"},
            {"position": 0.75},
        ]

        flowable = reconstructed_visual_flowable(visual)

        self.assertIn("cold", flowable._lesson_semantic_labels)
        self.assertEqual(flowable._lesson_axis_tick_count, 2)
        self.assertGreaterEqual(sum(isinstance(item, Line) for item in flowable.contents), 5)

    def test_axis_tick_labels_are_included_in_visible_bounds(self):
        visual = self._phase_curve_visual()
        visual["schema"]["scene"]["primitives"][0].update({
            "y1": 0.031,
            "y2": 0.031,
            "ticks": [{"position": 0.5, "label": "below"}],
        })
        with self.assertRaisesRegex(ValueError, "canvas"):
            reconstructed_visual_flowable(visual)

    def test_nonuniform_group_scaling_turns_a_circle_into_the_correct_ellipse(self):
        visual = self._phase_curve_visual()
        visual["schema"]["scene"]["primitives"][2] = {
            "kind": "group", "semanticId": "path", "transform": {"scale": [0.5, 0.8]},
            "primitives": [{"kind": "circle", "cx": 0.50, "cy": 0.50, "r": 0.05}],
        }

        flowable = reconstructed_visual_flowable(visual)
        ellipse = next(item for item in flowable.contents if isinstance(item, Ellipse))

        self.assertAlmostEqual(ellipse.rx, 5.625)
        self.assertAlmostEqual(ellipse.ry, 9.0)

    def test_semantic_ids_are_globally_unique_and_relationship_refs_are_nonempty(self):
        collisions = []

        entity_collision = self._phase_curve_visual()
        entity_collision["entities"][0]["id"] = "path"
        collisions.append(("entity-primitive", entity_collision))

        relationship_collision = self._phase_curve_visual()
        relationship_collision["relationships"][0]["id"] = "path"
        collisions.append(("relationship-primitive", relationship_collision))

        duplicate_relationship = self._phase_curve_visual()
        duplicate_relationship["relationships"].append(copy.deepcopy(duplicate_relationship["relationships"][0]))
        collisions.append(("duplicate-relationship", duplicate_relationship))

        duplicate_entity = self._phase_curve_visual()
        duplicate_entity["entities"][1]["id"] = duplicate_entity["entities"][0]["id"]
        collisions.append(("duplicate-entity", duplicate_entity))

        duplicate_primitive = self._phase_curve_visual()
        duplicate_primitive["schema"]["scene"]["primitives"][1]["semanticId"] = "path"
        collisions.append(("duplicate-primitive", duplicate_primitive))

        invariant_collision = self._phase_curve_visual()
        invariant_collision["invariants"][0]["id"] = "path"
        collisions.append(("invariant-primitive", invariant_collision))

        constraint_collision = self._phase_curve_visual()
        constraint_collision["constraints"][0]["id"] = "path"
        collisions.append(("constraint-primitive", constraint_collision))

        for name, visual in collisions:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "duplicate semantic id"):
                    reconstructed_visual_flowable(visual)

        empty_refs = self._phase_curve_visual()
        empty_refs["relationships"][0]["primitiveIds"] = []
        with self.assertRaisesRegex(ValueError, "primitiveIds"):
            reconstructed_visual_flowable(empty_refs)

    def test_malformed_nested_json_always_raises_value_error(self):
        cases = []

        primitive = self._phase_curve_visual()
        primitive["schema"]["scene"]["primitives"] = [None]
        cases.append(("primitive-null", primitive))

        style = self._phase_curve_visual()
        style["schema"]["scene"]["primitives"][0]["style"] = {"stroke": []}
        cases.append(("style-list", style))

        refs = self._phase_curve_visual()
        refs["relationships"][0]["primitiveIds"] = [{}]
        cases.append(("ref-object", refs))

        label = self._phase_curve_visual()
        label["schema"]["scene"]["primitives"][0]["label"] = 7
        cases.append(("label-number", label))

        scene = self._phase_curve_visual()
        scene["schema"]["scene"] = 3
        cases.append(("scene-number", scene))

        tube = self._tube_visual()
        tube["schema"]["tube"]["shape"] = []
        cases.append(("tube-list", tube))

        for name, visual in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

    def test_all_malformed_style_ref_label_tube_and_scene_shapes_raise_value_error(self):
        cases = []
        malformed_values = ([], {}, None, 7)

        for value in ([], None, 7):
            visual = self._phase_curve_visual()
            visual["schema"]["scene"]["primitives"][0]["style"] = value
            cases.append((f"style-container-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._phase_curve_visual()
            visual["schema"]["scene"]["primitives"][0]["style"] = {"dash": value}
            cases.append((f"style-token-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._phase_curve_visual()
            visual["relationships"][0]["primitiveIds"] = [value]
            cases.append((f"ref-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._phase_curve_visual()
            visual["schema"]["scene"]["primitives"][0]["label"] = value
            cases.append((f"label-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._hess_path_visual()
            visual["schema"]["scene"]["primitives"][5]["label"] = value
            cases.append((f"connector-label-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._tube_visual()
            visual["schema"]["tube"] = value
            cases.append((f"tube-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._phase_curve_visual()
            visual["schema"]["scene"] = value
            cases.append((f"scene-{type(value).__name__}", visual))
        for value in malformed_values:
            visual = self._phase_curve_visual()
            visual["schema"]["scene"]["primitives"][0]["kind"] = value
            cases.append((f"kind-{type(value).__name__}", visual))

        for name, visual in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

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

        injected_factor = self._tube_visual()
        injected_factor["schema"]["pressureRelation"]["terms"][0]["factors"] = ["rho g h + injected"]
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            reconstructed_visual_flowable(injected_factor)

        legacy_value = self._tube_visual()
        legacy_value["schema"]["pressureRelation"]["terms"][0] = {"operator": "+", "value": "rho g h"}
        with self.assertRaisesRegex(ValueError, "unknown field"):
            reconstructed_visual_flowable(legacy_value)

        injected_symbol = self._tube_visual()
        injected_symbol["schema"]["pressureRelation"]["lhs"] = "P_gas = malicious"
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            reconstructed_visual_flowable(injected_symbol)

        equal_levels = self._tube_visual()
        equal_levels["schema"]["tube"]["liquidLevels"] = {"left": 0.4, "right": 0.4}
        with self.assertRaisesRegex(ValueError, "differ"):
            reconstructed_visual_flowable(equal_levels)

        swapped_symbols = self._tube_visual()
        swapped_symbols["schema"]["pressureRelation"].update({
            "lhs": "atmospheric_pressure",
            "base": "gas_pressure",
        })
        with self.assertRaisesRegex(ValueError, "gas_pressure.*atmospheric_pressure"):
            reconstructed_visual_flowable(swapped_symbols)

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
                "pressureRelation": {
                    "lhs": "gas_pressure", "operator": "=", "base": "atmospheric_pressure",
                    "terms": [{"operator": "+", "factors": ["density", "gravity", "height"]}],
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
