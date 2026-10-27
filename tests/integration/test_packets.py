"""Coder packets: right evidence, and blind (no indicator values, no outcomes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dde.cli import main
from scenario import Scenario, manifest

pytestmark = pytest.mark.integration

FORBIDDEN = ("divergent", "unresolved", "not_applicable", "jaccard", "method_match",
             "class_match", "name_arity", "same_name", "unique_elsewhere", "L1", "L2", "L3",
             "conflict", "cherry-pick")


def test_packet_content_and_blindness(scenario: Scenario, fake_rm: Path, tmp_path: Path) -> None:
    m = manifest(scenario, tmp_path / "cases.csv")
    out = tmp_path / "packets"
    assert main(["packets", "--manifest", str(m), "--repos", str(scenario.repo.parent),
                 "--rm", str(fake_rm), "--rm-cache", str(tmp_path / "c"), "--out", str(out)]) == 0
    text = (out / "C01" / "packet.md").read_text(encoding="utf-8")
    assert "public void flushAll(int timeout)" in text  # target region via the rename map
    assert "No counterpart method located in the target class." in text  # retryOrder
    assert "[target] Move Method" in text
    assert scenario.target_pre in text
    for word in FORBIDDEN:
        assert word not in text, word
    assert (out / "index.csv").read_text().splitlines() == ["case_id,packet", "C01,C01/packet.md"]
    assert not list(out.glob("indicators_*.csv"))
