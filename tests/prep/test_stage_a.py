"""dde-prep Stage A on the scenario repository: runs -> SHAs -> reverify -> check -> checkpoint."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dde.manifest import load_manifest, read_rows, write_manifest
from dde_prep.check import check_manifest
from dde_prep.cli import main
from dde_prep.runs import RUN_COLUMNS, derive_level
from dde_prep.shas import apply_shas
from scenario import P, Scenario, commit, git, manifest

pytestmark = pytest.mark.integration


def write_runs(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(RUN_COLUMNS), restval="", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


def blank_manifest(sc: Scenario, path: Path) -> Path:
    manifest(sc, path)
    rows = read_rows(path)
    for col in ("source_pre_sha", "source_post_sha", "target_pre_sha", "repatch_target_sha"):
        rows[0][col] = ""
    rows[0]["eligibility"] = "pending"
    rows.append({**rows[0], "case_id": "C02", "pr_number": "2"})
    write_manifest(path, rows)
    return path


def test_derive_level() -> None:
    assert derive_level({"git_status": "clean"})[0] == "L1"
    assert derive_level({"git_status": "conflict", "repatch_status": "completed"})[0] == "L2"
    assert (
        derive_level(
            {
                "git_status": "conflict",
                "repatch_status": "completed",
                "repatch_remaining_conflicting_files": "A.java",
            }
        )[0]
        == "L3"
    )
    assert derive_level({"git_status": "conflict", "repatch_status": "timeout"}) == (
        "Other",
        "repatch_status=timeout",
    )


def test_apply_shas_fills_revisions_and_eligibility(scenario: Scenario, tmp_path: Path) -> None:
    cases = blank_manifest(scenario, tmp_path / "cases.csv")
    runs = write_runs(
        tmp_path / "restricted" / "outcomes.csv",
        [
            {
                "case_id": "C01",
                "source_commits": scenario.source_post[:12],
                "target_sha": scenario.target_pre[:12],
                "git_status": "conflict",
            },
            {"case_id": "X99", "source_commits": "abc", "target_sha": "def"},
        ],
    )
    problems = apply_shas(cases, runs, scenario.repo.parent, cases)
    assert problems == ["X99: RePatch run with no manifest row (RePatch only)"]
    by = {c.case_id: c for c in load_manifest(cases)}
    c1 = by["C01"]
    assert (c1.source_pre_sha, c1.source_post_sha) == (scenario.source_pre, scenario.source_post)
    assert c1.target_pre_sha == c1.repatch_target_sha == scenario.target_pre
    assert c1.eligibility == "eligible"
    assert by["C02"].eligibility == "characterization_only"
    assert "git_status" not in read_rows(cases)[0]  # no outcome crossed into the manifest
    assert check_manifest(cases) == []


def test_check_reports_problems(scenario: Scenario, tmp_path: Path) -> None:
    cases = manifest(
        scenario, tmp_path / "cases.csv", repatch_target_sha=scenario.ancestor, eligibility="maybe"
    )
    problems = check_manifest(cases)
    assert any("eligibility 'maybe'" in p for p in problems)
    cases = manifest(scenario, tmp_path / "c2.csv", repatch_target_sha=scenario.ancestor)
    assert any("differs from repatch_target_sha" in p for p in check_manifest(cases))


def test_reverify_flags_a_fix_already_in_the_target(scenario: Scenario, tmp_path: Path) -> None:
    git(scenario.repo, "checkout", "-q", "target")
    w = scenario.repo / P / "io" / "Writer.java"
    w.write_text(w.read_text().replace("System.out.println(s);", "System.out.println(s.trim());"))
    fixed = commit(scenario.repo, "independent fix", "2024-04-15T00:00:00Z")
    cases = manifest(scenario, tmp_path / "cases.csv", target_pre_sha=fixed, repatch_target_sha=fixed)
    assert (
        main(
            [
                "reverify",
                "--cases",
                str(cases),
                "--repos",
                str(scenario.repo.parent),
                "--out",
                str(tmp_path / "v"),
            ]
        )
        == 0
    )
    with (tmp_path / "v" / "case_status.csv").open() as fh:
        fix = [r for r in csv.DictReader(fh) if r["flag"] == "fix_possibly_present"]
    assert len(fix) == 1 and fix[0]["detail"].startswith(f"{P}/Writer.java")


def test_reverify_apply_excludes_missing_commits(scenario: Scenario, tmp_path: Path) -> None:
    cases = manifest(scenario, tmp_path / "cases.csv", source_post_sha="f" * 40)
    assert (
        main(
            [
                "reverify",
                "--cases",
                str(cases),
                "--repos",
                str(scenario.repo.parent),
                "--out",
                str(tmp_path / "v"),
                "--apply",
            ]
        )
        == 0
    )
    (case,) = load_manifest(cases)
    assert case.eligibility == "excluded"
    assert case.exclusion_reason.startswith("sha_not_in_repository")


def test_checkpoint1_end_to_end(scenario: Scenario, tmp_path: Path) -> None:
    cases = manifest(scenario, tmp_path / "cases.csv")
    runs = write_runs(
        tmp_path / "restricted" / "outcomes.csv",
        [
            {
                "case_id": "C01",
                "source_commits": scenario.source_post,
                "target_sha": scenario.target_pre,
                "git_status": "conflict",
                "repatch_status": "completed",
                "repatch_remaining_conflicting_files": f"{P}/BaseService.java",
                "level": "L2",
            }
        ],
    )
    code = main(
        [
            "checkpoint1",
            "--cases",
            str(cases),
            "--runs",
            str(runs),
            "--out",
            str(tmp_path / "cp"),
            "--restricted",
            str(tmp_path / "restricted"),
        ]
    )
    report = (tmp_path / "cp" / "checkpoint1_report.md").read_text()
    assert code == 1  # the entered level disagrees with the raw fields
    assert "level entered as L2 but the raw fields give L3" in report
    assert "Result: **FAIL**" in report  # one case cannot meet the criteria
    assert "only in non-MO files: 1" in report
    assert (tmp_path / "cp" / "pilot_cases.csv").read_text().startswith("case_id\n")
