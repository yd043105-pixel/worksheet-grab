import unittest

from fixtures import valid_lesson_dict
from tools.lesson_packet.model import lesson_from_dict, validate_lesson, validate_lesson_dict


class LessonModelTests(unittest.TestCase):
    def test_accepts_complete_lesson(self):
        lesson = valid_lesson_dict()
        self.assertEqual([], validate_lesson_dict(lesson))
        self.assertEqual([], validate_lesson(lesson_from_dict(lesson)))

    def test_rejects_empty_concept_instruction(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["concepts"] = []
        self.assertIn("period-1-concepts-empty", validate_lesson_dict(lesson))

    def test_rejects_question_without_prior_support(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["independentPractice"][0]["requires"] = ["unknown-concept"]
        self.assertIn("unsupported-requirement:unknown-concept", validate_lesson_dict(lesson))

    def test_requires_complete_causal_chain(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["causalChain"].pop("representation")
        self.assertIn("causal-chain-missing:representation", validate_lesson_dict(lesson))

    def test_requires_source_grounded_phenomenon(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["phenomenon"] = {"body": "압력이 증가한다."}
        self.assertIn("period-1-phenomenon-source-missing", validate_lesson_dict(lesson))

    def test_requires_two_explanatory_concepts(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["concepts"].pop()
        self.assertIn("period-1-concepts-insufficient", validate_lesson_dict(lesson))

    def test_requires_each_lesson_flow_element(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["guidedPractice"] = []
        self.assertIn("period-1-guided-practice-empty", validate_lesson_dict(lesson))

    def test_declares_representation_requirements_before_use(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["representations"][0]["requires"] = ["relation-pressure"]
        self.assertIn("unsupported-requirement:relation-pressure", validate_lesson_dict(lesson))

    def test_requires_question_objectives_and_observations(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["question"] = ""
        lesson["periods"][0]["objectives"] = []
        lesson["periods"][0]["observations"] = []
        self.assertEqual(
            [
                "period-1-question-empty",
                "period-1-objectives-empty",
                "period-1-observations-empty",
            ],
            [
                error for error in validate_lesson_dict(lesson)
                if error in {
                    "period-1-question-empty",
                    "period-1-objectives-empty",
                    "period-1-observations-empty",
                }
            ],
        )

    def test_requires_explanatory_concepts_and_source_visual(self):
        lesson = valid_lesson_dict()
        lesson["visuals"][0]["sourcePage"] = None
        lesson["periods"][0]["concepts"][1]["explanation"] = ""
        self.assertIn("period-1-phenomenon-source-missing", validate_lesson_dict(lesson))
        self.assertIn("period-1-concept-explanation-missing", validate_lesson_dict(lesson))

    def test_requires_a_substantive_representation(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["representations"][0]["body"] = ""
        self.assertIn("period-1-representation-body-missing", validate_lesson_dict(lesson))

    def test_rejects_empty_practice_entries(self):
        for field, label in (
            ("workedExample", "worked-example"),
            ("guidedPractice", "guided-practice"),
            ("independentPractice", "independent-practice"),
            ("exitCheck", "exit-check"),
        ):
            with self.subTest(field=field):
                lesson = valid_lesson_dict()
                lesson["periods"][0][field] = {} if field == "workedExample" else [{}]
                errors = validate_lesson_dict(lesson)
                self.assertIn(f"period-1-{label}-prompt-missing", errors)
                self.assertIn(f"period-1-{label}-requires-missing", errors)

    def test_rejects_blank_practice_prompt(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["guidedPractice"][0]["prompt"] = "  "
        self.assertIn("period-1-guided-practice-prompt-missing", validate_lesson_dict(lesson))

    def test_rejects_missing_empty_and_malformed_practice_requires(self):
        for field, label in (
            ("workedExample", "worked-example"),
            ("guidedPractice", "guided-practice"),
            ("independentPractice", "independent-practice"),
            ("exitCheck", "exit-check"),
        ):
            for value, suffix in ((None, "missing"), ([], "missing"), ("concept-pressure", "invalid")):
                with self.subTest(field=field, value=value):
                    lesson = valid_lesson_dict()
                    item = lesson["periods"][0][field]
                    item = item if field == "workedExample" else item[0]
                    if value is None:
                        item.pop("requires")
                    else:
                        item["requires"] = value
                    self.assertIn(
                        f"period-1-{label}-requires-{suffix}",
                        validate_lesson_dict(lesson),
                    )

    def test_requires_distinct_unique_concept_ids(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["concepts"][1]["id"] = "concept-pressure"
        errors = validate_lesson_dict(lesson)
        self.assertIn("period-1-concepts-duplicate-id:concept-pressure", errors)
        self.assertIn("period-1-concepts-insufficient", errors)

    def test_rejects_malformed_concepts(self):
        lesson = valid_lesson_dict()
        lesson["periods"][0]["concepts"] = [{}, {"id": "concept-two", "term": "", "explanation": ""}]
        errors = validate_lesson_dict(lesson)
        self.assertIn("period-1-concept-id-missing", errors)
        self.assertIn("period-1-concept-term-missing", errors)
        self.assertIn("period-1-concept-explanation-missing", errors)

    def test_rejects_list_and_dict_practice_prompts_without_crashing(self):
        for field, label in (
            ("workedExample", "worked-example"),
            ("guidedPractice", "guided-practice"),
            ("independentPractice", "independent-practice"),
            ("exitCheck", "exit-check"),
        ):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    lesson = valid_lesson_dict()
                    item = lesson["periods"][0][field]
                    item = item if field == "workedExample" else item[0]
                    item["prompt"] = value
                    self.assertIn(
                        f"period-1-{label}-prompt-invalid",
                        validate_lesson_dict(lesson),
                    )

    def test_rejects_non_string_concept_fields_without_crashing(self):
        for field, value, error in (
            ("id", [], "period-1-concept-id-invalid"),
            ("id", {}, "period-1-concept-id-invalid"),
            ("term", [], "period-1-concept-term-invalid"),
            ("term", {}, "period-1-concept-term-invalid"),
            ("explanation", [], "period-1-concept-explanation-invalid"),
            ("explanation", {}, "period-1-concept-explanation-invalid"),
        ):
            with self.subTest(field=field, value=value):
                lesson = valid_lesson_dict()
                lesson["periods"][0]["concepts"][1][field] = value
                errors = validate_lesson_dict(lesson)
                self.assertIn(error, errors)
                self.assertIn("period-1-concepts-insufficient", errors)

    def test_rejects_non_string_requirement_ids_without_crashing(self):
        for value in ({"id": "concept-pressure"}, [], 3):
            with self.subTest(value=value):
                lesson = valid_lesson_dict()
                lesson["periods"][0]["exitCheck"][0]["requires"] = [value]
                self.assertIn(
                    "unsupported-requirement-invalid",
                    validate_lesson_dict(lesson),
                )


if __name__ == "__main__":
    unittest.main()
