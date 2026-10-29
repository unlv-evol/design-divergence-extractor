"""Runs one case through stages 1-8 and returns rows for every output file.

Nothing in this module reads integration outcomes: its only inputs are the
manifest row, the pair's repository, and RefactoringMiner (R3).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dde import aggregate as agg
from dde.diff import parse_unified_diff
from dde.entities import NO_CLASS, ClassEntity, MethodEntity, edited_entities
from dde.git import GitError, Repo
from dde.indicators import (
    CallResolver,
    ClassMeasures,
    Measure,
    class_measures,
    contract_change,
    introduced_calls,
    method_match_measure,
    relocation,
)
from dde.java import parse_java
from dde.matching import (
    ClassMatch,
    FileMatch,
    MethodMatch,
    TargetTree,
    anc_class_name,
    match_class,
    match_file,
    match_method,
)
from dde.models import Case, FilePatch, Flag, JavaFile, RenameMaps, ResolvedRevisions, State
from dde.refminer import (
    Refactoring,
    RefactoringMinerError,
    build_rename_maps,
    counts_touching,
    normalize,
    run_range,
)
from dde.revisions import verify_revisions

log = logging.getLogger(__name__)
Row = dict[str, Any]


@dataclass(frozen=True)
class Settings:
    repos_dir: Path
    rm_executable: Path | None
    rm_cache: Path
    rm_timeout: int = 3600
    use_refactorings: bool = True  # False: identity rename maps, flagged per case


@dataclass
class MethodPacket:
    label: str
    kind: str
    pre_text: str | None
    post_text: str | None
    target_text: str | None
    target_note: str


@dataclass
class ClassPacket:
    local_name: str
    source_summary: str
    target_summary: str | None
    target_note: str


@dataclass
class FilePacket:
    source_path: str
    target_path: str | None
    diff_text: str
    classes: list[ClassPacket] = field(default_factory=list)
    methods: list[MethodPacket] = field(default_factory=list)


@dataclass
class CaseResult:
    case: Case
    revisions: ResolvedRevisions | None = None
    flags: list[Flag] = field(default_factory=list)
    method_rows: list[Row] = field(default_factory=list)
    class_rows: list[Row] = field(default_factory=list)
    file_rows: list[Row] = field(default_factory=list)
    patch_row: Row | None = None
    match_rows: list[Row] = field(default_factory=list)
    refactoring_rows: list[Row] = field(default_factory=list)
    packet_files: list[FilePacket] = field(default_factory=list)
    packet_refactorings: list[str] = field(default_factory=list)
    relocation_leaves: list[agg.Summary] = field(default_factory=list)

    @property
    def stopped(self) -> bool:
        return any(f.stops_case for f in self.flags)

    def flag(self, stage: str, name: str, detail: str = "", stops: bool = False) -> None:
        self.flags.append(Flag(self.case.case_id, stage, name, detail, stops))


# ---------------------------------------------------------------------------------


def verify_case(case: Case, settings: Settings) -> CaseResult:
    """Stage 1 plus the patch-level file check."""
    res = CaseResult(case)
    repo = Repo(settings.repos_dir / case.variant_pair)
    res.revisions, flags = verify_revisions(case, repo)
    res.flags.extend(flags)
    if res.revisions is not None:
        _check_diff_files(res, repo, res.revisions)
    return res


def _check_diff_files(res: CaseResult, repo: Repo, rev: ResolvedRevisions) -> list[str]:
    changed = repo.changed_files(rev.source_pre, rev.source_post)
    listed = set(res.case.mo_files) | set(res.case.non_mo_files)
    extra = sorted(set(changed) - listed)
    missing = sorted(listed - set(changed))
    if extra or missing:
        res.flag("patch", "diff_files_differ_from_manifest",
                 f"in diff only: {';'.join(extra)} | in manifest only: {';'.join(missing)}")
    for p in res.case.mo_files:
        if p not in changed:
            res.flag("patch", "mo_file_not_in_diff", p)
    return changed


def run_case(case: Case, settings: Settings, with_indicators: bool = True) -> CaseResult:
    """All stages for one case. Errors are recorded, never raised."""
    res = verify_case(case, settings)
    if res.revisions is None or res.stopped:
        return res
    try:
        _run_stages(res, settings, with_indicators)
    except (GitError, OSError, ValueError) as exc:  # one bad case must not stop a run
        log.exception("case %s failed", case.case_id)
        res.flag("pipeline", "case_failed", f"{type(exc).__name__}: {exc}", stops=True)
    return res


def _run_stages(res: CaseResult, settings: Settings, with_indicators: bool) -> None:
    case, rev = res.case, res.revisions
    assert rev is not None
    repo = Repo(settings.repos_dir / case.variant_pair)

    # Stage 2: patch
    all_patches = parse_unified_diff(repo.diff(rev.source_pre, rev.source_post))
    mo_set = set(case.mo_files)
    mo_patches = [fp for fp in all_patches if fp.path in mo_set]

    # Stage 3: renames, both sides since the ancestor
    src_refs, src_maps, src_used = _side_renames(res, repo, settings, "source", rev.ancestor,
                                                 rev.source_pre)
    tgt_refs, tgt_maps, tgt_used = _side_renames(res, repo, settings, "target", rev.ancestor,
                                                 rev.target_pre)

    target = TargetTree(repo, rev.target_pre)
    resolver = CallResolver(target)
    src_files = repo.ls_files(rev.source_pre)
    tgt_files = repo.ls_files(rev.target_pre)
    relevant_src: set[str] = set()
    relevant_tgt: set[str] = set()

    for fp in sorted(mo_patches, key=lambda f: f.path):
        relevant_src |= {fp.path, src_maps.file_to_anc(fp.path)}
        fm = match_file(fp.path, src_maps, tgt_maps, target)  # Stage 4
        if fm.target_path:
            relevant_tgt |= {fm.target_path, tgt_maps.file_to_anc(fm.target_path)}
        _process_file(res, repo, rev, fp, fm, src_maps, tgt_maps, target, resolver,
                      src_files, tgt_files, with_indicators)

    _refactoring_rows(res, (src_refs, src_used, relevant_src), (tgt_refs, tgt_used, relevant_tgt))
    if with_indicators:
        res.patch_row = _patch_row(res, repo, rev, all_patches, settings,
                                   (src_refs, src_maps, relevant_src),
                                   (tgt_refs, tgt_maps, relevant_tgt))


def _side_renames(
    res: CaseResult, repo: Repo, settings: Settings, side: str, start: str, end: str
) -> tuple[list[Refactoring], RenameMaps, set[int]]:
    if not settings.use_refactorings:
        res.flag("renames", "renames_skipped", f"{side}: identity rename maps (--no-refactorings)")
        return [], RenameMaps(available=True, note="identity"), set()
    if settings.rm_executable is None:
        res.flag("renames", "refactoringminer_not_configured", side)
        return [], RenameMaps(available=False, note="RefactoringMiner not configured"), set()
    try:
        commits = run_range(settings.rm_executable, repo, start, end,
                            settings.rm_cache / res.case.variant_pair, settings.rm_timeout)
    except RefactoringMinerError as exc:
        res.flag("renames", "refactoringminer_failed", f"{side}: {exc}")
        return [], RenameMaps(available=False, note=str(exc)), set()
    refs = normalize(commits, side, repo.rev_list(start, end))
    maps, used = build_rename_maps(refs)
    return refs, maps, used


# -- per file -----------------------------------------------------------------------


def _parse(repo: Repo, sha: str, path: str | None) -> JavaFile | None:
    if path is None:
        return None
    text = repo.read_file(sha, path)
    return None if text is None else parse_java(path, text)


def _process_file(
    res: CaseResult,
    repo: Repo,
    rev: ResolvedRevisions,
    fp: FilePatch,
    fm: FileMatch,
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
    target: TargetTree,
    resolver: CallResolver,
    src_files: frozenset[str],
    tgt_files: frozenset[str],
    with_indicators: bool,
) -> None:
    cid = res.case.case_id
    pre = _parse(repo, rev.source_pre, fp.old_path)
    post = _parse(repo, rev.source_post, fp.new_path)
    tfile = target.parse(fm.target_path) if fm.target_path else None
    if not fp.path.endswith(".java"):
        res.flag("profiles", "non_java_mo_file", fp.path)
    for role, jf in (("source_pre", pre), ("source_post", post), ("target_pre", tfile)):
        if jf is not None and jf.has_error:
            res.flag("profiles", "parse_error", f"{role}:{jf.path}")
    _flag_fix_present(res, fp, target, fm)

    res.match_rows.append({
        "case_id": cid, "level": "file", "source_path": fp.path, "source_entity": "",
        "source_lines": "", "target_path": fm.target_path or "", "target_entity": "",
        "target_lines": "", "how": fm.how, "evidence": fm.evidence,
    })
    methods, classes = edited_entities(fp, pre, post)
    packet = FilePacket(fp.path, fm.target_path, _diff_text(fp))
    res.packet_files.append(packet)

    rename_ok = src_maps.available and tgt_maps.available
    class_info: dict[str, tuple[ClassEntity, ClassMatch | None, ClassMeasures | None]] = {}
    for ce in classes:
        src_cls = ce.pre
        if src_cls is None:  # class added by the patch
            class_info[ce.local_name] = (ce, None, None)
            packet.classes.append(ClassPacket(ce.local_name, _summary(ce.post) if ce.post else "",
                                              None, "Class added by the patch."))
            continue
        cm = match_class(src_cls, fm, src_maps, tgt_maps, target)
        cms = class_measures(src_cls, cm, src_maps, tgt_maps, target) if with_indicators else None
        class_info[ce.local_name] = (ce, cm, cms)
        res.match_rows.append({
            "case_id": cid, "level": "class", "source_path": fp.path,
            "source_entity": src_cls.local_name,
            "source_lines": f"{src_cls.start_line}-{src_cls.end_line}",
            "target_path": cm.target_path or "",
            "target_entity": cm.target.local_name if cm.target else "",
            "target_lines": f"{cm.target.start_line}-{cm.target.end_line}" if cm.target else "",
            "how": cm.value, "evidence": cm.evidence,
        })
        packet.classes.append(ClassPacket(
            src_cls.local_name, _summary(src_cls),
            _summary(cm.target) if cm.target else None,
            "" if cm.target else "No counterpart class located in the target.",
        ))

    method_summaries: dict[str, dict[str, list[agg.Summary]]] = {}
    for me in methods:
        mrow, mp = _method(res, fp, me, class_info, src_maps, tgt_maps, resolver,
                           rename_ok, with_indicators)
        packet.methods.append(mp)
        if mrow is None:
            continue
        res.method_rows.append(mrow)
        if me.class_local != NO_CLASS:
            bucket = method_summaries.setdefault(me.class_local, {k: [] for k in agg.METHOD_LEVEL})
            bucket["method_match"].append(
                agg.leaf(mrow["method_match"], State(mrow["method_match_state"]), "any"))
            bucket["contract"].append(agg.leaf(None, State(mrow["contract_state"]), "any"))
            bucket["unresolved_ratio"].append(
                agg.leaf(mrow["unresolved_ratio"], State(mrow["calls_state"]), "max"))

    if not with_indicators:
        return

    file_class_summaries: dict[str, list[agg.Summary]] = {
        k: [] for k in (*agg.CLASS_LEVEL, *agg.METHOD_LEVEL)
    }
    for local, (ce, _cm, cms) in sorted(class_info.items()):
        row: Row = {"case_id": cid, "file": fp.path, "class": local,
                    "hunks": _hunks(ce.hunks)}
        if cms is None:
            na = Measure.na("class added by the patch")
            measures = {k: na for k in agg.CLASS_LEVEL}
        elif not rename_ok:
            un = Measure.unavailable("rename maps unavailable")
            measures = {k: un for k in agg.CLASS_LEVEL}
            measures["class_match"] = Measure(cms.class_match.value, State.UNAVAILABLE,
                                              "rename maps unavailable")
        else:
            measures = {k: getattr(cms, k) for k in agg.CLASS_LEVEL}
            row["relocated_to"] = "; ".join(cms.relocated_to)
        for k, m in measures.items():
            row[k] = m.value
            row[f"{k}_state"] = m.state.value
            file_class_summaries[k].append(agg.leaf(m.value, m.state, agg.WORST[k]))
        rolled = method_summaries.get(local, {k: [] for k in agg.METHOD_LEVEL})
        for k in agg.METHOD_LEVEL:
            s = agg.combine(rolled[k], agg.WORST[k])
            _put_summary(row, k, s)
            file_class_summaries[k].append(s)
        res.class_rows.append(row)

    rel = relocation(pre or post, fp.path, src_files, fm, tfile, tgt_files)
    frow: Row = {"case_id": cid, "file": fp.path, "target_path": fm.target_path or "",
                 "file_match": fm.how, "hunks": len(fp.hunks),
                 "lines_added": fp.added, "lines_deleted": fp.deleted,
                 "package_changed": rel.package_changed, "module_changed": rel.module_changed,
                 "relocation_state": rel.state.value}
    for k in (*agg.CLASS_LEVEL, *agg.METHOD_LEVEL):
        _put_summary(frow, k, agg.combine(file_class_summaries[k], agg.WORST[k]))
    res.relocation_leaves.append(agg.leaf(None, rel.state, "any"))
    res.file_rows.append(frow)


def _method(
    res: CaseResult,
    fp: FilePatch,
    me: MethodEntity,
    class_info: dict[str, tuple[ClassEntity, ClassMatch | None, ClassMeasures | None]],
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
    resolver: CallResolver,
    rename_ok: bool,
    with_indicators: bool,
) -> tuple[Row | None, MethodPacket]:
    cid = res.case.case_id
    row: Row = {"case_id": cid, "file": fp.path, "class": me.class_local, "method": me.label,
                "entity_kind": me.kind, "hunks": _hunks(me.hunks),
                "source_pre_lines": _span(me.pre), "source_post_lines": _span(me.post)}
    _, cm, _ = class_info.get(me.class_local, (None, None, None))
    target_text: str | None = None
    note = ""
    mm: MethodMatch | None = None
    contract: tuple[bool | None, bool | None, Measure]

    if me.kind == "outside_method":
        match = Measure.na("edit outside any method")
        contract = (None, None, Measure.na("edit outside any method"))
        calls_m: tuple[Any, Any, Any, Measure] = (None, None, None, Measure.na("edit outside any method"))
        note = "Edit outside any method."
    else:
        if me.pre is None:
            match = Measure.na("method added by the patch")
            contract = (None, None, Measure.na("method added by the patch"))
            note = "Method added by the patch."
        elif cm is None or cm.target is None:
            match = Measure.unresolved("no counterpart class")
            contract = (None, None, Measure.unresolved("no counterpart class"))
            note = "No counterpart class located in the target."
        else:
            src_cls = class_info[me.class_local][0].pre
            assert src_cls is not None
            mm = match_method(me.pre, anc_class_name(src_cls, src_maps), cm.target,
                              src_maps, tgt_maps)
            match = method_match_measure(mm)
            if mm.target is not None:
                target_text = mm.target.text
                c = contract_change(me.pre, mm.target, mm, src_maps, tgt_maps)
                contract = (c.params_changed, c.return_changed, Measure(None, c.state, c.note))
            else:
                contract = (None, None, Measure.unresolved(f"method matched as {mm.value}"))
                note = ("Several candidate methods in the target class; see the class summary."
                        if mm.value == "ambiguous" else "No counterpart method located in the target class.")
            res.match_rows.append({
                "case_id": cid, "level": "method", "source_path": fp.path,
                "source_entity": f"{me.class_local}#{me.pre.signature}",
                "source_lines": _span(me.pre), "target_path": cm.target_path or "",
                "target_entity": f"{cm.target.local_name}#{mm.target.signature}" if mm.target else "",
                "target_lines": _span(mm.target), "how": mm.value, "evidence": mm.evidence,
            })
        if with_indicators:
            c6 = introduced_calls(me.pre, me.post, src_maps, tgt_maps, resolver)
            calls_m = (c6.n_introduced, c6.n_unresolved, c6.ratio,
                       Measure(None, c6.state, ";".join(c6.unresolved_names) or c6.note))
        else:
            calls_m = (None, None, None, Measure.na())

    packet = MethodPacket(me.label, me.kind, me.pre.text if me.pre else None,
                          me.post.text if me.post else None, target_text, note)
    if not with_indicators:
        return None, packet

    if not rename_ok and me.kind != "outside_method":
        un = Measure.unavailable("rename maps unavailable")
        match = un if match.state.computed or match.state is State.UNRESOLVED else match
        contract = (None, None, un) if contract[2].state is not State.NOT_APPLICABLE else contract
        if calls_m[3].state is not State.NOT_APPLICABLE:
            calls_m = (None, None, None, un)
    row.update({
        "method_match": match.value, "method_match_state": match.state.value,
        "params_changed": contract[0], "return_changed": contract[1],
        "contract_state": contract[2].state.value,
        "n_introduced": calls_m[0], "n_unresolved_calls": calls_m[1],
        "unresolved_ratio": calls_m[2], "calls_state": calls_m[3].state.value,
        "unresolved_calls": calls_m[3].note if calls_m[3].state is State.DIVERGENT else "",
    })
    return row, packet


# -- patch level --------------------------------------------------------------------


def _patch_row(
    res: CaseResult,
    repo: Repo,
    rev: ResolvedRevisions,
    all_patches: list[FilePatch],
    settings: Settings,
    source: tuple[list[Refactoring], RenameMaps, set[str]],
    target: tuple[list[Refactoring], RenameMaps, set[str]],
) -> Row:
    case = res.case
    row: Row = {
        "case_id": case.case_id,
        "variant_pair": case.variant_pair,
        "n_files": len(all_patches),
        "n_hunks": sum(len(fp.hunks) for fp in all_patches),
        "lines_added": sum(fp.added for fp in all_patches),
        "lines_deleted": sum(fp.deleted for fp in all_patches),
        "n_mo_files": len(case.mo_files),
        "n_non_mo_files": len(case.non_mo_files),
        "divergence_age_days": round(
            (repo.commit_time(rev.target_pre) - repo.commit_time(rev.ancestor)) / 86400, 1),
    }
    for side, (refs, maps, paths) in (("source", source), ("target", target)):
        if maps.available and settings.use_refactorings:
            counts: Counter[str] = counts_touching(refs, paths)
            row[f"refactorings_{side}"] = sum(counts.values())
            row[f"refactorings_{side}_by_type"] = ";".join(f"{t}:{n}" for t, n in sorted(counts.items()))
        else:
            row[f"refactorings_{side}"] = None
            row[f"refactorings_{side}_by_type"] = ""
    for k in (*agg.CLASS_LEVEL, *agg.METHOD_LEVEL):
        items = [_get_summary(fr, k) for fr in res.file_rows]
        _put_summary(row, k, agg.combine(items, agg.WORST[k]))
    _put_summary(row, "relocation", agg.combine(res.relocation_leaves, "any"))
    return row


def _refactoring_rows(
    res: CaseResult,
    *sides: tuple[list[Refactoring], set[int], set[str]],
) -> None:
    """Refactorings touching the case's files, on each side, for the audit trail."""
    for refs, used, paths in sides:
        for i, r in enumerate(refs):
            if not (r.files & paths):
                continue
            res.refactoring_rows.append({
                "case_id": res.case.case_id, "side": r.side, "commit": r.commit,
                "commit_order": r.order, "type": r.type, "description": r.description,
                "left_files": ";".join(sorted({x.file_path for x in r.left})),
                "right_files": ";".join(sorted({x.file_path for x in r.right})),
                "used_for_rename_map": i in used,
            })
            res.packet_refactorings.append(f"[{r.side}] {r.type}: {r.description}")


