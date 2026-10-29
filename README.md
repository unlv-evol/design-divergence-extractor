# dde and dde-prep

This repository holds two tools, released together:

- **`dde`, the Design-Divergence Extractor**: computes indicators from a finished case manifest. It
  never reads outcomes.
- **`dde-prep`**: everything that prepares `dde`'s inputs (workplan Stage A) and supports the coding
  pilot. It may read RePatch results; `dde` never imports it, and a test enforces that.

## dde — Design-Divergence Extractor

`dde` computes the design-divergence indicators (I1–I8) for the *Beyond Refactorings* study:
for each directed patch-transfer attempt, it compares the code a bug fix edits in the source
variant with its counterpart in the target variant, after normalizing the renames both sides
made since their shared ancestor. It also writes the blind coder packets for pass-1 coding.

The tool specification (`docs/extractor-spec.pdf`, v1.2) is the authority on what each indicator
means. Where the spec is silent, the choice made is recorded in `docs/decisions.md`.

What `dde` never does: read RePatch results, conflict logs, or outcome labels (R3); use SALP in
any form (R1); or look at any revision other than source pre/post, target pre, and the ancestor (R4).

## Install

Python 3.11+, git, and (for real runs) Java 17+ with RefactoringMiner 3.1.6 (`tools/README.md`).

```bash
make dev        # pinned dependencies + editable install
make check      # ruff, mypy, pytest: what CI runs
```

Runtime dependencies of `dde` are only `tree-sitter` and `tree-sitter-java`, pinned in
`requirements.lock`. `dde-prep`'s pilot commands also need `openpyxl` (`pip install -e ".[prep]"`).

## Workflow

**1. Prepare the data (Stage A) with `dde-prep`.** Outcomes stay in `restricted/`.

```bash
dde-prep draft        --gacpd-run data/gacpd --out cases.csv          # one row per GACPD PR, duplicates marked
dde-prep setup-repos  --cases cases.csv --repos repos/                 # one repository per pair (network)
# fill restricted/outcomes.csv: one row per RePatch PR (examples/outcomes.example.csv)
dde-prep apply-shas   --cases cases.csv --runs restricted/outcomes.csv --repos repos/
dde-prep reverify     --cases cases.csv --repos repos/ --out verify/   # add --apply for missing commits
dde-prep check        --cases cases.csv                                # ready for dde?
dde-prep checkpoint1  --cases cases.csv --runs restricted/outcomes.csv --status verify/case_status.csv
```

`apply-shas` reads only the commit columns of the runs: `source_post_sha` is the last commit
RePatch cherry-picked, `source_pre_sha` the parent of the first, and both target columns are the
run's target. Pending cases with a run become `eligible`; without one, `characterization_only`.
`reverify` adds Stage A's MO re-check (`fix_possibly_present`, `mo_file_without_target_counterpart`)
to `dde verify`; those flags need a person's decision. `checkpoint1` re-derives each level from the
raw fields, writes the Checkpoint 1 report, and proposes the pilot sample
(`checkpoint1/pilot_cases.csv`, ids only; levels in `restricted/pilot_strata.csv`).

Until a reader for RePatch's released logs exists, `restricted/outcomes.csv` is filled by hand.

**2. Extract with `dde`.**

```bash
export DDE_REFACTORINGMINER=$PWD/tools/RefactoringMiner-3.1.6/bin/RefactoringMiner
dde verify       --manifest cases.csv --repos repos/ --out out/        # revisions and patch files
dde refactorings --manifest cases.csv --repos repos/                   # fill the RefactoringMiner cache
dde packets      --manifest cases.csv --repos repos/ --out packets/    # blind pass-1 packets
dde extract      --manifest cases.csv --repos repos/ --out out/ [--cases C01,C02]
```

**3. Pilot coding and validation.**

```bash
dde-prep pilot-sheets pilot --coder coderA --packets packets/ --cases checkpoint1/pilot_cases.csv --out coding_coderA.xlsx
dde-prep pilot-agreement --a coding_coderA.xlsx --b coding_coderB.xlsx [--consensus coding_consensus.xlsx] --out agreement/
dde validate --indicators out/indicators_patch.csv --codes agreement/codes_long.csv --out out/
```

