import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image as PillowImage, ImageChops

from tools.lesson_packet.visual_review import REQUIRED_CHECKS, render_review_packet, validate_review


SOURCE_HASH = "a" * 64


def candidate(**overrides):
    value = {
        "id": "visual-candidate-001",
        "sourcePage": 4,
        "sourceSha256": SOURCE_HASH,
        "bounds": [0.25, 0.20, 0.75, 0.80],
        "embeddedTextStatus": "known",
        "supportedSection": "period-1-representation",
        "reviewRequired": True,
        "reviewStatus": "pending",
    }
    value.update(overrides)
    return value


def review(**overrides):
    value = {
        "candidateId": "visual-candidate-001",
        "reviewer": "teacher-1",
        "status": "approved",
        "checks": {name: True for name in REQUIRED_CHECKS},
        "transcribedLabels": [],
        "notes": "Reviewed against the source page.",
    }
    value.update(overrides)
    return value


class VisualReviewPacketTests(unittest.TestCase):
    def test_review_packet_contains_full_page_overlay_and_candidate_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = PillowImage.new("RGB", (240, 120), "white")
            page.paste((20, 90, 180), (60, 24, 180, 96))
            page_path = root / "page.png"
            page.save(page_path)

            packet_path = render_review_packet(candidate(), page_path, root / "review.png")

            self.assertEqual(packet_path, root / "review.png")
            with PillowImage.open(packet_path) as packet:
                self.assertEqual(packet.mode, "RGB")
                self.assertGreater(packet.width, packet.height)
                self.assertNotEqual(packet.getpixel((packet.width - 10, packet.height // 2)), (255, 255, 255))
                self.assertNotEqual(ImageChops.difference(packet, PillowImage.new("RGB", packet.size, "white")).getbbox(), None)


class VisualReviewValidationTests(unittest.TestCase):
    def test_approved_review_with_all_checks_and_matching_identity_is_valid(self):
        self.assertEqual(validate_review(candidate(), review()), [])

    def test_required_checks_use_exact_boolean_true_and_stable_order(self):
        errors = validate_review(candidate(), review(checks={}))

        self.assertEqual(
            errors,
            [f"review-check-failed:{name}" for name in REQUIRED_CHECKS],
        )

    def test_unread_embedded_text_cannot_be_approved_without_transcription(self):
        errors = validate_review(candidate(embeddedTextStatus="unread"), review(transcribedLabels=[]))

        self.assertIn("label-transcription-missing", errors)

    def test_unread_embedded_text_is_approved_with_nonempty_transcription(self):
        self.assertEqual(
            validate_review(
                candidate(embeddedTextStatus="unread"),
                review(transcribedLabels=["P_atm", "P_gas"]),
            ),
            [],
        )

    def test_identity_source_trace_and_status_are_validated_deterministically(self):
        errors = validate_review(
            candidate(sourcePage=0, sourceSha256="not-a-sha256", bounds=[0, 0, 1, 1]),
            review(candidateId="other-candidate", status="maybe"),
        )

        self.assertEqual(
            errors,
            [
                "candidate-source-page-invalid",
                "candidate-source-sha256-invalid",
                "candidate-identity-mismatch",
                "review-status-invalid",
            ],
        )

    def test_approval_requires_source_trace_check_and_preserves_optional_trace(self):
        errors = validate_review(
            candidate(),
            review(
                checks={name: name != "sourceTrace" for name in REQUIRED_CHECKS},
                sourcePage=5,
                sourceSha256=SOURCE_HASH,
                bounds=[0.2, 0.2, 0.8, 0.8],
            ),
        )

        self.assertIn("review-check-failed:sourceTrace", errors)
        self.assertIn("review-source-page-mismatch", errors)
        self.assertIn("review-bounds-mismatch", errors)


if __name__ == "__main__":
    unittest.main()
