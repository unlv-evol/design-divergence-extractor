"""``dde-prep reverify``: dde's stage-1 check plus Stage A's MO re-verification.

GACPD judged each fix missing at one revision; RePatch integrated onto another. For every
eligible case this runs ``dde``'s revision check and then flags MO files whose fix already
appears in the target (``fix_possibly_present``) or that have no target counterpart
(``mo_file_without_target_counterpart``). Files are matched by path or unique file name
only. ``--apply`` excludes cases whose commits are missing from the repository; every other
flag needs a person's decision.
"""

from __future__ import annotations

from pathlib import Path

from dde.diff import parse_unified_diff
from dde.git import Repo
from dde.manifest import load_manifest, read_rows, select_cases, write_manifest
from dde.matching import TargetTree, match_file
from dde.models import RenameMaps
from dde.outputs import BASE_COLUMNS, status_rows, write_csv
from dde.pipeline import CaseResult, Settings, fix_possibly_present, verify_case

AUTO_EXCLUDE = {"sha_not_in_repository": "sha_not_in_repository"}


def reverify_case(res: CaseResult, repo: Repo) -> None:
    if res.revisions is None or res.stopped:
        return
    rev = res.revisions
    target = TargetTree(repo, rev.target_pre)
    identity = RenameMaps()
    mo = set(res.case.mo_files)
    for fp in parse_unified_diff(repo.diff(rev.source_pre, rev.source_post)):
        if fp.path not in mo:
            continue
        fm = match_file(fp.path, identity, identity, target)
        if fm.target_path is None:
            res.flag("revisions", "mo_file_without_target_counterpart", f"{fp.path}: {fm.how}")
        detail = fix_possibly_present(fp, target, fm)
        if detail:
            res.flag("revisions", "fix_possibly_present", detail)


def reverify(cases_path: Path, repos: Path, out: Path, apply: bool) -> list[CaseResult]:
    cases = select_cases(load_manifest(cases_path), None, include_ineligible=False)
    settings = Settings(repos, None, Path("."))
    results = []
    for c in cases:
        res = verify_case(c, settings)
        reverify_case(res, Repo(repos / c.variant_pair))
        results.append(res)
    write_csv(out / "case_status.csv", status_rows(results), BASE_COLUMNS["case_status.csv"])
    if apply:
        decided = {}
        for res in results:
            for f in res.flags:
                if f.flag in AUTO_EXCLUDE:
                    decided[res.case.case_id] = f"{AUTO_EXCLUDE[f.flag]}: {f.detail}"
        rows = read_rows(cases_path)
        for r in rows:
            if r["case_id"] in decided:
                r["eligibility"], r["exclusion_reason"] = "excluded", decided[r["case_id"]]
        write_manifest(cases_path, rows)
    return results
