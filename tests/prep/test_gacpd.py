import json
from pathlib import Path

from dde.manifest import load_manifest
from dde_prep.gacpd import draft_manifest


def _pr(run: Path, pair_dir: str, pr: str) -> None:
    d = run / pair_dir / f"{pr}_MO"
    (d / "MO" / "f").mkdir(parents=True)
    (d / "pr_results.json").write_text(
        json.dumps(
            {
                "classifiedPR": pr,
                "repoDivergenceDate": "2021-07-06T17:39:59Z",
                "cutoffDate": "2026-06-30T16:37:26Z",
                "addedFilesSkipped": ["t/T.java"],
                "files": [
                    {"fileName": "s/CombinedKey.java", "classification": "MO"},
                    {"fileName": "s/Supplier.java", "classification": "NA"},
                    {"fileName": "s/Other.java", "classification": "ED"},
                ],
            }
        )
    )
    (d / "MO" / "f" / "results.josn").write_text(
        json.dumps(
            {"fileName": "s/CombinedKey.java", "mainline": "apache/kafka", "divergentRepo": "linkedin/kafka"}
        )
    )


def test_draft_manifest_from_gacpd_layout(tmp_path: Path) -> None:
    pr = tmp_path / "run" / "apache_kafka-linked_kafka" / "12535_MO"
    (pr / "MO" / "streams_x_CombinedKey_java").mkdir(parents=True)
    (pr / "pr_results.json").write_text(
        json.dumps(
            {
                "classifiedPR": "12535",
                "repoDivergenceDate": "2021-07-06T17:39:59Z",
                "cutoffDate": "2026-06-30T16:37:26Z",
                "addedFilesSkipped": ["t/T.java"],
                "files": [
                    {"fileName": "s/CombinedKey.java", "classification": "MO"},
                    {"fileName": "s/Supplier.java", "classification": "NA"},
                    {"fileName": "s/Other.java", "classification": "ED"},
                ],
            }
        )
    )
    (pr / "MO" / "streams_x_CombinedKey_java" / "results.josn").write_text(
        json.dumps(
            {
                "fileName": "s/CombinedKey.java",
                "mainline": "apache/kafka",
                "divergentRepo": "linkedin/kafka",
            }
        )
    )
    out = tmp_path / "cases.draft.csv"
    n, problems = draft_manifest(tmp_path / "run", out)
    assert (n, problems) == (1, [])
    (case,) = load_manifest(out)
    assert case.case_id == "apache_kafka--linkedin_kafka-PR12535"
    assert case.mo_files == ("s/CombinedKey.java",)
    assert case.non_mo_files == ("s/Other.java", "s/Supplier.java")
    assert case.source_pre_sha == "" and case.eligibility == "pending"


def test_duplicates_are_kept_and_excluded(tmp_path: Path) -> None:
    _pr(tmp_path / "run", "apache_kafka-linked_kafka", "7")
    _pr(tmp_path / "run", "apache_kafka-linked_kafka-rerun", "7")
    out = tmp_path / "cases.csv"
    n, _ = draft_manifest(tmp_path / "run", out)
    cases = load_manifest(out)
    assert n == 2
    dup = [c for c in cases if c.eligibility == "excluded"]
    assert len(dup) == 1 and dup[0].exclusion_reason == "duplicate_mo"
    assert dup[0].case_id.endswith("-dup2")
