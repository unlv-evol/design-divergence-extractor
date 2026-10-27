"""Deterministic CSV and JSON writers (R5).

Rows are written in the order the pipeline produced them (cases sorted by id),
values are formatted the same way every time, and line endings are fixed.
"""

from __future__ import annotations

import csv
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dde import __version__
from dde.java import grammar_versions
from dde.pipeline import CaseResult

BASE_COLUMNS = {
    "indicators_method.csv": ["case_id", "file", "class", "method"],
    "indicators_class.csv": ["case_id", "file", "class"],
    "indicators_file.csv": ["case_id", "file"],
    "indicators_patch.csv": ["case_id"],
    "entity_matches.csv": ["case_id", "level", "source_path", "source_entity", "source_lines",
                           "target_path", "target_entity", "target_lines", "how", "evidence"],
    "refactorings.csv": ["case_id", "side", "commit", "commit_order", "type", "description",
                         "left_files", "right_files", "used_for_rename_map"],
    "case_status.csv": ["case_id", "stage", "flag", "detail", "stops_case"],
}


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}"
    if hasattr(value, "value"):  # enums
        return str(value.value)
    return str(value)


def write_csv(path: Path, rows: list[dict[str, Any]], base: list[str]) -> None:
    columns = list(base)
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in columns])


def status_rows(results: list[CaseResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for res in results:
        for f in res.flags:
            rows.append({"case_id": f.case_id, "stage": f.stage, "flag": f.flag,
                         "detail": f.detail, "stops_case": f.stops_case})
        rows.append({"case_id": res.case.case_id, "stage": "summary",
                     "flag": "stopped" if res.stopped else "completed",
                     "detail": "", "stops_case": res.stopped})
    return rows


def write_extract(out: Path, results: list[CaseResult]) -> list[Path]:
    tables = {
        "indicators_method.csv": [r for res in results for r in res.method_rows],
        "indicators_class.csv": [r for res in results for r in res.class_rows],
        "indicators_file.csv": [r for res in results for r in res.file_rows],
        "indicators_patch.csv": [res.patch_row for res in results if res.patch_row],
        "entity_matches.csv": [r for res in results for r in res.match_rows],
        "refactorings.csv": [r for res in results for r in res.refactoring_rows],
        "case_status.csv": status_rows(results),
    }
    written = []
    for name, rows in tables.items():
        write_csv(out / name, rows, BASE_COLUMNS[name])
        written.append(out / name)
    return written


def _tool_commit() -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        return proc.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def write_run_manifest(
    out: Path,
    command: str,
    manifest_path: Path,
    manifest_sha256: str,
    results: list[CaseResult],
    settings: dict[str, Any],
    rm_version: str,
    git_version: str,
) -> Path:
    """Versions, input hashes, and per-case SHAs. The only file with a timestamp."""
    doc = {
        "tool": "dde",
        "tool_version": __version__,
        "tool_commit": _tool_commit(),
        "command": command,
        "python": platform.python_version(),
        "grammars": grammar_versions(),
        "refactoringminer": rm_version,
        "git": git_version,
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha256},
        "settings": settings,
        "cases": {
            res.case.case_id: (
                {"source_pre": res.revisions.source_pre, "source_post": res.revisions.source_post,
                 "target_pre": res.revisions.target_pre, "ancestor": res.revisions.ancestor}
                if res.revisions else None
            )
            for res in results
        },
        "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path = out / "run_manifest.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
