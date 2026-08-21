# Task 2 report: caption-anchored composite figures

Implemented `assemble_composite_figures(page)` and focused unit coverage.

Implementation includes:

- caption parsing and deterministic caption anchoring;
- bounded normalized-gap expansion across vector, table, and image atoms;
- stops for neighboring captions, body-text barriers, page furniture, and configured gaps;
- complete multi-panel candidates with intersecting/attached figure-internal labels included in crop bounds;
- exact text roles of `body`, `caption`, and `figureInternal`;
- source page/hash provenance and normalized candidate bounds;
- conservative `unread` plus `reviewRequired: true` for raster candidates without verified text;
- no reconstruction or ranking behavior.

Verification:

- Focused suite: `python -m unittest discover -s test/lesson_packet -p "test_figure_assembly.py" -v` — 7/7 passed.
- Existing package suite: `python -m unittest discover -s test/lesson_packet -p "test*.py" -v` — 83/83 passed.

No textbook PDFs, page images, crops, manifests, overlays, review packets, lesson JSON, or other copyright-bearing artifacts were added.
