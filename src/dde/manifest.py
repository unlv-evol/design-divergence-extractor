"""Reading and checking the case manifest (``cases.csv``, spec Section 4)."""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from dde.models import Case

REQUIRED_COLUMNS = (
    "case_id",
    "mo_id",
    "variant_pair",
    "direction",
    "source_repo",
    "target_repo",
    "pr_number",
    "source_pre_sha",
    "source_post_sha",
    "target_pre_sha",
    "repatch_target_sha",
    "ancestor_sha",
    "mo_files",
    "non_mo_files",
    "eligibility",
    "exclusion_reason",
)

# R3: the manifest must not carry integration outcomes. A column whose name looks
# like one is refused outright rather than silently ignored, so a manifest
# exported with labels attached cannot slip through.
_OUTCOME_COLUMN = re.compile(
    r"(^|_)(outcome|label|level|result|status|conflict|resolved|repatch_status|git_status|cherry)",
    re.IGNORECASE,
)
_ALLOWED_DESPITE_PATTERN = frozenset({"repatch_target_sha"})

# Vocabulary of the manifest's decision columns. dde runs only ``eligible`` cases;
# the rest is written by dde-prep and kept for the audit trail.
ELIGIBILITY_VALUES = ("eligible", "characterization_only", "excluded", "pending")
EXCLUSION_REASONS = (
    "duplicate_mo", "no_repatch_case", "target_sha_unknown", "sha_not_in_repository",
    "fix_already_in_target", "mo_file_not_in_patch", "no_java_mo_file", "history_unavailable",
    "other",
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ManifestError(ValueError):
    """The manifest cannot be used as given."""


def outcome_like_columns(columns: list[str]) -> list[str]:
    return [
        c for c in columns if c not in _ALLOWED_DESPITE_PATTERN and _OUTCOME_COLUMN.search(c)
    ]


def _split_paths(raw: str) -> tuple[str, ...]:
    return tuple(sorted({p.strip() for p in raw.split(";") if p.strip()}))


def load_manifest(path: Path) -> list[Case]:
    """Parse ``cases.csv`` into cases, refusing anything ambiguous."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise ManifestError(f"{path}: missing columns: {', '.join(missing)}")
        leaking = outcome_like_columns(columns)
        if leaking:
            raise ManifestError(
                f"{path}: columns look like integration outcomes and are not allowed "
                f"in the manifest (spec R3): {', '.join(leaking)}"
            )
        cases: list[Case] = []
        seen: set[str] = set()
        for lineno, row in enumerate(reader, start=2):
            cid = (row["case_id"] or "").strip()
            if not cid:
                raise ManifestError(f"{path}:{lineno}: empty case_id")
            if not _SAFE_ID.match(cid):
                raise ManifestError(
                    f"{path}:{lineno}: case_id {cid!r} may only use letters, digits, . _ -"
                )
            if cid in seen:
                raise ManifestError(f"{path}:{lineno}: duplicate case_id {cid!r}")
            seen.add(cid)
            cases.append(
                Case(
                    case_id=cid,
                    mo_id=(row["mo_id"] or "").strip(),
                    variant_pair=(row["variant_pair"] or "").strip(),
                    direction=(row["direction"] or "").strip(),
                    source_repo=(row["source_repo"] or "").strip(),
                    target_repo=(row["target_repo"] or "").strip(),
                    pr_number=(row["pr_number"] or "").strip(),
                    source_pre_sha=(row["source_pre_sha"] or "").strip(),
                    source_post_sha=(row["source_post_sha"] or "").strip(),
                    target_pre_sha=(row["target_pre_sha"] or "").strip(),
                    repatch_target_sha=(row["repatch_target_sha"] or "").strip(),
                    ancestor_sha=(row["ancestor_sha"] or "").strip(),
                    mo_files=_split_paths(row["mo_files"] or ""),
                    non_mo_files=_split_paths(row["non_mo_files"] or ""),
                    eligibility=(row["eligibility"] or "").strip(),
                    exclusion_reason=(row["exclusion_reason"] or "").strip(),
                )
            )
    return cases


def select_cases(
    cases: list[Case], only: list[str] | None, include_ineligible: bool
) -> list[Case]:
    if only:
        wanted = set(only)
        unknown = wanted - {c.case_id for c in cases}
        if unknown:
            raise ManifestError(f"unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.case_id in wanted]
    if not include_ineligible:
        cases = [c for c in cases if c.eligible]
    return sorted(cases, key=lambda c: c.case_id)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    """The manifest as plain rows, extra columns kept (for tools that edit it)."""
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    """Write rows with the required columns first; refuse outcome-like columns (R3)."""
    columns = list(REQUIRED_COLUMNS)
    for r in rows:
        columns += [k for k in r if k not in columns]
    leaking = outcome_like_columns(columns)
    if leaking:
        raise ManifestError(f"refusing to write outcome-like columns: {', '.join(leaking)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(rows)
