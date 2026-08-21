# Task 4 Report: Generate Visual Review Packets And Enforce Approval

## Implemented

- Added `tools/lesson_packet/visual_review.py`.
- Added `test/lesson_packet/test_visual_review.py`.
- Implemented deterministic full-page overlay plus candidate-crop PNG packets.
- Added required review checks: `completeFigure`, `internalLabels`, `directions`, `axesUnits`, `conditions`, and `sourceTrace`.
- Added deterministic review validation for candidate identity, source page/hash/bounds, reviewer/status/check fields, and exact boolean check values.
- Unread embedded text remains gated until `transcribedLabels` is a non-empty verified transcription.
- No OCR, reconstruction, ranking, or source-extraction interface changes were added.

## Verification

- Focused: `python -m unittest discover -s test/lesson_packet -p "test_visual_review.py" -v` — 7 tests passed.
- Full lesson-packet package: `python -m unittest discover -s test/lesson_packet -p "test*.py" -v` — 110 tests passed.
- `git diff --check` passed.
- No textbook PDFs, page images, crops, manifests, overlays, review packets, lesson JSON, or other copyright-bearing artifacts were added.

## Commit

`feat: add source visual review gate`
