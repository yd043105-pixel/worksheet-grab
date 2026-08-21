# Task 7 report: gate worksheet visuals on source review

## Implemented

- Added the production precondition to the worksheet implementation plan and design spec.
- Task 5 builder inputs and Task 6 pilot/batch content are restricted to source candidates with `reviewStatus: approved`.
- Reconstruction additionally requires a verified semantic schema and no blocking reasons.
- Kept `reviewRequired` true until approval, required verified transcription for unread raster labels, and made unresolved representative-audit blockers prevent pilot or batch production.
- Updated the Task 5 and Task 6 interfaces to name approved source-candidate manifests and review records as inputs.
- Did not modify code, source artifacts, or stale Task 4A/TBD/TODO terminology.

## Verification

Command required by the brief:

```text
rg -n "T(BD)|TO(DO)|Task 4A|title-only|reviewStatus|label-transcription" docs/superpowers/plans docs/superpowers/specs
```

Result: the expected current-architecture matches were present for `title-only`, `reviewStatus`, and `label-transcription`; the new approval precondition appeared in both target documents; no `T(BD)`, `TO(DO)`, or `Task 4A` matches were found.

Command required by the brief:

```text
git diff --check
```

Result: passed with no whitespace errors.

## Files

- `docs/superpowers/plans/2026-08-21-material-energy-lesson-worksheets.md`
- `docs/superpowers/specs/2026-08-21-self-contained-chemistry-lesson-worksheets-design.md`
- `.superpowers/sdd/2026-08-21-source-visual-evidence-pipeline/task-7-report.md`

