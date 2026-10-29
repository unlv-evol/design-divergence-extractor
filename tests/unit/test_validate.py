import csv
from pathlib import Path

import pytest

from dde.manifest import ManifestError
from dde.validate import validate


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
