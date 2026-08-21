# Task 2 fix round 1 report

Fixed every finding in `task-2-review.md`.

- Canonical caption parsing now accepts delimited English/Korean labels while rejecting narrative `Figure 1 shows ...` body text.
- Body barriers participate in initial caption seeding and later expansion; barrier text remains `body` and outside the crop.
- Equidistant captions use stable caption-order ownership so an atom cannot be claimed twice.
- Candidates carry `supportedSection`; linked candidates are marked retained, while unlinked candidates are explicitly `pending-context` and not retained.
- Tightly attached non-caption text is classified geometrically as `figureInternal`, preserving multiword labels, legends, conditions, and units.
- Multi-panel candidates retain one caption-anchored candidate and emit deterministic panel metadata with source provenance and normalized bounds.

Verification:

- Focused suite: `python -m unittest discover -s test/lesson_packet -p "test_figure_assembly.py" -v` — 13/13 passed.
- The package suite was not rerun in this final stop/finalization pass, per the latest instruction to run only focused tests.

No reconstruction, ranking, OCR claims, or copyright-bearing artifacts were added.
