"""Draft a case manifest from a GACPD run (helper for workplan Stage A).

GACPD records which files of a pull request are missed opportunities (MO) and
which are not (NA, ED). It records no commit SHAs, so the draft leaves every
SHA column empty and ``eligibility`` as ``pending``: Stage A fills them from
the PR data and RePatch's logs, and decides eligibility.

Layout read (one directory per variant pair, one per PR)::

    <run>/<pair>/<PR>_MO/pr_results.json
    <run>/<pair>/<PR>_MO/MO/<flattened path>/results.json   (or results.josn)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dde.manifest import REQUIRED_COLUMNS
from dde.outputs import write_csv

_RESULTS_NAMES = ("results.json", "results.josn")


def _slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", repo).strip("_").lower()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pair_repos(pr_dir: Path) -> tuple[str, str]:
    """(mainline, divergent) from any per-file results record under the PR."""
    for name in _RESULTS_NAMES:
        for path in sorted(pr_dir.rglob(name)):
            rec = _read_json(path)
            if rec and rec.get("mainline") and rec.get("divergentRepo"):
                return str(rec["mainline"]), str(rec["divergentRepo"])
    return "", ""


def draft_manifest(run_dir: Path, out: Path) -> tuple[int, list[str]]:
    rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for pr_results in sorted(run_dir.rglob("pr_results.json")):
        pr_dir = pr_results.parent
        if not pr_dir.name.upper().endswith("_MO"):
            continue
        rec = _read_json(pr_results)
        if rec is None:
            problems.append(f"{pr_results}: unreadable")
            continue
        pr = str(rec.get("classifiedPR") or re.sub(r"\D", "", pr_dir.name))
        source, target = _pair_repos(pr_dir)
        if not (source and target):
            problems.append(f"{pr_dir}: no results.json names the repository pair")
        files = rec.get("files") or []
        mo = sorted(str(f["fileName"]) for f in files
                    if str(f.get("classification", "")).upper() == "MO")
        non_mo = sorted(str(f["fileName"]) for f in files
                        if str(f.get("classification", "")).upper() != "MO")
        pair = f"{_slug(source)}--{_slug(target)}" if source and target else pr_dir.parent.name
        rows.append({
            "case_id": f"{pair}-PR{pr}",
            "mo_id": f"{pr_dir.parent.name}/{pr_dir.name}",
            "variant_pair": pair,
            "direction": f"{source} -> {target}",
            "source_repo": f"https://github.com/{source}" if source else "",
            "target_repo": f"https://github.com/{target}" if target else "",
            "pr_number": pr,
            "source_pre_sha": "", "source_post_sha": "", "target_pre_sha": "",
            "repatch_target_sha": "", "ancestor_sha": "",
            "mo_files": ";".join(mo),
            "non_mo_files": ";".join(non_mo),
            "eligibility": "pending",
            "exclusion_reason": "",
            "gacpd_divergence_date": rec.get("repoDivergenceDate", ""),
            "gacpd_cutoff_date": rec.get("cutoffDate", ""),
            "gacpd_added_files_skipped": ";".join(sorted(rec.get("addedFilesSkipped") or [])),
        })
    write_csv(out, rows, list(REQUIRED_COLUMNS))
    return len(rows), problems
