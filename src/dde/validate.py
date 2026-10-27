"""``dde validate``: indicators against frozen pass-1 codes (spec Section 10).

Joins ``indicators_patch.csv`` with the coders' labels and writes, per
indicator and target code, the evidence needed for agreement counts and error
inspection in workplan Section 8. Outcome labels are never involved.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from dde.manifest import ManifestError, outcome_like_columns
from dde.outputs import write_csv

# Indicator -> codebook codes it is validated against (spec, Indicators table).
# The codebook is frozen after the pilot; override with --map if names change.
DEFAULT_MAP: dict[str, list[str]] = {
    "class_match": ["feature_absence", "responsibility_split_merge"],
    "method_match": ["feature_absence", "responsibility_split_merge"],
    "method_jaccard": ["role_changed"],
    "field_jaccard": ["role_changed"],
    "n_relocated": ["responsibility_split_merge"],
    "collab_jaccard": ["collaboration_changed"],
    "contract": ["api_contract_redesign"],
    "unresolved_ratio": ["missing_replaced_abstraction", "api_contract_redesign"],
    "supertypes_changed": ["missing_replaced_abstraction"],
    "relocation": ["cross_layer_relocation"],
}


def load_codes(path: Path, coder: str) -> dict[str, set[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        if "case_id" not in cols or "code" not in cols:
            raise ManifestError(f"{path}: needs columns case_id and code")
        leaking = outcome_like_columns(cols)
        if leaking:
            raise ManifestError(f"{path}: outcome-like columns are not allowed: {', '.join(leaking)}")
        codes: dict[str, set[str]] = {}
        for row in reader:
            if "coder" in cols and (row.get("coder") or "").strip() != coder:
                continue
            cid = (row["case_id"] or "").strip()
            code = (row["code"] or "").strip()
            codes.setdefault(cid, set())
            if code:
                codes[cid].add(code)
    return codes


def validate(indicators: Path, codes_path: Path, out: Path, coder: str = "consensus",
             mapping_path: Path | None = None) -> list[Path]:
    mapping = DEFAULT_MAP
    if mapping_path is not None:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    codes = load_codes(codes_path, coder)
    with indicators.open(newline="", encoding="utf-8") as fh:
        patch_rows = {r["case_id"]: r for r in csv.DictReader(fh)}
    coded = sorted(set(codes) & set(patch_rows))

    sheet: list[dict[str, object]] = []
    summary: Counter[tuple[str, str, str]] = Counter()
    for ind in sorted(mapping):
        for code in mapping[ind]:
            for cid in coded:
                r = patch_rows[cid]
                state = r.get(f"{ind}_state", "")
                present = code in codes[cid]
                sheet.append({
                    "indicator": ind, "code": code, "case_id": cid,
                    "code_present": present, "indicator_state": state,
                    "worst": r.get(f"{ind}_worst", ""), "mean": r.get(f"{ind}_mean", ""),
                    "unavailable_share": r.get(f"{ind}_unavailable_share", ""),
                })
                cell = ("present" if present else "absent") + "/" + (state or "missing")
                summary[(ind, code, cell)] += 1
    rows = []
    for ind in sorted(mapping):
        for code in mapping[ind]:
            row: dict[str, object] = {"indicator": ind, "code": code, "n_cases": len(coded)}
            for p in ("present", "absent"):
                for s in ("divergent", "zero", "unresolved", "unavailable", "not_applicable"):
                    row[f"{p}_{s}"] = summary[(ind, code, f"{p}/{s}")]
            rows.append(row)
    sheet_path = out / "validation_sheet.csv"
    summary_path = out / "validation_summary.csv"
    write_csv(sheet_path, sheet, ["indicator", "code", "case_id"])
    write_csv(summary_path, rows, ["indicator", "code", "n_cases"])
    missing = sorted(set(codes) - set(patch_rows))
    note = out / "validation_notes.txt"
    note.write_text(
        f"coded cases: {len(codes)}; with indicators: {len(coded)}\n"
        + (f"coded but no indicator row: {', '.join(missing)}\n" if missing else ""),
        encoding="utf-8",
    )
    return [sheet_path, summary_path, note]
