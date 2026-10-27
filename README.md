# dde — Design-Divergence Extractor

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

Runtime dependencies are only `tree-sitter` and `tree-sitter-java`, pinned in `requirements.lock`.

## Workflow

```bash
export DDE_REFACTORINGMINER=$PWD/tools/RefactoringMiner-3.1.6/bin/RefactoringMiner

# Stage A helper: draft cases.csv from a GACPD run (SHAs and eligibility are left for Stage A)
dde gacpd-manifest --gacpd-run data/gacpd --out cases.draft.csv

# One working repository per variant pair, with both histories (the only network step)
dde setup-repos --manifest cases.csv --repos repos/

# Check revisions first: SHAs present, target SHA equals RePatch's, patch files match the manifest
dde verify --manifest cases.csv --repos repos/ --out out/

# Fill the RefactoringMiner cache ahead of time (slow on long histories; runs unattended)
dde refactorings --manifest cases.csv --repos repos/

# Blind packets for pass-1 coding (stages 1-6; no indicator values)
dde packets --manifest cases.csv --repos repos/ --out packets/

# Indicators, aggregates, audit trail (stages 1-8)
dde extract --manifest cases.csv --repos repos/ --out out/ [--cases C01,C02]

# After pass-1 codes are frozen: indicator vs. code evidence for workplan Section 8
dde validate --indicators out/indicators_patch.csv --codes codes.csv --out out/
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
  manifest.py     cases.csv reading and the outcome-column guard
  git.py          read-only git wrapper; per-pair repository setup
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
  gacpd.py        draft manifest from a GACPD run
  models.py       shared dataclasses and the State enum
tests/
  unit/           per-module tests; R1 and R3 checks in test_requirements.py
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
