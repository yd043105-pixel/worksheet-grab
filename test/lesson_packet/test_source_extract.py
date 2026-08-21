import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

import tools.lesson_packet.source_extract as source_extract
from tools.lesson_packet.source_extract import (
    extract_page_evidence,
    extract_page_regions,
    inventory_sources,
    link_visual_context,
    parse_lesson_count,
    rank_visual_candidate,
    render_all_sources,
    render_source_pages,
    validate_visual_map,
    _audit_representatives,
)
from tools.lesson_packet.visuals import reconstructed_visual_flowable
from fixtures import valid_lesson_dict


def _write_region_fixture(root: Path) -> tuple[Path, Path]:
    image_path = root / "apparatus.png"
    image = Image.new("RGB", (80, 60), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((8, 8, 72, 52), outline="black", width=3)
    drawing.line((40, 8, 40, 52), fill="black", width=2)
    image.save(image_path)

    pdf_path = root / "regions.pdf"
    document = canvas.Canvas(str(pdf_path), pagesize=(400, 400), pageCompression=0)
    document.setFont("Helvetica", 11)
    document.drawString(30, 365, "Figure 1 shows the gas pressure apparatus.")
    document.drawString(30, 345, "Ordinary body glyphs remain positioned text evidence.")
    document.drawImage(str(image_path), 45, 225, width=80, height=60)
    document.setFont("Helvetica", 9)
    document.drawString(45, 210, "Figure 1. Gas pressure apparatus")

    for x in (250, 300, 350):
        document.line(x, 225, x, 285)
    for y in (225, 255, 285):
        document.line(250, y, 350, y)
    document.setFont("Helvetica", 8)
    document.drawString(257, 263, "time")
    document.drawString(305, 263, "rate")
    document.drawString(257, 233, "1")
    document.drawString(305, 233, "2")
    document.drawString(250, 210, "Table 1. Reaction rate data")

    document.line(45, 75, 45, 160)
    document.line(45, 75, 145, 75)
    document.line(50, 85, 85, 115)
    document.line(85, 115, 140, 145)
    document.setFont("Helvetica", 9)
    document.drawString(45, 58, "Figure 2. Concentration-time graph")
    document.showPage()
    document.save()

    rendered = root / "pages"
    render_source_pages(pdf_path, rendered, dpi=72)
    return pdf_path, rendered


def _relevant_candidate() -> dict:
    return {
        "id": "page-001-image-001",
        "kind": "nativeImage",
        "sourcePage": 1,
        "sourceSha256": "a" * 64,
        "bounds": [0.1, 0.2, 0.5, 0.6],
        "captionText": "Figure 1. Gas pressure apparatus and pressure difference",
        "explicitReferences": ["Figure 1 shows the gas pressure apparatus."],
        "nearbyText": [
            {"text": "Compare gas pressure with atmospheric pressure.", "distance": 0.03}
        ],
    }


def _lesson_evidence() -> dict:
    return {
        "title": "A title is metadata, not ranking evidence",
        "sections": {
            "representation": {
                "text": "The gas pressure apparatus represents pressure difference.",
                "entities": ["gas", "apparatus"],
                "quantities": ["pressure", "pressure difference"],
            },
            "question": {
                "text": "How does gas pressure compare with atmospheric pressure?",
                "entities": ["gas"],
                "quantities": ["pressure"],
            },
        },
    }


def _composite_page_fixture() -> dict:
    source_hash = "c" * 64

    def region(region_id: str, bounds: list[float], **extra) -> dict:
        return {
            "id": region_id,
            "sourcePage": 1,
            "sourceSha256": source_hash,
            "bounds": bounds,
            **extra,
        }

    return {
        "id": "page-001",
        "sourcePage": 1,
        "sourceSha256": source_hash,
        "pageWidth": 400.0,
        "pageHeight": 600.0,
        "bounds": [0.0, 0.0, 1.0, 1.0],
        "renderedPage": "page-001.png",
        "textBlocks": [
            region(
                "page-001-text-001",
                [0.1, 0.08, 0.72, 0.12],
                text="Figure 1 shows the gas pressure apparatus and graph.",
                sourceText="Figure 1 shows the gas pressure apparatus and graph.",
            ),
            region(
                "page-001-text-002",
                [0.52, 0.49, 0.74, 0.53],
                text="pressure (atm)",
                sourceText="pressure (atm)",
            ),
            region(
                "page-001-text-003",
                [0.1, 0.61, 0.8, 0.66],
                text="A separate explanation divides the neighboring figures on this page.",
                sourceText="A separate explanation divides the neighboring figures on this page.",
            ),
            region(
                "page-001-text-004",
                [0.1, 0.67, 0.5, 0.69],
                text="Figure 2 shows a separate energy profile.",
                sourceText="Figure 2 shows a separate energy profile.",
            ),
        ],
        "captionBlocks": [
            region(
                "page-001-caption-001",
                [0.1, 0.55, 0.76, 0.59],
                text="Figure 1. J-tube apparatus and Boyle graph",
                sourceText="Figure 1. J-tube apparatus and Boyle graph",
                label="Figure 1",
            ),
            region(
                "page-001-caption-002",
                [0.1, 0.84, 0.45, 0.88],
                text="Figure 2. Separate energy profile",
                sourceText="Figure 2. Separate energy profile",
                label="Figure 2",
            ),
        ],
        "visualCandidates": [
            region("page-001-native-image-001", [0.82, 0.05, 0.96, 0.22], kind="nativeImage", objectCount=1),
            region("page-001-vector-group-001", [0.1, 0.28, 0.25, 0.5], kind="vectorGroup", objectCount=4),
            region("page-001-vector-group-002", [0.27, 0.37, 0.34, 0.43], kind="vectorGroup", objectCount=2),
            region("page-001-vector-group-003", [0.36, 0.28, 0.49, 0.5], kind="vectorGroup", objectCount=4),
            region("page-001-vector-group-004", [0.52, 0.22, 0.75, 0.5], kind="vectorGroup", objectCount=6),
            region("page-001-vector-group-005", [0.1, 0.71, 0.38, 0.81], kind="vectorGroup", objectCount=5),
        ],
    }


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


class SourceRegionTests(unittest.TestCase):
    def test_representative_blockers_fail_the_aggregate_audit(self):
        representatives = [
            {
                "id": "required-j-tube",
                "blocker": "caption-complete-candidate-missing",
                "fragmentationDetected": [],
                "axisOmissionsDetected": [],
                "neighborMergesDetected": [],
                "decorativePortraitRetention": [],
            },
            {
                "id": "osmosis-figures",
                "blocker": None,
                "fragmentationDetected": ["candidate-001"],
                "axisOmissionsDetected": [],
                "neighborMergesDetected": [],
                "decorativePortraitRetention": [],
            },
        ]

        audit = source_extract._representative_audit_summary(representatives)

        self.assertEqual(
            audit["errors"],
            [
                "representative:required-j-tube:caption-complete-candidate-missing",
                "representative:osmosis-figures:fragmentationDetected:candidate-001",
            ],
        )
        self.assertFalse(audit["invariants"]["allRepresentativeEvidenceValid"])

    def test_representative_audit_matches_candidate_related_text(self):
        candidate = {
            "id": "rate-graph",
            "captionText": "의 몰농도 | 그림 - 2 |",
            "relatedText": [{"text": "반응 속도는 시간에 따라 감소한다."}],
            "internalText": [{"text": "농도 그래프 (M) 시간 (s)"}],
            "explicitReferences": ["reaction rate reference"],
            "decision": "reuse",
            "bounds": [0.2, 0.2, 0.8, 0.7],
            "visualAtomIds": [],
        }
        unrelated_candidate = {
            **candidate,
            "id": "unrelated-figure",
            "captionText": "일반적인 삽화",
            "relatedText": [],
            "internalText": [],
            "explicitReferences": [],
        }
        manifest = {
            "sourceStem": "4-1-1. 화학 반응 속도(2차시 분량)",
            "pages": [{
                "textBlocks": [{
                    "role": "caption",
                    "text": "의 몰농도 | 그림 - 2 |",
                    "bounds": [0.2, 0.75, 0.8, 0.8],
                }, {
                    "role": "body",
                    "text": "반응 속도는 이 페이지의 다른 설명에만 있다.",
                    "bounds": [0.05, 0.05, 0.95, 0.1],
                }],
                "visualAtoms": [],
                "visualCandidates": [candidate, unrelated_candidate],
            }],
        }

        representative = _audit_representatives([manifest])[-3]

        self.assertEqual(representative["id"], "reaction-rate-graph")
        self.assertEqual(representative["candidateIds"], ["rate-graph"])

    def test_canonical_page_evidence_is_available_from_the_compatibility_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf, rendered = _write_region_fixture(Path(directory))

            page = extract_page_evidence(pdf, rendered)[0]

            self.assertEqual(page["sourcePage"], 1)
            self.assertEqual(page["renderedPage"], "page-001.png")
            self.assertIn("visualAtoms", page)

    def test_extract_regions_uses_one_based_ids_normalized_bounds_and_source_traceability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, rendered = _write_region_fixture(root)

            pages = extract_page_regions(pdf, rendered)

            self.assertEqual(len(pages), 1)
            page = pages[0]
            self.assertEqual(page["sourcePage"], 1)
            self.assertEqual(page["id"], "page-001")
            self.assertEqual(page["pageWidth"], 400.0)
            self.assertEqual(page["pageHeight"], 400.0)
            self.assertEqual(page["bounds"], [0.0, 0.0, 1.0, 1.0])
            self.assertEqual(page["sourceSha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())
            self.assertEqual(page["renderedPage"], "page-001.png")
            regions = page["textBlocks"] + page["captionBlocks"] + page["visualCandidates"]
            self.assertTrue(regions)
            for region in regions:
                left, top, right, bottom = region["bounds"]
                self.assertTrue(0 <= left < right <= 1, region)
                self.assertTrue(0 <= top < bottom <= 1, region)
                self.assertRegex(region["id"], r"^page-001-[a-z-]+-\d{3}$")
                self.assertEqual(region["sourcePage"], 1)
                self.assertEqual(region["sourceSha256"], page["sourceSha256"])

    def test_extract_regions_separates_positioned_text_and_captions(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf, rendered = _write_region_fixture(Path(directory))

            page = extract_page_regions(pdf, rendered)[0]

            body_text = " ".join(block["text"] for block in page["textBlocks"])
            captions = [block["text"] for block in page["captionBlocks"]]
            self.assertIn("Ordinary body glyphs", body_text)
            self.assertNotIn("Figure 1. Gas pressure apparatus", body_text)
            self.assertIn("Figure 1. Gas pressure apparatus", captions)
            self.assertIn("Table 1. Reaction rate data", captions)
            self.assertIn("Figure 2. Concentration-time graph", captions)
            for block in page["textBlocks"] + page["captionBlocks"]:
                self.assertIn("sourceText", block)
                self.assertNotIn("instruction", block)

    def test_extract_regions_finds_native_image_table_and_connected_vector_group_not_glyphs(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf, rendered = _write_region_fixture(Path(directory))

            candidates = extract_page_regions(pdf, rendered)[0]["visualCandidates"]

            kinds = [candidate["kind"] for candidate in candidates]
            self.assertIn("nativeImage", kinds)
            self.assertIn("table", kinds)
            self.assertIn("vectorGroup", kinds)
            self.assertNotIn("glyph", kinds)
            self.assertNotIn("text", kinds)
            self.assertLessEqual(kinds.count("vectorGroup"), 2)

    def test_extract_regions_uses_rendered_page_as_fallback_without_ocr_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=200)
            with pdf.open("wb") as stream:
                writer.write(stream)
            rendered = root / "pages"
            render_source_pages(pdf, rendered, dpi=72)

            page = extract_page_regions(pdf, rendered)[0]

            self.assertEqual(page["textBlocks"], [])
            self.assertEqual(page["captionBlocks"], [])
            self.assertEqual(
                [(item["kind"], item["bounds"]) for item in page["visualCandidates"]],
                [("renderedFallback", [0.0, 0.0, 1.0, 1.0])],
            )

    def test_extract_regions_excludes_fully_off_page_text_furniture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "off-page-header.pdf"
            document = canvas.Canvas(str(pdf), pagesize=(100, 200), pageCompression=0)
            document.setFont("Helvetica", 10)
            document.drawString(10, 210, "Off-page running header")
            document.drawString(10, 100, "Visible explanation")
            document.showPage()
            document.save()
            rendered = root / "pages"
            render_source_pages(pdf, rendered, dpi=72)

            page = extract_page_regions(pdf, rendered)[0]

            self.assertEqual([block["text"] for block in page["textBlocks"]], ["Visible explanation"])

    def test_extract_regions_excludes_fully_off_page_native_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "mark.png"
            Image.new("RGB", (20, 20), "black").save(image_path)
            pdf = root / "off-page-image.pdf"
            document = canvas.Canvas(str(pdf), pagesize=(100, 200), pageCompression=0)
            document.drawImage(str(image_path), 110, 100, width=20, height=20)
            document.showPage()
            document.save()
            rendered = root / "pages"
            render_source_pages(pdf, rendered, dpi=72)

            page = extract_page_regions(pdf, rendered)[0]

            self.assertEqual(
                [candidate["kind"] for candidate in page["visualCandidates"]],
                ["renderedFallback"],
            )

    def test_link_visual_context_associates_explicit_labels_proximity_and_body_references(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf, rendered = _write_region_fixture(Path(directory))

            page = link_visual_context(extract_page_regions(pdf, rendered))[0]

            image = next(item for item in page["visualCandidates"] if item["kind"] == "nativeImage")
            table = next(item for item in page["visualCandidates"] if item["kind"] == "table")
            graph = min(
                (item for item in page["visualCandidates"] if item["kind"] == "vectorGroup"),
                key=lambda item: item["bounds"][1],
            )
            self.assertTrue(image["captionId"])
            self.assertTrue(image["bodyReferenceIds"])
            self.assertIn("Figure 1", image["captionText"])
            self.assertIn("Table 1", table["captionText"])
            self.assertIn("Figure 2", graph["captionText"])
            self.assertGreater(image["contextScoreBreakdown"]["explicitLabel"], 0)
            self.assertGreater(image["contextScoreBreakdown"]["proximity"], 0)
            self.assertGreater(image["contextScoreBreakdown"]["bodyReference"], 0)

    def test_delimited_caption_label_after_title_links_roman_number_body_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "delimited-caption.pdf"
            document = canvas.Canvas(str(pdf), pagesize=(200, 240), pageCompression=0)
            document.setFont("Helvetica", 10)
            document.drawString(20, 210, "Figure IV-7 shows activation energy.")
            document.line(30, 80, 30, 170)
            document.line(30, 80, 170, 80)
            document.line(35, 90, 90, 150)
            document.line(90, 150, 165, 95)
            document.drawString(20, 60, "Enthalpy | Figure IV-7 | Reaction progress")
            document.showPage()
            document.save()
            rendered = root / "pages"
            render_source_pages(pdf, rendered, dpi=72)

            page = link_visual_context(extract_page_regions(pdf, rendered))[0]

            self.assertEqual([block["text"] for block in page["captionBlocks"]], ["Enthalpy | Figure IV-7 | Reaction progress"])
            graph = next(candidate for candidate in page["visualCandidates"] if candidate["kind"] == "vectorGroup")
            self.assertEqual(graph["captionId"], "page-001-caption-001")
            self.assertEqual(graph["bodyReferenceIds"], ["page-001-text-001"])

    def test_context_linking_canonicalizes_korean_label_when_caption_omits_unit_roman(self):
        page = {
            "id": "page-002",
            "sourcePage": 2,
            "sourceSha256": "b" * 64,
            "pageWidth": 100.0,
            "pageHeight": 100.0,
            "bounds": [0.0, 0.0, 1.0, 1.0],
            "renderedPage": "page-002.png",
            "textBlocks": [{
                "id": "page-002-text-001",
                "sourcePage": 2,
                "sourceSha256": "b" * 64,
                "bounds": [0.1, 0.1, 0.9, 0.2],
                "text": "그림Ⅳ-7에서 정반응의 활성화 에너지를 읽는다.",
                "sourceText": "그림Ⅳ-7에서 정반응의 활성화 에너지를 읽는다.",
            }],
            "captionBlocks": [{
                "id": "page-002-caption-001",
                "sourcePage": 2,
                "sourceSha256": "b" * 64,
                "bounds": [0.1, 0.75, 0.8, 0.8],
                "text": "엔탈피 | 그림 - 7 | 반응의 진행에 따른",
                "sourceText": "엔탈피 | 그림 - 7 | 반응의 진행에 따른",
            }],
            "visualCandidates": [{
                "id": "page-002-vector-group-001",
                "kind": "vectorGroup",
                "sourcePage": 2,
                "sourceSha256": "b" * 64,
                "bounds": [0.1, 0.25, 0.8, 0.7],
                "objectCount": 4,
            }],
        }

        candidate = link_visual_context([page])[0]["visualCandidates"][0]

        self.assertEqual(candidate["captionId"], "page-002-caption-001")
        self.assertEqual(candidate["bodyReferenceIds"], ["page-002-text-001"])

    def test_region_extraction_and_context_linking_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf, rendered = _write_region_fixture(Path(directory))

            first = link_visual_context(extract_page_regions(pdf, rendered))
            second = link_visual_context(extract_page_regions(pdf, rendered))

            self.assertEqual(first, second)
            for page in first:
                for key in ("textBlocks", "captionBlocks", "visualCandidates"):
                    ids = [item["id"] for item in page[key]]
                    self.assertEqual(ids, sorted(ids))

    def test_candidates_all_cli_writes_a_deterministic_source_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "sources"
            source_root.mkdir()
            pdf, fixture_pages = _write_region_fixture(source_root)
            pdf = pdf.rename(source_root / "fixture(1차시 분량).pdf")
            pages_root = root / "pages"
            pages_root.mkdir()
            fixture_pages.rename(pages_root / pdf.stem)
            source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            index = root / "source-index.json"
            index.write_text(
                json.dumps(
                    [{
                        "sourceFile": pdf.name,
                        "sourceStem": pdf.stem,
                        "lessonCount": 1,
                        "pageCount": 1,
                        "sha256": source_hash,
                    }],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "candidates"
            command = [
                sys.executable,
                "tools/lesson_packet/source_extract.py",
                "candidates-all",
                "--index", str(index),
                "--pages", str(pages_root),
                "--out", str(output),
                "--source-root", str(source_root),
            ]

            first = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            manifest_path = output / f"{pdf.stem}.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest["sourceSha256"], source_hash)
            self.assertEqual(manifest["pageCount"], 1)
            self.assertEqual(len(manifest["pages"]), 1)
            second = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(manifest_bytes, manifest_path.read_bytes())

    def test_candidates_all_writes_audit_packets_and_byte_identical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "sources"
            source_root.mkdir()
            pdf, fixture_pages = _write_region_fixture(source_root)
            pdf = pdf.rename(source_root / "fixture(1차시 분량).pdf")
            pages_root = root / "pages"
            pages_root.mkdir()
            fixture_pages.rename(pages_root / pdf.stem)
            source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
            index = root / "source-index.json"
            index.write_text(json.dumps([{
                "sourceFile": pdf.name,
                "sourceStem": pdf.stem,
                "lessonCount": 1,
                "pageCount": 1,
                "sha256": source_hash,
            }], ensure_ascii=False), encoding="utf-8")

            def run(output):
                command = [
                    sys.executable,
                    "tools/lesson_packet/source_extract.py",
                    "candidates-all",
                    "--index", str(index),
                    "--pages", str(pages_root),
                    "--out", str(output),
                    "--source-root", str(source_root),
                ]
                result = subprocess.run(command, check=False, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

            first_out = root / "first"
            second_out = root / "second"
            run(first_out)
            run(second_out)

            audit = json.loads((first_out / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["manifestCount"], 1)
            self.assertEqual(audit["pageCount"], 1)
            self.assertTrue(audit["invariants"]["allSourceHashesValid"])
            self.assertTrue(audit["invariants"]["allBoundsNormalized"])
            self.assertTrue(audit["invariants"]["allCandidatesReviewRequired"])
            self.assertTrue(audit["invariants"]["allRetainedDecisionReasonsPresent"])
            self.assertTrue(audit["invariants"]["allRetainedSupportedSectionsPresent"])
            self.assertNotIn("deterministicSerialization", audit["invariants"])
            self.assertTrue((first_out / "reviews.json").is_file())
            reviews = json.loads((first_out / "reviews.json").read_text(encoding="utf-8"))
            self.assertEqual(len(reviews), audit["reviewCount"])
            crop_paths = list((first_out / "crops").glob("*.png"))
            review_paths = list((first_out / "review").glob("*.png"))
            self.assertEqual(len(crop_paths), len(review_paths))
            self.assertGreater(len(crop_paths), 0)

            manifest = json.loads((first_out / f"{pdf.stem}.json").read_text(encoding="utf-8"))
            for page in manifest["pages"]:
                self.assertEqual(page["sourceSha256"], source_hash)
                for record in page["textBlocks"] + page["visualAtoms"]:
                    self.assertEqual(record["sourcePage"], page["sourcePage"])
                    self.assertEqual(record["sourceSha256"], source_hash)
                    left, top, right, bottom = record["bounds"]
                    self.assertTrue(0 <= left < right <= 1)
                    self.assertTrue(0 <= top < bottom <= 1)
                for candidate in page["visualCandidates"]:
                    self.assertEqual(candidate["sourceSha256"], source_hash)
                    self.assertTrue(candidate["reviewRequired"])
                    self.assertEqual(candidate["reviewStatus"], "pending")
                    self.assertTrue(candidate["cropPath"].startswith("crops/"))
                    self.assertTrue(candidate["reviewPacketPath"].startswith("review/"))
                    self.assertTrue(candidate["decisionReasons"])
                    if candidate["decision"] != "exclude":
                        self.assertTrue(candidate["supportedSection"])
                    if candidate["embeddedTextStatus"] == "unread":
                        self.assertEqual(candidate["decision"], "reconstruct")
                        self.assertTrue(candidate["reconstructionBlocked"])
                        self.assertIn("label-transcription-required", candidate["blockingReasons"])

            self.assertTrue(all(review["status"] == "pending" for review in reviews))

            first_json = sorted(path.relative_to(first_out).as_posix() for path in first_out.rglob("*.json"))
            second_json = sorted(path.relative_to(second_out).as_posix() for path in second_out.rglob("*.json"))
            self.assertEqual(first_json, second_json)
            for relative in first_json:
                self.assertEqual(
                    (first_out / relative).read_bytes(),
                    (second_out / relative).read_bytes(),
                    relative,
                )
            first_png = sorted((path.relative_to(first_out).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                               for path in first_out.rglob("*.png"))
            second_png = sorted((path.relative_to(second_out).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                                for path in second_out.rglob("*.png"))
            self.assertEqual(first_png, second_png)


class VisualCandidateRankingTests(unittest.TestCase):
    def test_relevant_apparatus_candidate_outranks_decorative_candidate_with_explained_scores(self):
        relevant = rank_visual_candidate(_relevant_candidate(), _lesson_evidence())
        decorative = rank_visual_candidate(
            {
                **_relevant_candidate(),
                "id": "page-001-image-002",
                "captionText": "Decorative school mascot",
                "explicitReferences": [],
                "nearbyText": [{"text": "Welcome", "distance": 0.03}],
            },
            _lesson_evidence(),
        )

        self.assertGreater(relevant["score"], decorative["score"])
        self.assertEqual(
            set(relevant["scoreBreakdown"]),
            {"captionReference", "spatialProximity", "termOverlap", "instructionalRole"},
        )
        self.assertEqual(sum(relevant["scoreBreakdown"].values()), relevant["score"])

    def test_retained_candidate_names_supported_section_reasons_and_review_requirement(self):
        ranked = rank_visual_candidate(_relevant_candidate(), _lesson_evidence())

        self.assertIn(ranked["recommendation"], {"reuse", "reconstruct"})
        self.assertIn(
            ranked["supportedSection"],
            {"phenomenon", "explanation", "representation", "workedExample", "question"},
        )
        self.assertTrue(ranked["decisionReasons"])
        self.assertTrue(ranked["reviewRequired"])
        self.assertEqual(ranked["sourcePage"], 1)
        self.assertEqual(ranked["sourceSha256"], "a" * 64)
        self.assertEqual(ranked["cropBounds"], [0.1, 0.2, 0.5, 0.6])

    def test_weak_evidence_and_title_only_overlap_default_to_exclude(self):
        weak = {
            **_relevant_candidate(),
            "captionText": "Decorative border",
            "explicitReferences": [],
            "nearbyText": [],
        }
        title_only = {"title": "Decorative border", "sections": {"explanation": "unrelated"}}

        ranked = rank_visual_candidate(weak, title_only)

        self.assertEqual(ranked["recommendation"], "exclude")
        self.assertIsNone(ranked["supportedSection"])
        self.assertTrue(ranked["decisionReasons"])
        self.assertTrue(ranked["reviewRequired"])

    def test_relevance_hazards_change_reuse_to_reconstruct(self):
        for hazard in (
            "answerLeakage",
            "requiredAnnotation",
            "poorPrintLegibility",
            "cropLosesMeaning",
        ):
            with self.subTest(hazard=hazard):
                candidate = _relevant_candidate()
                candidate["hazards"] = {hazard: True}

                ranked = rank_visual_candidate(candidate, _lesson_evidence())

                self.assertEqual(ranked["recommendation"], "reconstruct")
                self.assertTrue(any(hazard in reason for reason in ranked["decisionReasons"]))
                self.assertTrue(ranked["reviewRequired"])

    def test_clear_relevant_candidate_without_hazard_recommends_reuse(self):
        ranked = rank_visual_candidate(_relevant_candidate(), _lesson_evidence())

        self.assertEqual(ranked["recommendation"], "reuse")
        self.assertTrue(ranked["reviewRequired"])

    def test_malformed_inputs_raise_deterministic_value_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed_calls = (
                lambda: extract_page_regions(root / "missing.pdf", root),
                lambda: link_visual_context([None]),
                lambda: rank_visual_candidate([], {}),
                lambda: rank_visual_candidate(_relevant_candidate(), {"sections": []}),
            )
            for call in malformed_calls:
                with self.subTest(call=call):
                    with self.assertRaises(ValueError) as error:
                        call()
                    self.assertTrue(str(error.exception))


class VisualMapTests(unittest.TestCase):
    def test_reconstruct_visual_contract_is_validated_upstream_and_consumed_downstream(self):
        visual = valid_lesson_dict()["visuals"][0]
        self.assertEqual([], validate_visual_map(visual))
        self.assertGreater(reconstructed_visual_flowable(visual).width, 0)

    def test_reconstruct_requires_scene_but_crop_does_not(self):
        reconstruct = {"sourcePage": 1, "reuseMode": "reconstruct", "entities": [], "relationships": [], "invariants": []}
        self.assertIn("reconstruct-scene-missing", validate_visual_map(reconstruct))
        crop = {"sourcePage": 1, "reuseMode": "crop", "crop": [0, 0, 1, 1]}
        self.assertEqual([], validate_visual_map(crop))

    def test_reconstruct_validation_deeply_matches_renderer(self):
        cases = []

        bad_style = valid_lesson_dict()["visuals"][0]
        bad_style["schema"]["scene"]["primitives"][0]["style"] = {"stroke": "red"}
        cases.append(("style", bad_style))

        bad_reference = valid_lesson_dict()["visuals"][0]
        bad_reference["relationships"][0]["to"] = "missing"
        cases.append(("reference", bad_reference))

        bad_scene = valid_lesson_dict()["visuals"][0]
        bad_scene["schema"]["scene"]["primitives"] = [None]
        cases.append(("scene", bad_scene))

        clipped_marker = valid_lesson_dict()["visuals"][0]
        clipped_marker["schema"]["scene"]["primitives"][0] = {
            "kind": "marker", "semanticId": "particle-path", "x": 0.99, "y": 0.5, "size": 20,
        }
        cases.append(("bounds", clipped_marker))

        for name, visual in cases:
            with self.subTest(name=name):
                errors = validate_visual_map(visual)
                self.assertIn("reconstruct-invalid", errors)
                with self.assertRaises(ValueError):
                    reconstructed_visual_flowable(visual)

    def test_crop_maps_ignore_reconstruction_only_fields(self):
        crop = {
            "sourcePage": 1,
            "reuseMode": "crop",
            "crop": [0, 0, 1, 1],
            "schema": {"scene": None},
            "entities": 7,
            "relationships": {},
            "invariants": None,
        }
        self.assertEqual([], validate_visual_map(crop))

    def test_visual_map_rejects_malformed_json_without_raw_exceptions(self):
        for entry in ([], None, 3, "visual"):
            with self.subTest(entry=entry):
                self.assertEqual(["visual-invalid"], validate_visual_map(entry))
        visual = valid_lesson_dict()["visuals"][0]
        visual["reuseMode"] = []
        self.assertIn("reuse-mode-invalid", validate_visual_map(visual))
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
        valid = copy.deepcopy(valid_lesson_dict()["visuals"][0])
        self.assertEqual(validate_visual_map(valid), [])
        self.assertIn("source-page-must-be-one-based", validate_visual_map({**valid, "sourcePage": 0}))
        self.assertIn("figure-label-missing", validate_visual_map({**valid, "figureLabel": " "}))
        self.assertIn("reuse-mode-invalid", validate_visual_map({**valid, "reuseMode": "copy"}))

    def test_visual_map_rejects_a_stale_render_hash(self):
        entry = {"sourcePage": 1, "reuseMode": "crop", "sourceSha256": "old-hash"}
        metadata = {"sourceSha256": "current-hash", "pageCount": 1}
        self.assertIn("source-hash-stale", validate_visual_map(entry, metadata))
