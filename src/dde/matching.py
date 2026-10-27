"""Stages 4 and 6: file correspondence and class/method matching (I1).

All comparisons happen in ancestor vocabulary: each side's names are mapped
back through its own rename map before they are compared, so refactorings
RePatch already handles are not counted as divergence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from dde.git import Repo
from dde.java import parse_java
from dde.models import ClassDecl, JavaFile, MethodDecl, RenameMaps

_IDENT = re.compile(r"\b[A-Z][\w$]*\b")


@dataclass(frozen=True)
class FileMatch:
    source_path: str
    target_path: str | None
    how: str  # same_path | renamed | unique_class | ambiguous | unresolved
    evidence: str = ""


@dataclass(frozen=True)
class ClassMatch:
    value: str  # same_name | renamed | unique_elsewhere | ambiguous | absent
    target: ClassDecl | None
    target_path: str | None
    evidence: str = ""


@dataclass(frozen=True)
class MethodMatch:
    value: str  # exact | renamed | name_arity | name_only | ambiguous | absent
    target: MethodDecl | None
    evidence: str = ""


class TargetTree:
    """Read-only access to the target at its pin, with parsed-file caching."""

    def __init__(self, repo: Repo, sha: str) -> None:
        self.repo = repo
        self.sha = sha

    def text(self, path: str) -> str | None:
        return self.repo.read_file(self.sha, path)

    def parse(self, path: str) -> JavaFile | None:
        return _parse_at(self.repo, self.sha, path)

    def parse_many(self, paths: list[str]) -> dict[str, JavaFile]:
        texts = self.repo.read_files(self.sha, paths)
        return {p: _parse_cached(p, t) for p, t in texts.items() if t is not None}

    def exists(self, path: str) -> bool:
        return self.repo.file_exists(self.sha, path)

    def files_named(self, filename: str) -> list[str]:
        return sorted(p for p in self.repo.ls_files(self.sha) if PurePosixPath(p).name == filename)

    def files_declaring(self, simple_name: str) -> list[str]:
        pattern = rf"(class|interface|enum|record)[[:space:]]+{re.escape(simple_name)}([^A-Za-z0-9_$]|$)"
        return self.repo.grep_regex_files(self.sha, pattern)

    def files_calling(self, name: str) -> list[str]:
        return self.repo.grep_files(self.sha, f"{name}(")


def _parse_at(repo: Repo, sha: str, path: str) -> JavaFile | None:
    text = repo.read_file(sha, path)
    return None if text is None else _parse_cached(path, text)


@lru_cache(maxsize=4096)
def _parse_cached(path: str, text: str) -> JavaFile:
    return parse_java(path, text)


# -- ancestor vocabulary -----------------------------------------------------------


def anc_type(text: str, maps: RenameMaps) -> str:
    """Type text with every class name mapped to the ancestor's name."""
    return _IDENT.sub(lambda m: maps.simple_to_anc(m.group(0)), text)


def anc_class_name(cls: ClassDecl, maps: RenameMaps) -> str:
    return maps.class_to_anc(cls.qualified_name)


def anc_method_key(m: MethodDecl, anc_cls: str, maps: RenameMaps) -> tuple[str, int]:
    return maps.method_to_anc(anc_cls, m.name, m.arity), m.arity


def anc_params(m: MethodDecl, maps: RenameMaps) -> tuple[str, ...]:
    return tuple(anc_type(p, maps) for p in m.params)


# -- stage 4: files --------------------------------------------------------------


def match_file(
    source_path: str, src_maps: RenameMaps, tgt_maps: RenameMaps, target: TargetTree
) -> FileMatch:
    if target.exists(source_path):
        return FileMatch(source_path, source_path, "same_path")
    anc = src_maps.file_to_anc(source_path)
    candidate = tgt_maps.anc_to_file(anc)
    if candidate != source_path and target.exists(candidate):
        return FileMatch(source_path, candidate, "renamed",
                         f"rename map: {source_path} -> {anc} -> {candidate}")
    stem = PurePosixPath(source_path).stem
    tgt_simple = tgt_maps.anc_to_simple(src_maps.simple_to_anc(stem))
    found = target.files_named(f"{tgt_simple}.java")
    if len(found) == 1:
        return FileMatch(source_path, found[0], "unique_class", f"only {tgt_simple}.java in target")
    if len(found) > 1:
        return FileMatch(source_path, None, "ambiguous", f"{len(found)} files named {tgt_simple}.java")
    return FileMatch(source_path, None, "unresolved", f"no {tgt_simple}.java in target")


