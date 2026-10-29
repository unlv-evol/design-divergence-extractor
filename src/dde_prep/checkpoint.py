"""``dde-prep checkpoint1``: data integrity (workplan v3, Section 11) and the pilot sample.

Re-derives each level from the raw RePatch fields, checks the manifest against the runs and
``dde verify`` flags, and writes ``checkpoint1/checkpoint1_report.md``,
``checkpoint1/pilot_cases.csv`` (ids only, shareable) and ``restricted/pilot_strata.csv``.
The pilot is stratified by level (about a third each), spread across pairs with non-Kafka
pairs first, with a non-Kafka L3 case when one exists, drawn with a fixed seed.
"""

from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
from pathlib import Path

from dde.manifest import ELIGIBILITY_VALUES as _ELIG
from dde.manifest import EXCLUSION_REASONS as _EXCL
from dde_prep.runs import RUN_COLUMNS, derive_level

EXCLUSION_REASONS = set(_EXCL)
ELIGIBILITY = set(_ELIG) - {"pending"}
OUTCOME_COLUMNS = list(RUN_COLUMNS)


def read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def split(v: str) -> set[str]:
    return {p.strip() for p in v.split(";") if p.strip()}


def is_kafka(pair: str) -> bool:
    return "kafka" in pair.lower()


def _spread(pool: list[str], by_case: dict[str, dict[str, str]]) -> list[str]:
    """Round-robin across pairs, non-Kafka pairs first, keeping the shuffled order within a pair."""
    groups: dict[str, list[str]] = defaultdict(list)
    for cid in pool:
        groups[by_case[cid]["variant_pair"]].append(cid)
    order = sorted(groups, key=lambda p: (is_kafka(p), p))
    out: list[str] = []
    while any(groups.values()):
        for p in order:
            if groups[p]:
                out.append(groups[p].pop(0))
    return out


