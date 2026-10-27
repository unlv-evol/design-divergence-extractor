"""Stage 7: indicators I1-I8 and their measurement states (spec Sections 6-7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dde.matching import (
    ClassMatch,
    FileMatch,
    MethodMatch,
    TargetTree,
    anc_class_name,
    anc_method_key,
    anc_params,
    anc_type,
)
from dde.models import ClassDecl, JavaFile, MethodDecl, RenameMaps, State

BUILD_FILES = ("build.gradle", "build.gradle.kts", "pom.xml", "build.xml", "build.sbt")
MAX_CANDIDATE_FILES = 2000


@dataclass(frozen=True)
class Measure:
    value: float | int | str | bool | None
    state: State
    note: str = ""

    @staticmethod
    def na(note: str = "") -> Measure:
        return Measure(None, State.NOT_APPLICABLE, note)

    @staticmethod
    def unresolved(note: str = "") -> Measure:
        return Measure(None, State.UNRESOLVED, note)

    @staticmethod
    def unavailable(note: str = "") -> Measure:
        return Measure(None, State.UNAVAILABLE, note)


def jaccard(a: set[object], b: set[object]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _overlap(a: set[object], b: set[object]) -> Measure:
    j = jaccard(a, b)
    return Measure(j, State.ZERO if j == 1.0 else State.DIVERGENT)


# -- I1 -------------------------------------------------------------------------

CLASS_MATCH_STATE = {
    "same_name": State.ZERO,
    "renamed": State.ZERO,
    "unique_elsewhere": State.DIVERGENT,
    "absent": State.DIVERGENT,
    "ambiguous": State.UNRESOLVED,
}
METHOD_MATCH_STATE = {
    "exact": State.ZERO,
    "renamed": State.ZERO,
    "name_arity": State.DIVERGENT,
    "name_only": State.DIVERGENT,
    "absent": State.DIVERGENT,
    "ambiguous": State.UNRESOLVED,
}
CONTRACT_COMPARABLE = frozenset({"exact", "renamed", "name_arity"})


def class_match_measure(cm: ClassMatch) -> Measure:
    return Measure(cm.value, CLASS_MATCH_STATE[cm.value], cm.evidence)


def method_match_measure(mm: MethodMatch) -> Measure:
    return Measure(mm.value, METHOD_MATCH_STATE[mm.value], mm.evidence)


# -- method level: I5, I6 -----------------------------------------------------------


@dataclass(frozen=True)
class ContractMeasure:
    params_changed: bool | None
    return_changed: bool | None
    state: State
    note: str = ""


def contract_change(
    src: MethodDecl, tgt: MethodDecl, mm: MethodMatch, src_maps: RenameMaps, tgt_maps: RenameMaps
) -> ContractMeasure:
    if mm.value not in CONTRACT_COMPARABLE:
        return ContractMeasure(None, None, State.UNRESOLVED, f"method matched as {mm.value}")
    params = anc_params(src, src_maps) != anc_params(tgt, tgt_maps)
    ret = anc_type(src.return_type, src_maps) != anc_type(tgt.return_type, tgt_maps)
    return ContractMeasure(params, ret, State.DIVERGENT if (params or ret) else State.ZERO)


@dataclass(frozen=True)
class CallsMeasure:
    n_introduced: int | None
    n_unresolved: int | None
    ratio: float | None
    state: State
    unresolved_names: tuple[str, ...] = ()
    note: str = ""


class CallResolver:
    """Does ``name(`` occur anywhere in the target at its pin? (I6, a lower bound)."""

    def __init__(self, target: TargetTree) -> None:
        self.target = target
        self._seen: dict[str, bool] = {}

    def occurs(self, name: str) -> bool:
        if name not in self._seen:
            pattern = rf"(^|[^A-Za-z0-9_$]){re.escape(name)}[[:space:]]*\("
            self._seen[name] = bool(self.target.repo.grep_regex_files(self.target.sha, pattern))
        return self._seen[name]


def introduced_calls(
    pre: MethodDecl | None,
    post: MethodDecl | None,
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
    resolver: CallResolver,
) -> CallsMeasure:
    if post is None:
        return CallsMeasure(None, None, None, State.NOT_APPLICABLE, note="method deleted by the patch")
    introduced = sorted(post.calls - (pre.calls if pre else frozenset()))
    if not introduced:
        return CallsMeasure(0, 0, None, State.NOT_APPLICABLE, note="no introduced calls")
    unresolved = []
    for name in introduced:
        anc = src_maps.flat_methods.get(name, name)
        candidates = sorted(tgt_maps.anc_to_flat_method_names(anc) | {name})
        if not any(resolver.occurs(c) for c in candidates):
            unresolved.append(name)
    ratio = len(unresolved) / len(introduced)
    return CallsMeasure(
        len(introduced),
        len(unresolved),
        ratio,
        State.DIVERGENT if unresolved else State.ZERO,
        tuple(unresolved),
    )


# -- class level: I2, I3, I4, I7 -------------------------------------------------------


@dataclass
class ClassMeasures:
    class_match: Measure
    method_jaccard: Measure
    field_jaccard: Measure
    n_lost: Measure
    n_relocated: Measure
    collab_jaccard: Measure
    supertypes_changed: Measure
    relocated_to: list[str] = field(default_factory=list)


def _all(m: Measure) -> ClassMeasures:
    return ClassMeasures(m, m, m, m, m, m, m)


def class_measures(
    src: ClassDecl,
    cm: ClassMatch,
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
    target: TargetTree,
) -> ClassMeasures:
    match = class_match_measure(cm)
    tgt = cm.target
    if tgt is None or cm.target_path is None:
        dep = Measure.unresolved(f"class matched as {cm.value}")
        out = _all(dep)
        out.class_match = match
        return out
    if src.has_error or tgt.has_error:
        out = _all(Measure.unavailable("parse error inside the class"))
        out.class_match = match
        return out
    s_anc, t_anc = anc_class_name(src, src_maps), anc_class_name(tgt, tgt_maps)
    ms = {anc_method_key(m, s_anc, src_maps) for m in src.methods}
    mt = {anc_method_key(m, t_anc, tgt_maps) for m in tgt.methods}
    fs = {src_maps.field_to_anc(s_anc, f) for f in src.fields}
    ft = {tgt_maps.field_to_anc(t_anc, f) for f in tgt.fields}
    own = {src_maps.simple_to_anc(src.simple_name), tgt_maps.simple_to_anc(tgt.simple_name)}
    cs = {src_maps.simple_to_anc(c) for c in src.collaborators} - own
    ct = {tgt_maps.simple_to_anc(c) for c in tgt.collaborators} - own
    ss = {src_maps.simple_to_anc(s) for s in src.supertypes}
    st = {tgt_maps.simple_to_anc(s) for s in tgt.supertypes}

    lost = sorted(ms - mt)
    relocated, note = _relocated(lost, tgt, cm.target_path, tgt_maps, target)
    if relocated is None:
        n_lost = n_reloc = Measure.unavailable(note)
        where: list[str] = []
    else:
        state = State.DIVERGENT if lost else State.ZERO
        n_lost = Measure(len(lost), state)
        n_reloc = Measure(len(relocated), state)
        where = relocated
    return ClassMeasures(
        class_match=match,
        method_jaccard=_overlap(set(ms), set(mt)),
        field_jaccard=_overlap(set(fs), set(ft)),
        n_lost=n_lost,
        n_relocated=n_reloc,
        collab_jaccard=_overlap(set(cs), set(ct)),
        supertypes_changed=Measure(ss != st, State.DIVERGENT if ss != st else State.ZERO),
        relocated_to=where,
    )


def _relocated(
    lost: list[tuple[str, int]],
    tgt_cls: ClassDecl,
    tgt_path: str,
    tgt_maps: RenameMaps,
    target: TargetTree,
) -> tuple[list[str] | None, str]:
    """Lost methods found, same name and arity, in a different target class."""
    found: list[str] = []
    for anc_name, arity in lost:
        names = sorted(tgt_maps.anc_to_flat_method_names(anc_name))
        paths: set[str] = set()
        for n in names:
            paths.update(target.files_calling(n))
        if len(paths) > MAX_CANDIDATE_FILES:
            return None, f"{len(paths)} candidate files for {anc_name}; above the search limit"
        hit = None
        for path, jf in sorted(target.parse_many(sorted(paths)).items()):
            for c in jf.classes:
                if path == tgt_path and c.local_name == tgt_cls.local_name:
                    continue
                c_anc = anc_class_name(c, tgt_maps)
                for m in c.methods:
                    if m.arity == arity and tgt_maps.method_to_anc(c_anc, m.name, arity) == anc_name:
                        hit = f"{anc_name}/{arity} -> {path}:{c.local_name}"
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            found.append(hit)
    return found, ""


# -- file level: I8 ------------------------------------------------------------------


@dataclass(frozen=True)
class RelocationMeasure:
    package_changed: bool | None
    module_changed: bool | None
    state: State
    note: str = ""


def module_of(path: str, files: frozenset[str]) -> str:
    """Top-level build module of a path: its first directory, if that has a build file."""
    if "/" not in path:
        return ""
    top = path.split("/", 1)[0]
    return top if any(f"{top}/{b}" in files for b in BUILD_FILES) else ""


def relocation(
    src_file: JavaFile | None,
    src_path: str,
    src_files: frozenset[str],
    fm: FileMatch,
    tgt_file: JavaFile | None,
    tgt_files: frozenset[str],
) -> RelocationMeasure:
    if fm.target_path is None:
        return RelocationMeasure(None, None, State.UNRESOLVED, f"file correspondence {fm.how}")
    if src_file is None or tgt_file is None:
        return RelocationMeasure(None, None, State.UNAVAILABLE, "file could not be read")
    pkg = src_file.package != tgt_file.package
    mod = module_of(src_path, src_files) != module_of(fm.target_path, tgt_files)
    return RelocationMeasure(pkg, mod, State.DIVERGENT if (pkg or mod) else State.ZERO)
