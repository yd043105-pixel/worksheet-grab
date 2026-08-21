import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from tools.lesson_packet.source_extract import inventory_sources, parse_lesson_count


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
            self.assertEqual(
                [x["sourceFile"] for x in inventory_sources(root)],
                ["a(1차시 분량).pdf"],
            )