# -- stage 6: classes and methods -----------------------------------------------


def match_class(
    src_cls: ClassDecl,
    file_match: FileMatch,
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
    target: TargetTree,
) -> ClassMatch:
    anc = anc_class_name(src_cls, src_maps)
    anc_local = _local_part(anc, src_cls)
    if file_match.target_path is not None and file_match.how != "unique_class":
        tfile = target.parse(file_match.target_path)
        if tfile is not None:
            raw = tfile.by_local_name(src_cls.local_name)
            if raw is not None:
                return ClassMatch("same_name", raw, file_match.target_path, "same local name")
            for c in tfile.classes:
                if _local_part(anc_class_name(c, tgt_maps), c) == anc_local:
                    return ClassMatch("renamed", c, file_match.target_path,
                                      f"{src_cls.local_name} and {c.local_name} share ancestor {anc_local}")
    # Not in the counterpart file: search the whole target by name.
    tgt_simple = tgt_maps.anc_to_simple(src_maps.simple_to_anc(src_cls.simple_name))
    hits: list[tuple[str, ClassDecl]] = []
    for path, jf in sorted(target.parse_many(target.files_declaring(tgt_simple)).items()):
        hits.extend((path, c) for c in jf.classes if c.simple_name == tgt_simple)
    if len(hits) == 1:
        return ClassMatch("unique_elsewhere", hits[0][1], hits[0][0], f"only declaration of {tgt_simple}")
    if len(hits) > 1:
        where = "; ".join(f"{p}:{c.local_name}" for p, c in hits[:5])
        return ClassMatch("ambiguous", None, None, f"{len(hits)} declarations of {tgt_simple}: {where}")
    return ClassMatch("absent", None, None, f"no declaration of {tgt_simple} in target")


def _local_part(qualified_anc: str, cls: ClassDecl) -> str:
    """The last N dotted parts, N being the nesting depth of ``cls``."""
    depth = cls.local_name.count(".") + 1
    return ".".join(qualified_anc.split(".")[-depth:])


def match_method(
    src_m: MethodDecl,
    src_anc_cls: str,
    tgt_cls: ClassDecl,
    src_maps: RenameMaps,
    tgt_maps: RenameMaps,
) -> MethodMatch:
    tgt_anc_cls = anc_class_name(tgt_cls, tgt_maps)
    for t in tgt_cls.methods:
        if t.name == src_m.name and t.params == src_m.params:
            return MethodMatch("exact", t, f"{t.signature} at lines {t.start_line}-{t.end_line}")
    s_name, s_arity = anc_method_key(src_m, src_anc_cls, src_maps)
    s_params = anc_params(src_m, src_maps)
    keyed = [(t, anc_method_key(t, tgt_anc_cls, tgt_maps), anc_params(t, tgt_maps)) for t in tgt_cls.methods]
    renamed = [t for t, (n, _), p in keyed if n == s_name and p == s_params]
    if len(renamed) == 1:
        t = renamed[0]
        return MethodMatch("renamed", t, f"{src_m.signature} ~ {t.signature} via rename maps")
    same_arity = [t for t, (n, a), _ in keyed if n == s_name and a == s_arity]
    if len(same_arity) == 1:
        t = same_arity[0]
        return MethodMatch("name_arity", t, f"{t.signature}: same name and arity")
    if len(same_arity) > 1:
        return MethodMatch("ambiguous", None, f"{len(same_arity)} overloads of {s_name}/{s_arity}")
    same_name = [t for t, (n, _), _ in keyed if n == s_name]
    if len(same_name) == 1:
        t = same_name[0]
        return MethodMatch("name_only", t, f"{t.signature}: same name, arity differs")
    if len(same_name) > 1:
        return MethodMatch("ambiguous", None, f"{len(same_name)} methods named {s_name}")
    return MethodMatch("absent", None, f"no method {s_name} in {tgt_cls.local_name}")
