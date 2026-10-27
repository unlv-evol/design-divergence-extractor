"""Against the real RefactoringMiner. Skipped unless DDE_REFACTORINGMINER is set.

Run this once with the pinned release before trusting the rename maps: it
checks that the JSON the tool actually writes is read the way the fake in
``tests/fake_refactoringminer.py`` assumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dde.git import Repo
from dde.refminer import build_rename_maps, normalize, run_range
from scenario import P, Scenario

pytestmark = [pytest.mark.integration, pytest.mark.refactoringminer]


def test_real_tool_produces_the_expected_rename_maps(
    scenario: Scenario, real_rm: Path, tmp_path: Path
) -> None:
    repo = Repo(scenario.repo)
    commits = run_range(real_rm, repo, scenario.ancestor, scenario.target_pre, tmp_path / "c",
                        timeout=600)
    refs = normalize(commits, "target", repo.rev_list(scenario.ancestor, scenario.target_pre))
    types = {r.type for r in refs}
    assert {"Rename Method", "Move Class"} <= types
    maps, _ = build_rename_maps(refs)
    assert maps.methods[("com.shop.OrderService", "flushAll", 1)] == "flush"
    assert maps.classes["com.shop.io.Writer"] == "com.shop.Writer"
    assert maps.files[f"{P}/io/Writer.java"] == f"{P}/Writer.java"
