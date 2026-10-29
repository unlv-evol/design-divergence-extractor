"""``dde-prep check``: is the manifest consistent and ready for ``dde``?"""

from __future__ import annotations

import re
from pathlib import Path

from dde.manifest import (
    ELIGIBILITY_VALUES,
    EXCLUSION_REASONS,
    ManifestError,
    load_manifest,
    read_rows,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")


def check_manifest(path: Path) -> list[str]:
    try:
        load_manifest(path)  # dde's own reader: columns, ids, outcome guard
    except ManifestError as exc:
        return [str(exc)]
    problems = []
    for r in read_rows(path):
        cid, e = r["case_id"], r["eligibility"]
        if e not in ELIGIBILITY_VALUES:
            problems.append(f"{cid}: eligibility {e!r} not in {', '.join(ELIGIBILITY_VALUES)}")
        if e == "excluded" and r["exclusion_reason"].split(":")[0].strip() not in EXCLUSION_REASONS:
            problems.append(f"{cid}: exclusion_reason {r['exclusion_reason']!r} not in the list")
        if e == "eligible":
            for col in ("source_pre_sha", "source_post_sha", "target_pre_sha", "repatch_target_sha"):
                if not _SHA.match(r[col]):
                    problems.append(f"{cid}: eligible but {col} is not a full 40-character SHA")
            if r["target_pre_sha"] != r["repatch_target_sha"]:
                problems.append(f"{cid}: target_pre_sha differs from repatch_target_sha")
            if not r["mo_files"]:
                problems.append(f"{cid}: eligible but has no MO files")
        if e == "pending":
            problems.append(f"{cid}: still pending")
    return problems
