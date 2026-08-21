import hashlib
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from tools.lesson_packet.source_extract import render_source_pages
from tools.lesson_packet.source_evidence import (
    extract_page_evidence,
    normalize_bounds,
    sha256_file,
)


def _write_evidence_fixture(root: Path) -> tuple[Path, Path]:
    image_path = root / "apparatus.png"
    Image.new("RGB", (24, 24), "white").save(image_path)

    pdf = root / "evidence.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(200, 200), pageCompression=0)
    document.setFont("Helvetica", 10)
    document.drawString(20, 180, "Body text remains source evidence.")
    document.drawImage(str(image_path), 20, 110, width=40, height=40)
    document.line(100, 100, 100, 150)
    document.line(100, 100, 170, 100)
    document.line(105, 105, 165, 140)
    document.drawString(20, 90, "Figure 1. Evidence caption")
    document.showPage()
    document.save()

    rendered = root / "pages"
    render_source_pages(pdf, rendered, dpi=72)
    return pdf, rendered


class PageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.pdf, self.rendered = _write_evidence_fixture(self.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_page_evidence_is_one_based_hash_bound_and_deterministic(self):
        first = extract_page_evidence(self.pdf, self.rendered)
        second = extract_page_evidence(self.pdf, self.rendered)

        self.assertEqual(first, second)
        self.assertEqual(first[0]["sourcePage"], 1)
        self.assertRegex(first[0]["sourceSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(first[0]["sourceSha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertEqual(first[0]["pageSize"], (200.0, 200.0))
        self.assertEqual(first[0]["renderedPage"], "page-001.png")
        for record in first[0]["textBlocks"] + first[0]["visualAtoms"]:
            self.assertRegex(record["id"], r"^page-001-[a-z-]+-\d{3}$")
            self.assertEqual(record["sourcePage"], 1)
            self.assertEqual(record["sourceSha256"], first[0]["sourceSha256"])
            left, top, right, bottom = record["bounds"]
            self.assertTrue(0 <= left < right <= 1, record)
            self.assertTrue(0 <= top < bottom <= 1, record)

    def test_text_roles_begin_as_body_or_caption_never_internal(self):
        page = extract_page_evidence(self.pdf, self.rendered)[0]

        self.assertTrue(all(x["role"] in {"body", "caption"} for x in page["textBlocks"]))
        self.assertIn("body", {block["role"] for block in page["textBlocks"]})
        self.assertIn("caption", {block["role"] for block in page["textBlocks"]})
        for block in page["textBlocks"]:
            self.assertIsInstance(block["text"], str)
            self.assertEqual(block["matchText"], " ".join(block["text"].replace("\u00ad", "").split()))

    def test_visual_atoms_preserve_native_image_and_vector_geometry_without_decisions(self):
        page = extract_page_evidence(self.pdf, self.rendered)[0]

        kinds = {atom["kind"] for atom in page["visualAtoms"]}
        self.assertIn("nativeImage", kinds)
        self.assertIn("vectorGroup", kinds)
        self.assertTrue(all("recommendation" not in atom for atom in page["visualAtoms"]))
        self.assertTrue(all("role" not in atom for atom in page["visualAtoms"]))

    def test_public_inputs_fail_with_deterministic_value_errors(self):
        missing_pdf = self.root / "missing.pdf"
        cases = (
            lambda: sha256_file(missing_pdf),
            lambda: normalize_bounds(None, 200, 200),
            lambda: normalize_bounds((0, 0, 1, 1), 0, 200),
            lambda: normalize_bounds((0, 0, math.inf, 1), 200, 200),
            lambda: extract_page_evidence(missing_pdf, self.rendered),
            lambda: extract_page_evidence(self.pdf, self.root / "missing-pages"),
        )
        for call in cases:
            with self.subTest(call=call):
                with self.assertRaises(ValueError) as error:
                    call()
                self.assertTrue(str(error.exception))
