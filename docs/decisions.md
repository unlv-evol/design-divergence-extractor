# Implementation decisions

Choices made where the specification (v1.2) is silent or ambiguous. Each can be reversed; the
code location is given. Items marked **for the team** affect what the indicators mean and should
be confirmed before full extraction.

## Matching and rename maps

1. **Which RefactoringMiner types build rename maps** (`refminer.py`). Classes: Rename Class,
   Move Class, Move And Rename Class. Methods: Rename Method, Move And Rename Method (the name
   change only). Fields: Rename Attribute. **Move Method is not a rename**: it changes the owning
   class, which I3 measures as relocation. Package-level refactorings are not used; the
   Move Class entries they produce are.
2. **Method renames are keyed by the ancestor class** at the time of the rename, so a later class
   rename does not break them.
3. **Class match `ambiguous`** (`matching.py`). The spec lists `same_name`, `renamed`,
   `unique_elsewhere`, `absent`. Several same-named classes elsewhere in the target give
   `ambiguous` with state `unresolved`, mirroring the method levels.
4. **`exact` method match uses raw text**; `renamed` uses ancestor vocabulary. A method whose
   name and parameter types are unchanged but whose return type differs is `exact` for I1 and
   divergent for I5.
5. **Constructors** are methods named `<init>`.

## States

6. **I6 with no introduced calls is `not_applicable`**, not `zero` (`indicators.py`). There is
   nothing to resolve, and a zero would pull means down. **For the team.**
7. **RefactoringMiner failure makes I1–I7 `unavailable`** for the whole case, since all of them
   depend on ancestor vocabulary. I8 does not, and stays computed. Packets are still written.
8. **A parse error inside a class** makes that class's I2–I4 and I7 `unavailable`; parse errors
   elsewhere in the file are flagged in `case_status.csv` only.
9. **Methods added by the patch** get I1 and I5 `not_applicable`; I6 is computed with an empty
   pre version. Methods the patch deletes get I6 `not_applicable`.

## Entities and aggregation

10. **Insertions** count towards a pre-patch method only when the old lines on both sides of the
    insertion are inside that method, so a new method added between two methods is not
    attributed to the one above it.
11. **Edits outside any method** produce one `<outside-method>` row per class, all method-level
    indicators `not_applicable`. Edits outside any class (imports) have no class row.
12. **`any` indicators** (I1 match, I5, I7, I8) roll up as worst = 1 if any contribution is
    divergent, mean = share divergent.
13. **Indicators are computed for MO files only**; non-MO files are counted in the covariates.

## Covariates and audit

14. **Refactoring covariates** count RefactoringMiner entries with any location in the case's MO
    files (source side) or their counterparts (target side), under current or ancestor paths.
15. **`refactorings.csv` lists only entries touching the case's files**, not every refactoring
    since the ancestor, which would repeat thousands of rows per Kafka case.
16. **`fix_possibly_present`** is raised when every substantive added line of an MO file (longer
    than three characters, not only punctuation, not a comment or import) already appears in the
    target counterpart file.
17. **Module** (I8) is the first path segment when that directory holds `build.gradle(.kts)`,
    `pom.xml`, `build.xml`, or `build.sbt`; otherwise the root module.

## Known issues found while building (for the team)

- **I6 overcounts library calls.** A JDK call such as `trim()` is unresolved if the target's own
  sources never contain `trim(`. The spec calls I6 a lower bound; in small or narrow targets it
  can be an upper bound instead. Options: an allow-list of JDK method names, or stating the
  limitation.
- **RefactoringMiner reports Change Return Type, Change Parameter Type, and similar as
  refactorings.** The spec treats such changes as contract divergence (I5). This is the
  refactoring-versus-design boundary in workplan Section 13; the rows are kept in
  `refactorings.csv` so coders can see them, but they do not enter rename maps.
- **GACPD records no SHAs.** `gacpd-manifest` leaves them empty. Stage A must fill source pre/post
  (PR head or merge commit, to be decided) and the target SHA from RePatch's logs.

## Tool boundary (0.2.0)

18. **`dde` and `dde-prep` are separate packages in one repository.** `dde-prep` may read RePatch
    results; `dde` must not (R3), and `tests/unit/test_requirements.py` fails if any `dde` module
    imports `dde_prep`. The manifest is the only artifact that crosses, and `dde.manifest` owns
    its schema so the two cannot drift.
19. **`reverify --apply` decides only what needs no judgement**: cases whose commits are missing
    from the repository become `excluded: sha_not_in_repository`. `fix_possibly_present` and
    `mo_file_without_target_counterpart` stay flags for a person.
20. **`restricted/outcomes.csv` is filled by hand** until a reader for RePatch's released logs is
    written; that needs a sample of the logs.