Only cases whose `eligibility` is `eligible` run unless `--include-ineligible` is given.
`--no-refactorings` replaces RefactoringMiner with identity rename maps; every case is then
flagged `renames_skipped`, and those outputs must not be mixed with normal runs.

### Inputs

`cases.csv` has the columns in spec Section 4 (see `examples/cases.example.csv`). Columns whose
names look like outcomes (`outcome`, `level`, `status`, `conflict`, ...) are refused. Repositories
live at `repos/<variant_pair>/`. `codes.csv` for `validate` is long format: `case_id, coder, code`;
by default only `coder == consensus` rows are used. The indicator-to-code map is
`examples/indicator_map.json`; pass `--map` once the codebook is frozen if code names differ.

### Outputs

| File | Content |
| --- | --- |
| `indicators_method.csv` | One row per edited method (I1 method match, I5, I6), including methods the patch adds or deletes and edits outside any method |
| `indicators_class.csv` | One row per edited class (I1 class match, I2, I3, I4, I7) plus method roll-ups |
| `indicators_file.csv` | One row per MO file (I8) plus class and method roll-ups |
| `indicators_patch.csv` | One row per case: every roll-up and the baseline covariates |
| `entity_matches.csv` | How each source file, class, and method was matched, with evidence |
| `refactorings.csv` | RefactoringMiner entries touching the case's files; those used for rename maps marked |
| `case_status.csv` | Verification flags, per-stage errors, and a summary row per case |
| `run_manifest.json` | Tool, Python, grammar, git, and RefactoringMiner versions; manifest hash; per-case SHAs |
| `packets/<case>/packet.md` | Blind pass-1 evidence |

Each roll-up is reported as `<indicator>_worst`, `_mean`, `_n`, `_state`, `_unavailable_share`.
Every value carries one of five states: `divergent`, `zero`, `not_applicable`, `unresolved`,
`unavailable`. All files except `run_manifest.json` are byte-identical across runs (R5).

## Code layout

One module per pipeline stage, no framework:

```
src/dde/
  cli.py          command line
  manifest.py     cases.csv schema (owned here), reading, and the outcome-column guard
  git.py          read-only git wrapper
  revisions.py    stage 1: SHAs, R2 check, ancestor
  diff.py         stage 2: unified diff into files and hunks
  refminer.py     stage 3: RefactoringMiner runs, cache, two-sided rename maps
  matching.py     stages 4 and 6: file, class, and method correspondence
  java.py         stage 5: tree-sitter profiles
  entities.py     hunks onto edited methods and classes
  indicators.py   stage 7: I1-I8 and measurement states
  aggregate.py    stage 8: roll-ups
  pipeline.py     runs one case through all stages
  outputs.py      deterministic CSV/JSON writers
  packets.py      coder packets
  validate.py     indicators vs. frozen codes
  models.py       shared dataclasses and the State enum
src/dde_prep/     data preparation; may read outcomes; dde never imports it
  cli.py          command line
  gacpd.py        draft the manifest from a GACPD run
  repos.py        per-pair working repositories
  runs.py         restricted RePatch runs table; level derivation
  shas.py         revisions from the runs into the manifest
  check.py        manifest consistency
  reverify.py     dde verify plus MO re-verification at the target
  checkpoint.py   Checkpoint 1 report and pilot sampling
  pilot/          coding workbooks and agreement (kappa, codes for dde validate)
tests/
  unit/           per-module tests; R1, R3 and the dde/dde-prep boundary in test_requirements.py
  prep/           dde-prep tests
  integration/    end to end on a generated fork scenario (git, no network)
  scenario.py     the workplan's example as a real repository
  fake_refactoringminer.py   stand-in emitting RefactoringMiner 3.x JSON
  data/           JSON recorded from RefactoringMiner 3.1.6
```

## Testing

`make test` runs unit and integration tests; they need git but no network and no Java.
The integration scenario is the workplan's illustration (a renamed method, a responsibility
moved to a new collaborator, a changed return type, a moved class) with exact expected values.
`make test-rm` also runs it through the real RefactoringMiner; do this once per pinned release.

Still to add (spec Section 12): the three golden cases from real data, with expected values
written down before the tool is run on them. Put them under `tests/golden/`.
