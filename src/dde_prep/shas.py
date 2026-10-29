"""``dde-prep apply-shas``: fill the manifest's revisions from the RePatch runs.

For each case with a run: ``source_post_sha`` is the last commit RePatch cherry-picked,
``source_pre_sha`` the parent of the first, and ``target_pre_sha`` = ``repatch_target_sha`` =
the run's target. SHAs are expanded to 40 characters in the pair's repository. Pending cases
with a complete run become ``eligible``; pending cases without a run become
``characterization_only``. Excluded rows are never touched. Only commit columns of the runs
are read; no outcome is written to the manifest.
"""

from __future__ import annotations

from pathlib import Path

from dde.git import Repo
from dde.manifest import read_rows, write_manifest
from dde_prep.runs import load_runs, split


def apply_shas(cases_path: Path, runs_path: Path, repos: Path | None, out: Path) -> list[str]:
    rows = read_rows(cases_path)
    runs = load_runs(runs_path)
    problems: list[str] = []
    known = {r["case_id"] for r in rows}
    for cid in sorted(set(runs) - known):
        problems.append(f"{cid}: RePatch run with no manifest row (RePatch only)")
    for r in rows:
        if r["eligibility"] == "excluded":
            continue
        run = runs.get(r["case_id"])
        if run is None:
            if r["eligibility"] in ("", "pending"):
                r["eligibility"] = "characterization_only"
            continue
        commits = split(run["source_commits"])
        target = run["target_sha"]
        if not commits or not target:
            problems.append(f"{r['case_id']}: run lacks source_commits or target_sha")
            continue
        repo = Repo(repos / r["variant_pair"]) if repos else None
        post, pre, tgt = commits[-1], "", target
        if repo is not None and repo.exists():
            try:
                post, tgt = repo.resolve(post), repo.resolve(target)
                pre = repo.resolve(f"{repo.resolve(commits[0])}^")
            except Exception as exc:  # noqa: BLE001 - reported per case, never fatal
                problems.append(f"{r['case_id']}: cannot resolve commits: {exc}")
        else:
            problems.append(f"{r['case_id']}: no repository at {repos}; source_pre_sha left empty")
        r["source_post_sha"], r["target_pre_sha"], r["repatch_target_sha"] = post, tgt, tgt
        if pre:
            r["source_pre_sha"] = pre
        if r["eligibility"] in ("", "pending") and pre and len(post) == len(tgt) == 40:
            r["eligibility"] = "eligible"
    write_manifest(out, rows)
    return problems
