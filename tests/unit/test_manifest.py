from pathlib import Path

import pytest

from dde.manifest import REQUIRED_COLUMNS, ManifestError, load_manifest, select_cases


def write(path: Path, rows: list[dict[str, str]], extra: tuple[str, ...] = ()) -> Path:
    cols = list(REQUIRED_COLUMNS) + list(extra)
    lines = [",".join(cols)] + [",".join(r.get(c, "") for c in cols) for r in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_loads_and_splits_paths(tmp_path: Path) -> None:
    p = write(tmp_path / "m.csv", [{"case_id": "C1", "mo_files": "b.java; a.java", "eligibility": "eligible"},
                                   {"case_id": "C2", "eligibility": "excluded"}])
    cases = load_manifest(p)
    assert cases[0].mo_files == ("a.java", "b.java")
    assert [c.case_id for c in select_cases(cases, None, False)] == ["C1"]
    assert len(select_cases(cases, None, True)) == 2
    with pytest.raises(ManifestError):
        select_cases(cases, ["C9"], False)


@pytest.mark.parametrize("col", ["outcome", "git_status", "repatch_status", "level", "conflicts"])
def test_outcome_columns_are_refused(tmp_path: Path, col: str) -> None:
    p = write(tmp_path / "m.csv", [{"case_id": "C1"}], extra=(col,))
    with pytest.raises(ManifestError, match="outcome"):
        load_manifest(p)


def test_duplicates_and_unsafe_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(write(tmp_path / "d.csv", [{"case_id": "C1"}, {"case_id": "C1"}]))
    with pytest.raises(ManifestError, match="case_id"):
        load_manifest(write(tmp_path / "u.csv", [{"case_id": "../x"}]))


def test_missing_columns(tmp_path: Path) -> None:
    (tmp_path / "m.csv").write_text("case_id\nC1\n")
    with pytest.raises(ManifestError, match="missing"):
        load_manifest(tmp_path / "m.csv")
