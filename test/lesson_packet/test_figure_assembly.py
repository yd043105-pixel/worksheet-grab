import copy
import unittest

from tools.lesson_packet.figure_assembly import assemble_composite_figures


SOURCE_HASH = "d" * 64


def _record(record_id, bounds, **extra):
    return {
        "id": record_id,
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "bounds": list(bounds),
        **extra,
    }


def _page(text_blocks, visual_atoms, **extra):
    return {
        "id": "page-003",
        "sourcePage": 3,
        "sourceSha256": SOURCE_HASH,
        "pageSize": (800.0, 1000.0),
        "bounds": [0.0, 0.0, 1.0, 1.0],
        "renderedPage": "page-003.png",
        "textBlocks": text_blocks,
        "visualAtoms": visual_atoms,
        **extra,
    }


def _j_tube_page_fixture():
    return _page(
        [
            _record("caption-i-3", [0.12, 0.68, 0.84, 0.73], role="caption", text="Figure I-3. J-tube apparatus and Boyle graph"),
            _record("p-gas", [0.14, 0.35, 0.21, 0.39], role="body", text="P gas"),
            _record("p-atm", [0.30, 0.45, 0.39, 0.49], role="body", text="P atm"),
            _record("axis-p", [0.52, 0.58, 0.64, 0.63], role="body", text="pressure (kPa)"),
            _record("axis-v", [0.77, 0.46, 0.84, 0.51], role="body", text="volume (V)"),
        ],
        [
            _record("tube-left", [0.10, 0.20, 0.25, 0.60], kind="vectorGroup", objectCount=5),
            _record("tube-right", [0.27, 0.20, 0.42, 0.60], kind="vectorGroup", objectCount=5),
            _record("arrow", [0.22, 0.39, 0.31, 0.47], kind="vectorGroup", objectCount=2),
            _record("graph", [0.50, 0.25, 0.82, 0.60], kind="vectorGroup", objectCount=8),
        ],
        figureAssemblyConfig={"maxNormalizedGap": 0.10},
    )


class CompositeFigureTests(unittest.TestCase):
    def test_caption_anchors_one_complete_figure(self):
        page = _j_tube_page_fixture()

        candidates = assemble_composite_figures(page)

        figure = next(x for x in candidates if x["captionId"] == "caption-i-3")
        self.assertEqual(set(figure["visualAtomIds"]), {"tube-left", "tube-right", "arrow", "graph"})
        self.assertEqual(set(figure["internalTextIds"]), {"p-gas", "p-atm", "axis-p", "axis-v"})
        self.assertEqual(figure["sourcePage"], 3)
        self.assertEqual(figure["sourceSha256"], SOURCE_HASH)

    def test_neighboring_captions_do_not_merge_figures(self):
        page = _page(
            [
                _record("caption-13", [0.10, 0.40, 0.32, 0.44], role="caption", text="Figure 13. Apparatus"),
                _record("caption-14", [0.68, 0.40, 0.90, 0.44], role="caption", text="Figure 14. Graph"),
            ],
            [
                _record("apparatus", [0.10, 0.10, 0.32, 0.34], kind="vectorGroup", objectCount=3),
                _record("graph", [0.68, 0.10, 0.90, 0.34], kind="vectorGroup", objectCount=4),
            ],
            figureAssemblyConfig={"maxNormalizedGap": 0.08},
        )

        figures = assemble_composite_figures(page)

        self.assertEqual([x["captionId"] for x in figures], ["caption-13", "caption-14"])
        self.assertEqual([x["visualAtomIds"] for x in figures], [["apparatus"], ["graph"]])

    def test_text_inside_composite_becomes_figure_internal(self):
        page = _j_tube_page_fixture()

        figure = assemble_composite_figures(page)[0]

        self.assertIn("axis-p", figure["internalTextIds"])
        axis = next(x for x in page["textBlocks"] if x["id"] == "axis-p")
        self.assertEqual(axis["role"], "figureInternal")
        body = next(x for x in page["textBlocks"] if x["id"] == "caption-i-3")
        self.assertEqual(body["role"], "caption")

    def test_body_barrier_stops_expansion(self):
        page = _page(
            [
                _record("caption-1", [0.08, 0.48, 0.30, 0.52], role="caption", text="Figure 1. Two regions"),
                _record("body-barrier", [0.31, 0.24, 0.37, 0.36], role="body", text="The next paragraph begins here"),
            ],
            [
                _record("left-panel", [0.08, 0.10, 0.28, 0.40], kind="vectorGroup", objectCount=3),
                _record("right-panel", [0.38, 0.10, 0.58, 0.40], kind="vectorGroup", objectCount=3),
            ],
            figureAssemblyConfig={"maxNormalizedGap": 0.12},
        )

        figure = assemble_composite_figures(page)[0]

        self.assertEqual(figure["visualAtomIds"], ["left-panel"])
        self.assertEqual(figure["relatedTextIds"], ["body-barrier"])
        self.assertEqual(next(x for x in page["textBlocks"] if x["id"] == "body-barrier")["role"], "body")

    def test_expansion_stops_at_page_furniture_and_configured_gap(self):
        page = _page(
            [_record("caption-2", [0.10, 0.48, 0.30, 0.52], role="caption", text="Figure 2. Apparatus")],
            [
                _record("apparatus", [0.10, 0.10, 0.30, 0.40], kind="vectorGroup", objectCount=3),
                _record("footer-rule", [0.34, 0.10, 0.45, 0.14], kind="pageFurniture", objectCount=1),
                _record("distant-atom", [0.55, 0.10, 0.75, 0.40], kind="vectorGroup", objectCount=3),
            ],
            figureAssemblyConfig={"maxNormalizedGap": 0.30},
        )

        figure = assemble_composite_figures(page)[0]

        self.assertEqual(figure["visualAtomIds"], ["apparatus"])

    def test_raster_candidate_with_unknown_pixel_text_requires_review(self):
        page = _page(
            [_record("caption-raster", [0.20, 0.65, 0.80, 0.70], role="caption", text="Figure 3. Printed apparatus")],
            [_record("raster", [0.20, 0.25, 0.80, 0.60], kind="nativeImage", objectCount=1)],
        )

        figure = assemble_composite_figures(page)[0]

        self.assertEqual(figure["embeddedTextStatus"], "unread")
        self.assertTrue(figure["reviewRequired"])

    def test_assembly_is_deterministic_and_bounds_include_internal_labels(self):
        first_page = _j_tube_page_fixture()
        second_page = copy.deepcopy(first_page)

        first = assemble_composite_figures(first_page)
        second = assemble_composite_figures(second_page)

        self.assertEqual(first, second)
        figure = first[0]
        self.assertEqual(figure["bounds"], [0.10, 0.20, 0.84, 0.63])
        self.assertTrue(all(0 <= value <= 1 for value in figure["bounds"]))
        self.assertTrue(figure["bounds"][0] < figure["bounds"][2])
        self.assertTrue(figure["bounds"][1] < figure["bounds"][3])


if __name__ == "__main__":
    unittest.main()
