import tempfile
import unittest
from pathlib import Path

from PIL import Image as PillowImage
from pypdf import PdfReader
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

    def test_templates_render_a_controlled_two_page_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "two-pages.pdf"
            doc = SimpleDocTemplate(str(pdf), pagesize=A4)
            doc.addPageTemplates(build_page_templates(doc, role="student"))
            styles = make_styles()
            doc.build([
                Paragraph("Two columns", styles["body"]),
                Paragraph("column content", styles["body"]),
                *full_width_flowables(Paragraph("Full width", styles["body"])),
                Paragraph("full width content", styles["body"]),
            ])
            self.assertTrue(pdf.is_file())
            self.assertGreater(pdf.stat().st_size, 1000)
            self.assertEqual(len(PdfReader(pdf).pages), 2)


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

    def test_reconstruction_rejects_missing_semantic_constraints(self):
        valid = self._particle_visual()
        for field in ("entities", "relationships", "invariants"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                reconstructed_visual_flowable({**valid, field: []})

    def test_apparatus_uses_explicit_tube_schema_labels_directions_and_sign(self):
        visual = {
            "title": "Do not derive labels from this title",
            "diagramType": "apparatus_tube",
            "entities": ["left arm", "right arm", "mercury"],
            "relationships": ["liquid height difference"],
            "invariants": ["both arms share one liquid"],
            "schema": {
                "tube": {"shape": "U", "sealedSide": "left", "openSide": "right", "liquidLevels": {"left": 0.30, "right": 0.64}},
                "labels": {"sealed": "P_gas", "open": "P_atm", "liquid": "Hg", "height": "h"},
                "pressureArrows": [{"side": "left", "direction": "down", "label": "P_gas"}, {"side": "right", "direction": "down", "label": "P_atm"}],
                "pressureRelation": {"expression": "P_gas = P_atm + rho g h", "sign": "+"},
            },
        }
        flowable = reconstructed_visual_flowable(visual)
        self.assertEqual(flowable._lesson_semantic_labels, ["P_gas", "P_atm", "Hg", "h", "P_gas = P_atm + rho g h"])
        self.assertEqual(flowable._lesson_arrow_directions, ["left:down", "right:down"])
        self.assertNotIn(visual["title"], flowable._lesson_semantic_labels)

    def test_j_tube_places_its_closed_arm_from_the_sealed_side_schema(self):
        visual = {
            "diagramType": "apparatus_tube",
            "entities": ["sealed gas", "air", "liquid"],
            "relationships": ["liquid height difference"],
            "invariants": ["one connected liquid"],
            "schema": {
                "tube": {"shape": "J", "sealedSide": "right", "openSide": "left", "liquidLevels": {"left": 0.64, "right": 0.30}},
                "labels": {"sealed": "Pgas", "open": "Patm", "liquid": "Hg"},
                "pressureRelation": {"expression": "Pgas = Patm + rho g h", "sign": "+"},
            },
        }
        flowable = reconstructed_visual_flowable(visual)
        top_segments = [
            (shape.x1, shape.x2)
            for shape in flowable.contents
            if hasattr(shape, "x1") and hasattr(shape, "x2") and shape.y1 == 180 and shape.y2 == 180
        ]
        self.assertIn((251, 275), top_segments)

    def test_reconstructs_particle_molecular_graph_and_energy_primitives(self):
        visuals = [
            self._particle_visual(),
            {
                "diagramType": "molecular_interactions",
                "entities": ["water molecule", "water molecule"],
                "relationships": ["hydrogen bond"],
                "invariants": ["opposite partial charges attract"],
                "schema": {"molecules": [{"label": "H2O", "atoms": ["O", "H", "H"]}, {"label": "H2O", "atoms": ["O", "H", "H"]}], "interaction": {"label": "hydrogen bond", "direction": "between"}},
            },
            {
                "diagramType": "cartesian_graph",
                "entities": ["pressure", "volume"],
                "relationships": ["inverse trend"],
                "invariants": ["temperature is constant"],
                "axes": ["V / L", "P / kPa"],
                "schema": {"points": [[1, 5], [2, 2.5]], "trend": {"label": "T constant", "direction": "decreasing"}},
            },
            {
                "diagramType": "energy_profile",
                "entities": ["reactants", "products", "transition state"],
                "relationships": ["activation barrier"],
                "invariants": ["products have lower energy"],
                "schema": {"states": [{"label": "Reactants", "energy": 0.3}, {"label": "Products", "energy": 0.1}], "transition": {"label": "Ea", "energy": 0.8}, "reactionDirection": "forward"},
            },
        ]
        for visual in visuals:
            with self.subTest(diagram=visual["diagramType"]):
                flowable = reconstructed_visual_flowable(visual)
                self.assertGreater(flowable.width, 0)
                self.assertGreater(flowable.height, 0)

    @staticmethod
    def _particle_visual():
        return {
            "diagramType": "particle_model",
            "entities": ["gas particle", "container"],
            "relationships": ["particles collide with wall"],
            "invariants": ["container volume is constant"],
            "schema": {"particles": [{"label": "gas", "x": 0.25, "y": 0.35}, {"label": "gas", "x": 0.65, "y": 0.65}], "containerLabel": "constant V", "arrows": [{"label": "collision", "direction": "right"}]},
        }


if __name__ == "__main__":
    unittest.main()
