# Task 5 report: bounded source-grounded visual reconstruction

## Result

Hardened the reconstruction fallback in `tools/lesson_packet/visuals.py` while preserving the existing source-crop and renderer validation interfaces.

## Changes

- Added deterministic `ValueError("visual must be an object")` handling for malformed top-level crop inputs.
- Rejected zero and non-positive group transform scales.
- Enforced maximum primitive nesting depth of 8.
- Enforced a maximum of 10,000 expanded repeat instances, including nested repeats.
- Rejected unsupported `style` fields on `group` and `repeat` nodes.
- Rejected equation syntax in non-equation labels, including pressure and arrow labels, with the stable message `label contains forbidden equation syntax`.
- Scaled oversized drawings proportionally to fit `MAX_VISUAL_WIDTH` × `MAX_VISUAL_HEIGHT`.
- Added focused regressions covering all Task 5 findings and retained the existing valid visual/crop coverage.

The implementation remains downstream of source cropping and semantic validation; it does not add OCR, source interpretation, or private/copyright-bearing artifacts.

## Verification

- `python -m unittest discover -s test/lesson_packet -p "test_build.py" -v` — 25/25 passed.
- `python -m unittest discover -s test/lesson_packet -v` — 116/116 passed.
- `git diff --check` — passed.

## Files

- `tools/lesson_packet/visuals.py`
- `test/lesson_packet/test_build.py`
- `.superpowers/sdd/2026-08-21-source-visual-evidence-pipeline/task-5-report.md`