# -- helpers ------------------------------------------------------------------------


def _put_summary(row: Row, key: str, s: agg.Summary) -> None:
    row[f"{key}_worst"] = s.worst
    row[f"{key}_mean"] = s.mean
    row[f"{key}_n"] = s.n
    row[f"{key}_state"] = s.state.value
    row[f"{key}_unavailable_share"] = s.unavailable_share


def _get_summary(row: Row, key: str) -> agg.Summary:
    return agg.Summary(row[f"{key}_worst"], row[f"{key}_mean"], row[f"{key}_n"],
                       State(row[f"{key}_state"]), row[f"{key}_unavailable_share"])


def _hunks(h: set[int]) -> str:
    return ";".join(str(i) for i in sorted(h))


def _span(m: Any) -> str:
    return f"{m.start_line}-{m.end_line}" if m is not None else ""


def _diff_text(fp: FilePatch) -> str:
    out = []
    for h in fp.hunks:
        out.append(f"@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@ {h.section}".rstrip())
        out.extend(f"{ln.tag}{ln.text}" for ln in h.lines)
    return "\n".join(out)


def _summary(cls: Any) -> str:
    lines = [f"{cls.kind} {cls.local_name}  (lines {cls.start_line}-{cls.end_line})"]
    if cls.supertypes:
        lines.append(f"  supertypes: {', '.join(cls.supertypes)}")
    if cls.fields:
        lines.append(f"  fields: {', '.join(cls.fields)}")
    for m in cls.methods:
        ret = f" : {m.return_type}" if m.return_type else ""
        lines.append(f"  {m.signature}{ret}")
    return "\n".join(lines)


_TRIVIAL = frozenset("{}();,")


def fix_possibly_present(fp: FilePatch, target: TargetTree, fm: FileMatch) -> str | None:
    """Detail text when every substantive added line of ``fp`` is already in the target file.

    Substantive: longer than three characters, not only punctuation, not a comment or import.
    Public because ``dde-prep`` uses the same rule for Stage A re-verification.
    """
    if fm.target_path is None:
        return None
    text = target.text(fm.target_path)
    if text is None:
        return None
    have = {ln.strip() for ln in text.splitlines()}
    added = [t.strip() for h in fp.hunks for t in h.added_texts]
    substantive = [t for t in added if len(t) > 3 and not set(t) <= _TRIVIAL
                   and not t.startswith(("//", "*", "/*", "import "))]
    if substantive and all(t in have for t in substantive):
        return f"{fp.path}: all {len(substantive)} added lines found in {fm.target_path}"
    return None


def _flag_fix_present(res: CaseResult, fp: FilePatch, target: TargetTree, fm: FileMatch) -> None:
    detail = fix_possibly_present(fp, target, fm)
    if detail:
        res.flag("revisions", "fix_possibly_present", detail)
