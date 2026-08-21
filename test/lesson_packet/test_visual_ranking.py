import copy
import unittest

from tools.lesson_packet.visual_ranking import link_candidate_context, rank_visual_candidate


SOURCE_HASH = "a" * 64


def _record(record_id, bounds, **extra):
    return {
        "id": record_id,
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "bounds": list(bounds),
        **extra,
    }


def _page():
    return {
        "id": "page-003",
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "supportedSection": "period-1-representation",
        "textBlocks": [
            _record(
                "caption-figure-1",
                [0.20, 0.70, 0.80, 0.74],
                role="caption",
                text="Figure 1. Pressure apparatus and graph",
            ),
            _record(
                "body-reference",
                [0.10, 0.60, 0.60, 0.65],
                role="body",
                text="Figure 1 compares gas pressure with atmospheric pressure.",
            ),
            _record(
                "axis-pressure",
                [0.60, 0.30, 0.78, 0.35],
                role="figureInternal",
                text="pressure (kPa)",
            ),
        ],
    }


def relevant_graph_candidate(**overrides):
    candidate = {
        "id": "visual-candidate-001",
        "kind": "vectorGroup",
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "bounds": [0.15, 0.20, 0.85, 0.68],
        "captionId": "caption-figure-1",
        "explicitReferences": [
            "Figure 1 compares gas pressure with atmospheric pressure."
        ],
        "entities": ["gas", "apparatus", "atmospheric pressure"],
        "relatedTextIds": ["body-reference"],
        "internalTextIds": ["axis-pressure"],
        "visualAtomIds": ["apparatus", "graph"],
        "embeddedTextStatus": "known",
    }
    candidate.update(overrides)
    return candidate


def relevant_raster_candidate(**overrides):
    return relevant_graph_candidate(
        kind="nativeImage",
        embeddedTextStatus="unread",
        **overrides,
    )


def decorative_portrait(**overrides):
    candidate = {
        "id": "visual-candidate-portrait",
        "kind": "nativeImage",
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "bounds": [0.05, 0.10, 0.30, 0.35],
        "captionText": "Portrait of a scientist",
        "embeddedTextStatus": "none",
        "decorative": True,
    }
    candidate.update(overrides)
    return candidate


def lesson_evidence():
    return {
        "title": "Pressure representation lesson",
        "sections": {
            "period-1-representation": {
                "text": "The pressure apparatus represents gas pressure and atmospheric pressure.",
            },
            "period-1-explanation": {
                "text": "Pressure is force per unit area.",
            },
        },
        "entities": ["gas", "apparatus", "atmospheric pressure"],
        "quantities": ["pressure", "kPa"],
        "explicitReferences": ["Figure 1 compares gas pressure with atmospheric pressure."],
    }


class VisualContextLinkingTests(unittest.TestCase):
    def test_links_caption_related_and_internal_source_text_without_changing_trace(self):
        page = _page()
        candidate = relevant_graph_candidate()

        linked = link_candidate_context(page, candidate)

        self.assertEqual(linked["sourcePage"], 3)
        self.assertEqual(linked["sourceSha256"], SOURCE_HASH)
        self.assertEqual(linked["bounds"], candidate["bounds"])
        self.assertEqual(linked["supportedSection"], "period-1-representation")
        self.assertEqual(linked["captionText"], "Figure 1. Pressure apparatus and graph")
        self.assertEqual([item["id"] for item in linked["relatedText"]], ["body-reference"])
        self.assertEqual([item["id"] for item in linked["internalText"]], ["axis-pressure"])
        self.assertNotIn("captionText", candidate)


class VisualRankingTests(unittest.TestCase):
    def test_explicit_reference_and_entity_overlap_support_reuse(self):
        result = rank_visual_candidate(relevant_graph_candidate(), lesson_evidence())

        self.assertEqual(result["decision"], "reuse")
        self.assertEqual(result["supportedSection"], "period-1-representation")
        self.assertIn("explicit-reference", result["decisionReasons"])
        self.assertEqual(result["blockingReasons"], [])
        self.assertTrue(result["reviewRequired"])
        self.assertEqual(result["reviewStatus"], "pending")

    def test_title_only_and_decorative_portrait_are_excluded(self):
        evidence = {
            "title": "Portrait of a scientist",
            "sections": {"period-1-representation": "No related instructional material."},
            "entities": [],
            "quantities": [],
            "explicitReferences": [],
        }

        result = rank_visual_candidate(decorative_portrait(), evidence)

        self.assertEqual(result["decision"], "exclude")
        self.assertIsNone(result["supportedSection"])
        self.assertIn("title-only-evidence", result["decisionReasons"])

    def test_unread_embedded_text_blocks_reconstruction(self):
        candidate = relevant_raster_candidate(needsAnnotation=True)

        result = rank_visual_candidate(candidate, lesson_evidence())

        self.assertEqual(result["decision"], "reconstruct")
        self.assertIn("label-transcription-required", result["blockingReasons"])
        self.assertEqual(result["reviewStatus"], "pending")

    def test_answer_leakage_annotation_poor_print_and_incomplete_crop_require_reconstruction(self):
        hazards = (
            ("answerLeakage", "answer-leakage"),
            ("needsAnnotation", "annotation-needed"),
            ("poorPrint", "poor-print"),
            ("incompleteCrop", "incomplete-crop"),
        )
        for field, reason in hazards:
            with self.subTest(field=field):
                result = rank_visual_candidate(
                    relevant_graph_candidate(**{field: True}),
                    lesson_evidence(),
                )
                self.assertEqual(result["decision"], "reconstruct")
                self.assertIn(reason, result["blockingReasons"])

    def test_missing_section_or_non_title_evidence_excludes_candidate(self):
        evidence = {
            "title": "gas pressure",
            "sections": {},
            "entities": ["gas"],
            "quantities": [],
            "explicitReferences": [],
        }

        result = rank_visual_candidate(relevant_graph_candidate(), evidence)

        self.assertEqual(result["decision"], "exclude")
        self.assertIsNone(result["supportedSection"])
        self.assertIn("supported-section-missing", result["blockingReasons"])

    def test_ranking_is_deterministic_and_does_not_mutate_inputs(self):
        candidate = relevant_graph_candidate()
        evidence = lesson_evidence()
        original_candidate = copy.deepcopy(candidate)
        original_evidence = copy.deepcopy(evidence)

        first = rank_visual_candidate(candidate, evidence)
        second = rank_visual_candidate(candidate, evidence)

        self.assertEqual(first, second)
        self.assertEqual(candidate, original_candidate)
        self.assertEqual(evidence, original_evidence)
        self.assertEqual(first["sourcePage"], 3)
        self.assertEqual(first["sourceSha256"], SOURCE_HASH)
        self.assertEqual(first["bounds"], [0.15, 0.20, 0.85, 0.68])

    def test_malformed_inputs_raise_deterministic_value_errors(self):
        malformed_calls = (
            lambda: link_candidate_context([], {}),
            lambda: link_candidate_context(_page(), []),
            lambda: rank_visual_candidate([], lesson_evidence()),
            lambda: rank_visual_candidate(relevant_graph_candidate(), {"sections": []}),
        )
        for call in malformed_calls:
            with self.subTest(call=call):
                with self.assertRaises(ValueError) as error:
                    call()
                self.assertTrue(str(error.exception))


if __name__ == "__main__":
    unittest.main()
