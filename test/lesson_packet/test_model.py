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


if __name__ == "__main__":
    unittest.main()
