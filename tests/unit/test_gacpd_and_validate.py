import csv
import json
from pathlib import Path

import pytest

from dde.gacpd import draft_manifest
from dde.manifest import ManifestError, load_manifest
from dde.validate import validate


def test_draft_manifest_from_gacpd_layout(tmp_path: Path) -> None:
    pr = tmp_path / "run" / "apache_kafka-linked_kafka" / "12535_MO"
    (pr / "MO" / "streams_x_CombinedKey_java").mkdir(parents=True)
    (pr / "pr_results.json").write_text(json.dumps({
        "classifiedPR": "12535", "repoDivergenceDate": "2021-07-06T17:39:59Z",
        "cutoffDate": "2026-06-30T16:37:26Z", "addedFilesSkipped": ["t/T.java"],
        "files": [{"fileName": "s/CombinedKey.java", "classification": "MO"},
                  {"fileName": "s/Supplier.java", "classification": "NA"},
                  {"fileName": "s/Other.java", "classification": "ED"}],
    }))
    (pr / "MO" / "streams_x_CombinedKey_java" / "results.josn").write_text(json.dumps({
        "fileName": "s/CombinedKey.java", "mainline": "apache/kafka", "divergentRepo": "linkedin/kafka",
    }))
    out = tmp_path / "cases.draft.csv"
    n, problems = draft_manifest(tmp_path / "run", out)
    assert (n, problems) == (1, [])
    (case,) = load_manifest(out)
    assert case.case_id == "apache_kafka--linkedin_kafka-PR12535"
    assert case.mo_files == ("s/CombinedKey.java",)
    assert case.non_mo_files == ("s/Other.java", "s/Supplier.java")
    assert case.source_pre_sha == "" and case.eligibility == "pending"


def test_validate_counts_by_code_and_state(tmp_path: Path) -> None:
    ind = tmp_path / "indicators_patch.csv"
    ind.write_text("case_id,relocation_worst,relocation_mean,relocation_state,relocation_unavailable_share\n"
                   "C1,1.0,1.0,divergent,0.0\nC2,0.0,0.0,zero,0.0\nC3,,,unavailable,1.0\n")
    codes = tmp_path / "codes.csv"
    codes.write_text("case_id,coder,code\nC1,consensus,cross_layer_relocation\nC2,consensus,\n"
                     "C3,consensus,cross_layer_relocation\nC2,alice,cross_layer_relocation\n")
    validate(ind, codes, tmp_path, mapping_path=None)
    with (tmp_path / "validation_summary.csv").open() as fh:
        row = next(r for r in csv.DictReader(fh) if r["indicator"] == "relocation")
    assert (row["n_cases"], row["present_divergent"], row["absent_zero"], row["present_unavailable"]) == (
        "3", "1", "1", "1")


def test_validate_refuses_outcome_columns(tmp_path: Path) -> None:
    (tmp_path / "i.csv").write_text("case_id\nC1\n")
    (tmp_path / "codes.csv").write_text("case_id,code,outcome\nC1,x,L3\n")
    with pytest.raises(ManifestError):
        validate(tmp_path / "i.csv", tmp_path / "codes.csv", tmp_path)
