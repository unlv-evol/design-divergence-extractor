# Changelog

## 0.2.0 — 2026-10-28

Data preparation moves out of `dde` into a companion package, `dde-prep`, in the same repository
(spec v1.3). `dde` keeps `verify`, `refactorings`, `extract`, `packets`, `validate`.

- Moved: `gacpd-manifest` (now `dde-prep draft`, which also marks duplicates), `setup-repos`, and
  the MO re-check added to `verify` in 0.1.1 (now `dde-prep reverify`).
- New in `dde-prep`: `apply-shas`, `check`, `reverify --apply`, `checkpoint1` (from the Stage A
  kit), `pilot-sheets` and `pilot-agreement` (from the coding pilot's supervisor scripts).
- `dde.manifest` owns the manifest schema, eligibility values and exclusion reasons, and gains
  `read_rows`/`write_manifest`, which refuse outcome-like columns.
- New test: no `dde` module may import `dde_prep`.

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
