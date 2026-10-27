from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scenario import Scenario, build  # noqa: E402

FAKE_RM = Path(__file__).parent / "fake_refactoringminer.py"


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    return build(tmp_path / "repos")


@pytest.fixture
def fake_rm(scenario: Scenario, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FAKE_RM_SPEC", str(scenario.rm_spec))
    monkeypatch.delenv("FAKE_RM_FAIL", raising=False)
    return FAKE_RM


@pytest.fixture
def real_rm() -> Path:
    path = os.environ.get("DDE_REFACTORINGMINER")
    if not path or not Path(path).exists():
        pytest.skip("DDE_REFACTORINGMINER not set")
    return Path(path)
