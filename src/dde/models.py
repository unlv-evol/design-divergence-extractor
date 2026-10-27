"""Shared value types.

Kept deliberately small: plain frozen dataclasses and one enum. Everything that
crosses a module boundary is defined here so the pipeline stages stay decoupled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class State(StrEnum):
    """Measurement state of one indicator value (spec, Section 7).

    Exactly one state applies to each value. ``DIVERGENT`` and ``ZERO`` are the
    only computed states; the others say why there is no number.
    """

    DIVERGENT = "divergent"
    ZERO = "zero"
    NOT_APPLICABLE = "not_applicable"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"

    @property
    def computed(self) -> bool:
        return self in (State.DIVERGENT, State.ZERO)


@dataclass(frozen=True)
class Case:
    """One row of ``cases.csv``: a directed patch-transfer attempt."""

    case_id: str
    mo_id: str
    variant_pair: str
    direction: str
    source_repo: str
    target_repo: str
    pr_number: str
    source_pre_sha: str
    source_post_sha: str
    target_pre_sha: str
    repatch_target_sha: str
    ancestor_sha: str
    mo_files: tuple[str, ...]
    non_mo_files: tuple[str, ...]
    eligibility: str
    exclusion_reason: str

    @property
    def eligible(self) -> bool:
        return self.eligibility.strip().lower() in ("eligible", "yes", "true", "1")


@dataclass(frozen=True)
class ResolvedRevisions:
    """Stage 1 output: the four pinned pre-integration states (R4)."""

    source_pre: str
    source_post: str
    target_pre: str
    ancestor: str
    ancestor_from_metadata: bool = False


@dataclass(frozen=True)
class Flag:
    """A verification flag or per-stage error, written to ``case_status.csv``."""

    case_id: str
    stage: str
    flag: str
    detail: str = ""
    stops_case: bool = False


@dataclass(frozen=True)
class DiffLine:
    tag: str  # " ", "+", "-"
    text: str
    old_no: int | None
    new_no: int | None


@dataclass(frozen=True)
class Hunk:
    index: int  # 1-based within the file
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: tuple[DiffLine, ...]

    @property
    def deleted_old_lines(self) -> list[int]:
        return [ln.old_no for ln in self.lines if ln.tag == "-" and ln.old_no is not None]

    @property
    def added_new_lines(self) -> list[int]:
        return [ln.new_no for ln in self.lines if ln.tag == "+" and ln.new_no is not None]

    @property
    def insertion_points(self) -> list[tuple[int | None, int | None]]:
        """Old-side lines on either side of each pure insertion.

        An insertion has no old line of its own. It is inside a pre-patch method
        only if the old lines just before and just after it are in that same
        method, so both neighbours are returned; ``None`` marks a file edge.
        """
        points: list[tuple[int | None, int | None]] = []
        lines = self.lines
        i = 0
        while i < len(lines):
            if lines[i].tag != "+":
                i += 1
                continue
            j = i
            while j < len(lines) and lines[j].tag == "+":
                j += 1
            if i == 0 or lines[i - 1].tag != "-":  # a run after deletions is a replacement
                before = next(
                    (ln.old_no for ln in reversed(lines[:i]) if ln.old_no is not None), None
                )
                if before is None and self.old_start > 1:
                    before = self.old_start - 1
                after = next((ln.old_no for ln in lines[j:] if ln.old_no is not None), None)
                points.append((before, after))
            i = j
        return points

    @property
    def added_texts(self) -> list[str]:
        return [ln.text for ln in self.lines if ln.tag == "+"]


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None  # None when the patch adds the file
    new_path: str | None  # None when the patch deletes the file
    hunks: tuple[Hunk, ...]

    @property
    def path(self) -> str:
        return self.old_path or self.new_path or ""

    @property
    def added(self) -> int:
        return sum(len(h.added_new_lines) for h in self.hunks)

    @property
    def deleted(self) -> int:
        return sum(len(h.deleted_old_lines) for h in self.hunks)


@dataclass(frozen=True)
class MethodDecl:
    name: str  # "<init>" for constructors
    params: tuple[str, ...]  # parameter type texts, whitespace removed
    return_type: str  # "" for constructors
    start_line: int
    end_line: int
    calls: frozenset[str]
    text: str

    @property
    def arity(self) -> int:
        return len(self.params)

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(self.params)})"


@dataclass(frozen=True)
class ClassDecl:
    local_name: str  # Outer.Inner within the file
    simple_name: str
    qualified_name: str  # package + local name
    kind: str  # class | interface | enum | record | annotation
    start_line: int
    end_line: int
    methods: tuple[MethodDecl, ...]
    fields: tuple[str, ...]
    supertypes: tuple[str, ...]  # simple names, generics stripped
    collaborators: frozenset[str]
    type_params: tuple[str, ...]
    has_error: bool
    text: str


@dataclass(frozen=True)
class JavaFile:
    path: str
    package: str
    classes: tuple[ClassDecl, ...]  # nested classes flattened, outer first
    has_error: bool

    def innermost_class(self, line: int) -> ClassDecl | None:
        best: ClassDecl | None = None
        for c in self.classes:
            if c.start_line <= line <= c.end_line and (
                best is None or c.end_line - c.start_line < best.end_line - best.start_line
            ):
                best = c
        return best

    def innermost_method(self, line: int) -> tuple[ClassDecl, MethodDecl] | None:
        cls = self.innermost_class(line)
        while cls is not None:
            for m in cls.methods:
                if m.start_line <= line <= m.end_line:
                    return cls, m
            cls = self.outer_of(cls)
        return None

    def outer_of(self, cls: ClassDecl) -> ClassDecl | None:
        if "." not in cls.local_name:
            return None
        outer = cls.local_name.rsplit(".", 1)[0]
        return next((c for c in self.classes if c.local_name == outer), None)

    def by_local_name(self, local_name: str) -> ClassDecl | None:
        return next((c for c in self.classes if c.local_name == local_name), None)


@dataclass
class RenameMaps:
    """One side's renames, from current (side) names into ancestor names.

    ``available`` is False when RefactoringMiner failed for this side; the maps
    are then empty and dependent indicators are ``unavailable``.
    """

    available: bool = True
    note: str = ""
    classes: dict[str, str] = field(default_factory=dict)  # qualified -> qualified
    simple_classes: dict[str, str] = field(default_factory=dict)  # simple -> simple
    files: dict[str, str] = field(default_factory=dict)  # path -> path
    methods: dict[tuple[str, str, int], str] = field(default_factory=dict)
    flat_methods: dict[str, str] = field(default_factory=dict)  # name -> name
    fields: dict[tuple[str, str], str] = field(default_factory=dict)

    # -- side -> ancestor ------------------------------------------------------
    def class_to_anc(self, qualified: str) -> str:
        return self.classes.get(qualified, qualified)

    def simple_to_anc(self, simple: str) -> str:
        return self.simple_classes.get(simple, simple)

    def method_to_anc(self, anc_class: str, name: str, arity: int) -> str:
        return self.methods.get((anc_class, name, arity), name)

    def field_to_anc(self, anc_class: str, name: str) -> str:
        return self.fields.get((anc_class, name), name)

    def file_to_anc(self, path: str) -> str:
        return self.files.get(path, path)

    # -- ancestor -> side (inverse; ambiguous inverses are dropped) -------------
    def anc_to_file(self, anc_path: str) -> str:
        inv = _invert(self.files)
        return inv.get(anc_path, anc_path)

    def anc_to_simple(self, anc_simple: str) -> str:
        inv = _invert(self.simple_classes)
        return inv.get(anc_simple, anc_simple)

    def anc_to_flat_method_names(self, anc_name: str) -> set[str]:
        names = {cur for cur, anc in self.flat_methods.items() if anc == anc_name}
        return names | {anc_name}


def _invert(mapping: dict[str, str]) -> dict[str, str]:
    seen: dict[str, str] = {}
    dup: set[str] = set()
    for cur, anc in mapping.items():
        if anc in seen:
            dup.add(anc)
        seen[anc] = cur
    return {k: v for k, v in seen.items() if k not in dup}
