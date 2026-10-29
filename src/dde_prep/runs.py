"""RePatch runs: the restricted outcome table and how levels are derived from it.

``restricted/outcomes.csv`` holds one row per RePatch PR with the raw Git and RePatch
fields. Until the reader for RePatch's released logs exists (it needs a sample of them),
this file is filled by hand in the layout below. Nothing here is ever given to ``dde``.
"""

from __future__ import annotations

import csv
from pathlib import Path

RUN_COLUMNS = (
    "case_id",
    "variant_pair",
    "pr_number",
    "repatch_case_id",
    "source_commits",
    "target_sha",
    "git_status",
    "git_conflicting_files",
    "repatch_status",
    "repatch_remaining_conflicting_files",
    "level",
    "notes",
)
LEVELS = ("L1", "L2", "L3", "Other")


class RunsError(ValueError):
    pass


def split(value: str) -> list[str]:
    return [p.strip() for p in value.split(";") if p.strip()]


def load_runs(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in RUN_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise RunsError(f"{path}: missing columns: {', '.join(missing)}")
        runs: dict[str, dict[str, str]] = {}
        for lineno, r in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in r.items()}
            if not row["case_id"]:
                raise RunsError(f"{path}:{lineno}: empty case_id")
            if row["case_id"] in runs:
                raise RunsError(f"{path}:{lineno}: duplicate case_id {row['case_id']}")
            runs[row["case_id"]] = row
    return runs


def derive_level(run: dict[str, str]) -> tuple[str, str]:
    """(level, reason) under workplan v3 Section 4; the reason explains ``Other``.

    L1 clean cherry-pick; L2 conflict and RePatch completed with zero remaining conflicting
    files; L3 conflict, RePatch completed, at least one file still conflicting; anything
    else is Other and never L3.
    """
    git = run.get("git_status", "").lower()
    rp = run.get("repatch_status", "").lower()
    if git == "clean":
        return "L1", ""
    if git != "conflict":
        return "Other", f"git_status={git or 'empty'}"
    if rp != "completed":
        return "Other", f"repatch_status={rp or 'empty'}"
    return ("L3", "") if split(run.get("repatch_remaining_conflicting_files", "")) else ("L2", "")
