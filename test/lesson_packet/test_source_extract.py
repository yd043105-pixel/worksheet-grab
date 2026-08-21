import tempfile
import unittest
import json
from pathlib import Path

from pypdf import PdfWriter

from tools.lesson_packet.source_extract import (
    inventory_sources,
    parse_lesson_count,
    render_all_sources,
    render_source_pages,
    validate_visual_map,
)


class SourceExtractTests(unittest.TestCase):
    def test_parse_lesson_count(self):
        self.assertEqual(parse_lesson_count("1-1-1. 기체의 성질(2차시 분량).pdf"), 2)
        self.assertEqual(parse_lesson_count("1-1-2. 이상기체 방정식(1차시 분량).pdf"), 1)

    def test_inventory_ignores_output_subdirectories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = PdfWriter()
            writer.add_blank_page(width=595.28, height=841.89)
            with (root / "a(1차시 분량).pdf").open("wb") as stream:
                writer.write(stream)
            (root / "학습지").mkdir()
            with (root / "학습지" / "ignored(1차시 분량).pdf").open("wb") as stream:
                writer.write(stream)
            self.assertEqual(
                [x["sourceFile"] for x in inventory_sources(root)],
                ["a(1차시 분량).pdf"],
            )

    def test_render_source_pages_uses_one_based_page_names_and_source_hash_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "fixture.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_blank_page(width=72, height=72)
            with pdf.open("wb") as stream:
                writer.write(stream)

            pages = render_source_pages(pdf, root / "pages")

            self.assertEqual([page.name for page in pages], ["page-001.png", "page-002.png"])
            self.assertTrue(all(page.is_file() for page in pages))
            metadata = json.loads((root / "pages" / "rendered-pages.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["sourceFile"], "fixture.pdf")
            self.assertEqual(metadata["pageCount"], 2)
            self.assertRegex(metadata["sourceSha256"], r"^[0-9a-f]{64}$")

    def test_render_all_uses_the_same_learning_sheet_exclusion_as_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture(1차시 분량).pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with source.open("wb") as stream:
                writer.write(stream)
            for role in ("교사용", "학생용"):
                duplicate = root / "학습지" / role / source.name
                duplicate.parent.mkdir(parents=True, exist_ok=True)
                duplicate.write_bytes(source.read_bytes())
            index = root / "source-index.json"
            index.write_text(json.dumps(inventory_sources(root)), encoding="utf-8")

            results = render_all_sources(index, root / "pages", root)

            self.assertEqual(results, [{"sourceFile": source.name, "renderedPages": 1}])


class VisualMapTests(unittest.TestCase):
    def test_visual_crop_bounds_are_normalized(self):
        errors = validate_visual_map(
            {"sourcePage": 1, "crop": [0.1, 0.2, 0.9, 0.8], "reuseMode": "crop"}
        )
        self.assertEqual(errors, [])
        self.assertIn(
            "crop-out-of-bounds",
            validate_visual_map({"sourcePage": 1, "crop": [-0.1, 0, 1, 1], "reuseMode": "crop"}),
        )

    def test_visual_map_requires_one_based_pages_and_valid_semantic_fields(self):
        valid = {
            "id": "gas-particles",
            "sourcePage": 1,
            "figureLabel": "particle model",
            "purpose": "Explain pressure",
            "reuseMode": "reconstruct",
            "entities": ["particle", "container"],
            "relationships": ["particles collide with the container"],
            "invariants": ["container volume is constant"],
            "axes": [],
            "units": [],
        }
        self.assertEqual(validate_visual_map(valid), [])
        self.assertIn("source-page-must-be-one-based", validate_visual_map({**valid, "sourcePage": 0}))
        self.assertIn("figure-label-missing", validate_visual_map({**valid, "figureLabel": " "}))
        self.assertIn("reuse-mode-invalid", validate_visual_map({**valid, "reuseMode": "copy"}))

    def test_visual_map_rejects_a_stale_render_hash(self):
        entry = {"sourcePage": 1, "reuseMode": "crop", "sourceSha256": "old-hash"}
        metadata = {"sourceSha256": "current-hash", "pageCount": 1}
        self.assertIn("source-hash-stale", validate_visual_map(entry, metadata))
