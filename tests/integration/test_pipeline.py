"""End to end on the scenario repository, through the command line."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dde.cli import main
from scenario import P, Scenario, manifest

pytestmark = pytest.mark.integration


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def extract(sc: Scenario, rm: Path, tmp: Path, *extra: str) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / "out"
    m = manifest(sc, tmp / "cases.csv")
    code = main(["extract", "--manifest", str(m), "--repos", str(sc.repo.parent),
                 "--rm", str(rm), "--rm-cache", str(tmp / "cache"), "--out", str(out), *extra])
    assert code == 0
    return out


def by(rows_: list[dict[str, str]], **keys: str) -> dict[str, str]:
    hits = [r for r in rows_ if all(r[k] == v for k, v in keys.items())]
    assert len(hits) == 1, (keys, hits)
    return hits[0]


def test_method_level_indicators(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    out = extract(scenario, fake_rm, tmp_path)
    m = rows(out / "indicators_method.csv")
    svc = f"{P}/OrderService.java"

    flush = by(m, file=svc, method="flush(int)")
    assert (flush["method_match"], flush["method_match_state"]) == ("renamed", "zero")
    assert flush["contract_state"] == "zero"

    find = by(m, file=svc, method="find(String)")
    assert (find["method_match"], find["method_match_state"]) == ("exact", "zero")
    assert (find["return_changed"], find["contract_state"]) == ("true", "divergent")
    assert (find["n_introduced"], find["n_unresolved_calls"]) == ("1", "1")
    assert find["unresolved_calls"] == "audit"

    retry = by(m, file=svc, method="retryOrder(Order,int)")
    assert (retry["method_match"], retry["method_match_state"]) == ("absent", "divergent")
    assert retry["contract_state"] == "unresolved"

    added = by(m, file=svc, method="backoff(int)")
    assert added["entity_kind"] == "added_by_patch"
    assert added["method_match_state"] == "not_applicable"
    assert (added["calls_state"], added["unresolved_ratio"]) == ("zero", "0.000000")

    outside = by(m, file=svc, method="<outside-method>")
    assert outside["class"] == "OrderService"
    for col in ("method_match_state", "contract_state", "calls_state"):
        assert outside[col] == "not_applicable"


def test_class_and_file_level_indicators(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    out = extract(scenario, fake_rm, tmp_path)
    c = rows(out / "indicators_class.csv")
    svc = by(c, **{"class": "OrderService"})
    assert (svc["class_match"], svc["class_match_state"]) == ("same_name", "zero")
    assert svc["method_jaccard"] == f"{2 / 3:.6f}"
    assert (svc["field_jaccard"], svc["field_jaccard_state"]) == ("1.000000", "zero")
    assert (svc["n_lost"], svc["n_relocated"]) == ("1", "1")
    assert "RetryPolicy" in svc["relocated_to"]
    assert svc["collab_jaccard"] == f"{5 / 6:.6f}"
    assert (svc["supertypes_changed"], svc["supertypes_changed_state"]) == ("false", "zero")
    assert svc["method_match_worst"] == "1.000000"  # some method absent
    assert svc["unresolved_ratio_worst"] == "1.000000"

    writer = by(c, **{"class": "Writer"})
    assert (writer["class_match"], writer["class_match_state"]) == ("same_name", "zero")

    f = rows(out / "indicators_file.csv")
    w = by(f, file=f"{P}/Writer.java")
    assert (w["file_match"], w["target_path"]) == ("renamed", f"{P}/io/Writer.java")
    assert (w["package_changed"], w["module_changed"], w["relocation_state"]) == (
        "true", "false", "divergent")
    assert by(f, file=f"{P}/OrderService.java")["relocation_state"] == "zero"
    assert {r["file"] for r in f} == {f"{P}/OrderService.java", f"{P}/Writer.java"}  # MO only


def test_patch_row_and_audit(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    out = extract(scenario, fake_rm, tmp_path)
    (patch,) = rows(out / "indicators_patch.csv")
    assert (patch["n_files"], patch["n_mo_files"], patch["n_non_mo_files"]) == ("3", "2", "1")
    assert patch["relocation_worst"] == "1.000000"
    assert patch["divergence_age_days"] == "91.000000"
    assert patch["refactorings_target_by_type"] == "Move Class:1;Move Method:1;Rename Method:1"
    assert patch["refactorings_source"] == "0"

    status = rows(out / "case_status.csv")
    assert by(status, stage="summary")["flag"] == "completed"
    matches = rows(out / "entity_matches.csv")
    assert by(matches, level="method", how="renamed")["target_entity"] == "OrderService#flushAll(int)"
    refs = rows(out / "refactorings.csv")
    assert {r["type"] for r in refs if r["used_for_rename_map"] == "true"} == {
        "Rename Method", "Move Class"}


def test_outputs_are_deterministic(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    """R5: two runs give byte-identical outputs (the run manifest has a timestamp)."""
    a = extract(scenario, fake_rm, tmp_path / "a")
    b = extract(scenario, fake_rm, tmp_path / "b")
    names = sorted(p.name for p in a.iterdir() if p.name != "run_manifest.json")
    assert names
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_refactoringminer_failure_makes_values_unavailable(
    scenario: Scenario, fake_rm: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_RM_FAIL", "1")
    out = extract(scenario, fake_rm, tmp_path)
    c = rows(out / "indicators_class.csv")
    assert by(c, **{"class": "OrderService"})["method_jaccard_state"] == "unavailable"
    flags = {r["flag"] for r in rows(out / "case_status.csv")}
    assert "refactoringminer_failed" in flags
    (patch,) = rows(out / "indicators_patch.csv")
    assert patch["method_jaccard_state"] == "unavailable"
    assert patch["method_jaccard_unavailable_share"] == "1.000000"


def test_without_refactorings_rename_counts_as_divergence(
    scenario: Scenario, fake_rm: Path, tmp_path: Path
) -> None:
    out = extract(scenario, fake_rm, tmp_path, "--no-refactorings")
    m = rows(out / "indicators_method.csv")
    flush = by(m, file=f"{P}/OrderService.java", method="flush(int)")
    assert flush["method_match"] == "absent"  # no rename map: the rename looks like a loss
    assert "renames_skipped" in {r["flag"] for r in rows(out / "case_status.csv")}


def test_target_sha_must_match_repatch(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    m = manifest(scenario, tmp_path / "cases.csv", repatch_target_sha=scenario.ancestor)
    out = tmp_path / "out"
    assert main(["extract", "--manifest", str(m), "--repos", str(scenario.repo.parent),
                 "--rm", str(fake_rm), "--rm-cache", str(tmp_path / "c"), "--out", str(out)]) == 0
    status = rows(out / "case_status.csv")
    assert by(status, flag="target_sha_differs_from_repatch")["stops_case"] == "true"
    assert rows(out / "indicators_patch.csv") == []


def test_verify_flags_manifest_drift(scenario: Scenario, tmp_path: Path) -> None:
    m = manifest(scenario, tmp_path / "cases.csv", non_mo_files="")
    assert main(["verify", "--manifest", str(m), "--repos", str(scenario.repo.parent),
                 "--out", str(tmp_path / "v")]) == 0
    flags = {r["flag"] for r in rows(tmp_path / "v" / "case_status.csv")}
    assert "diff_files_differ_from_manifest" in flags


def test_refactoringminer_cache_reuses_wider_range(
    scenario: Scenario, fake_rm: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dde.git import Repo
    from dde.refminer import run_range

    repo = Repo(scenario.repo)
    cache = tmp_path / "cache"
    wide = run_range(fake_rm, repo, scenario.ancestor, scenario.target_pre, cache)
    assert len(wide) == 3
    narrow_end = repo.run("rev-parse", f"{scenario.target_pre}~1").strip()
    monkeypatch.setenv("FAKE_RM_FAIL", "1")  # proves the cache answered
    narrow = run_range(fake_rm, repo, scenario.ancestor, narrow_end, cache)
    assert [c["sha1"] for c in narrow] == [c["sha1"] for c in wide[:2]]