def checkpoint1(
    cases_path: Path,
    runs_path: Path,
    status: Path | None,
    out: Path,
    restricted: Path,
    seed: int = 20261028,
    pilot_size: int = 15,
) -> tuple[bool, list[str]]:
    """Write the report and the proposed pilot sample; return (passed, problems)."""
    problems: list[str] = []
    cases = read(cases_path)
    outcomes = read(runs_path)
    if outcomes and not set(OUTCOME_COLUMNS) <= set(outcomes[0]):
        missing = sorted(set(OUTCOME_COLUMNS) - set(outcomes[0]))
        return False, [f"outcomes file is missing columns: {', '.join(missing)}"]
    leaking = [c for c in (cases[0] if cases else {}) if c in ("level", "git_status", "repatch_status")]
    if leaking:
        problems.append(f"cases.csv contains outcome columns {leaking}; move them to outcomes.csv")

    by_case = {c["case_id"]: c for c in cases}
    if len(by_case) != len(cases):
        problems.append("cases.csv has duplicate case_id values")
    out_by_case: dict[str, dict[str, str]] = {}
    for o in outcomes:
        if o["case_id"] in out_by_case:
            problems.append(f"outcomes.csv: duplicate case_id {o['case_id']}")
        out_by_case[o["case_id"]] = o

    # Re-derive and check levels.
    derived: dict[str, str] = {}
    for cid, o in out_by_case.items():
        level, why = derive_level(o)
        derived[cid] = level
        if o["level"] and o["level"] != level:
            problems.append(
                f"{cid}: level entered as {o['level']} but the raw fields give {level} {why}".rstrip()
            )

    # Validate manifest fields.
    for row in cases:
        e = row["eligibility"]
        if e not in ELIGIBILITY:
            problems.append(f"{row['case_id']}: eligibility '{e}' is not one of {sorted(ELIGIBILITY)}")
        if e == "excluded" and row["exclusion_reason"].split(":")[0] not in EXCLUSION_REASONS:
            reason = row["exclusion_reason"]
            problems.append(f"{row['case_id']}: exclusion_reason '{reason}' is not in the list")
        if e == "eligible":
            for col in ("source_pre_sha", "source_post_sha", "target_pre_sha", "repatch_target_sha"):
                if len(row[col]) != 40:
                    problems.append(f"{row['case_id']}: eligible but {col} is not a full 40-character SHA")
            if row["target_pre_sha"] != row["repatch_target_sha"]:
                problems.append(f"{row['case_id']}: target_pre_sha differs from repatch_target_sha")
            if row["case_id"] not in out_by_case:
                problems.append(f"{row['case_id']}: eligible but has no outcomes.csv row")

    # Flags from dde verify.
    stopped: set[str] = set()
    flag_counts: Counter[str] = Counter()
    if status:
        for r in read(status):
            if r["stage"] != "summary":
                flag_counts[r["flag"]] += 1
            if r["stage"] == "summary" and r["flag"] == "stopped":
                stopped.add(r["case_id"])
        for cid in sorted(stopped):
            if by_case.get(cid, {}).get("eligibility") == "eligible":
                problems.append(f"{cid}: eligible but dde verify stopped it; see case_status.csv")

    # Non-MO-only remaining conflicts (flag, not exclusion).
    non_mo_only = []
    for cid, o in out_by_case.items():
        if derived[cid] == "L3" and cid in by_case:
            remaining = split(o["repatch_remaining_conflicting_files"])
            if remaining and not (remaining & split(by_case[cid]["mo_files"])):
                non_mo_only.append(cid)

    overlap = sorted(set(by_case) & set(out_by_case))
    gacpd_only = sorted(set(by_case) - set(out_by_case))
    repatch_only = sorted(set(out_by_case) - set(by_case))
    eligible = [cid for cid in overlap if by_case[cid]["eligibility"] == "eligible" and cid not in stopped]
    ready = [cid for cid in eligible if derived[cid] in ("L1", "L2", "L3")]

    table: dict[str, Counter[str]] = defaultdict(Counter)
    for cid in ready:
        table[by_case[cid]["variant_pair"]][derived[cid]] += 1
    per_level = Counter(derived[cid] for cid in ready)
    pairs_with_cases = {p for p, cnt in table.items() if sum(cnt.values())}

    all_levels = Counter(derived.values())
    failed_cp = all_levels["L2"] + all_levels["L3"]

    # Checkpoint 1 criterion (workplan v3, Section 11).
    criteria = [
        ("at least 12 reconstructable transfers", len(ready) >= 12, f"{len(ready)}"),
        (
            "at least 3 per level",
            all(per_level[lv] >= 3 for lv in ("L1", "L2", "L3")),
            ", ".join(f"{lv}={per_level[lv]}" for lv in ("L1", "L2", "L3")),
        ),
        ("at least 3 variant pairs", len(pairs_with_cases) >= 3, f"{len(pairs_with_cases)}"),
    ]
    passed = all(ok for _, ok, _ in criteria)

    # Proposed pilot sample.
    rng = random.Random(seed)
    pools = {lv: sorted(cid for cid in ready if derived[cid] == lv) for lv in ("L1", "L2", "L3")}
    for pool in pools.values():
        rng.shuffle(pool)
    size = max(12, min(18, pilot_size))
    want = {lv: size // 3 for lv in pools}
    pilot: list[str] = []
    non_kafka_l3 = [c for c in pools["L3"] if not is_kafka(by_case[c]["variant_pair"])]
    if non_kafka_l3:
        pilot.append(non_kafka_l3[0])
    for lv, pool in pools.items():
        for cid in _spread(pool, by_case):
            if sum(derived[p] == lv for p in pilot) >= want[lv]:
                break
            if cid not in pilot:
                pilot.append(cid)
    rng.shuffle(pilot)  # the shared list must not reveal levels by its order
    pilot_non_kafka = sum(not is_kafka(by_case[c]["variant_pair"]) for c in pilot)

    out.mkdir(parents=True, exist_ok=True)
    restricted.mkdir(parents=True, exist_ok=True)
    with (out / "pilot_cases.csv").open("w", newline="", encoding="utf-8") as fh:
        fh.write("case_id\n" + "".join(f"{c}\n" for c in sorted(pilot)))
    with (restricted / "pilot_strata.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["case_id", "variant_pair", "level"])
        for c in sorted(pilot):
            w.writerow([c, by_case[c]["variant_pair"], derived[c]])

    excl = Counter(c["exclusion_reason"].split(":")[0] for c in cases if c["eligibility"] == "excluded")
    lines = [
        "# Checkpoint 1: data integrity\n",
        f"Result: **{'PASS' if passed else 'FAIL'}** (criteria are proposals; the team confirms)\n",
        "## Inventory\n",
        "| Quantity | Count |",
        "| --- | --- |",
        f"| Cases in cases.csv (after deduplication) | {len(cases)} |",
        f"| Cases in outcomes.csv (RePatch) | {len(outcomes)} |",
        f"| In both (overlap) | {len(overlap)} |",
        f"| GACPD only | {len(gacpd_only)} |",
        f"| RePatch only | {len(repatch_only)} |",
        f"| Eligible overlap, not stopped by dde verify | {len(eligible)} |",
        f"| Of which with an L1/L2/L3 label | {len(ready)} |",
        "",
        "## Re-derived RePatch outcomes (all rows of outcomes.csv)\n",
        "| Level | Count |",
        "| --- | --- |",
        *[f"| {lv} | {all_levels[lv]} |" for lv in ("L1", "L2", "L3", "Other")],
        "",
        f"Patch level: {all_levels['L2']} of {failed_cp} patches that failed cherry-pick were "
        "resolved by RePatch (zero remaining conflicting files). The arXiv version of the RePatch "
        "paper reports both 163 of 309 and 155 of 292; record which, if either, this matches.\n",
        "## Eligible cases by pair and level\n",
        "| Pair | L1 | L2 | L3 |",
        "| --- | --- | --- | --- |",
        *[f"| {p} | {t['L1']} | {t['L2']} | {t['L3']} |" for p, t in sorted(table.items())],
        "",
        "## Criteria\n",
        "| Criterion | Met | Value |",
        "| --- | --- | --- |",
        *[f"| {name} | {'yes' if ok else 'no'} | {val} |" for name, ok, val in criteria],
        "",
        "## Exclusions by reason\n",
        "| Reason | Count |",
        "| --- | --- |",
        *[f"| {r} | {n} |" for r, n in sorted(excl.items())],
        "",
        "## dde verify flags\n",
        "| Flag | Count |",
        "| --- | --- |",
        *[f"| {f} | {n} |" for f, n in sorted(flag_counts.items())],
        "",
        f"L3 patches whose remaining conflicts are only in non-MO files: {len(non_mo_only)}"
        + (f" ({', '.join(sorted(non_mo_only))})" if non_mo_only else ""),
        "",
        "## Proposed pilot sample\n",
        f"{len(pilot)} cases, {pilot_non_kafka} outside Kafka, from "
        f"{len({by_case[c]['variant_pair'] for c in pilot})} pairs, "
        f"{'with' if non_kafka_l3 else 'no'} non-Kafka L3 case available. Seed {seed}. "
        "Case ids are in `pilot_cases.csv`; levels are only in `restricted/pilot_strata.csv`.\n",
        "## Problems to fix\n",
        *([f"- {p}" for p in problems] or ["None."]),
        "",
    ]
    (out / "checkpoint1_report.md").write_text("\n".join(lines), encoding="utf-8")
    return passed, problems
