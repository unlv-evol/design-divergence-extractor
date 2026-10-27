# Changelog

## 0.1.1 — 2026-10-26

`dde verify` now also re-verifies MO status at the target SHA for Stage A: it raises
`fix_possibly_present` and `mo_file_without_target_counterpart` (path or unique file-name
matching only; `extract` repeats the fix check with rename maps).

## 0.1.0 — 2026-10-26

First implementation of specification v1.2: all four spec commands (`verify`, `extract`,
`packets`, `validate`), stages 1–8, indicators I1–I8 with measurement states, roll-ups, coder
packets, and run manifest. Helpers: `setup-repos`, `refactorings` (cache prefill), and
`gacpd-manifest`. Checked against RefactoringMiner 3.1.6. Golden cases from real data are not
yet included.
