"""Map MO hunks onto the methods and classes they edit (spec Section 6, levels).

Deleted lines and in-method insertions are placed in the pre-patch file; added
lines in the post-patch file. Pre and post versions of a method are then
paired, so a method the patch adds or deletes is recognised as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dde.models import ClassDecl, FilePatch, JavaFile, MethodDecl

OUTSIDE = "<outside-method>"
NO_CLASS = "<no-class>"


@dataclass
class MethodEntity:
    class_local: str
    kind: str  # method | added_by_patch | deleted_by_patch | outside_method
    pre: MethodDecl | None = None
    post: MethodDecl | None = None
    hunks: set[int] = field(default_factory=set)

    @property
    def label(self) -> str:
        m = self.pre or self.post
        return m.signature if m is not None else OUTSIDE


@dataclass
class ClassEntity:
    local_name: str
    pre: ClassDecl | None
    post: ClassDecl | None
    hunks: set[int] = field(default_factory=set)


def _pair(m: MethodDecl, candidates: tuple[MethodDecl, ...]) -> MethodDecl | None:
    exact = [c for c in candidates if c.name == m.name and c.params == m.params]
    if exact:
        return exact[0]
    arity = [c for c in candidates if c.name == m.name and c.arity == m.arity]
    if len(arity) == 1:
        return arity[0]
    return None


def edited_entities(
    fp: FilePatch, pre: JavaFile | None, post: JavaFile | None
) -> tuple[list[MethodEntity], list[ClassEntity]]:
    methods: dict[tuple[str, str], MethodEntity] = {}
    classes: dict[str, ClassEntity] = {}

    def touch_class(local: str, hunk: int) -> None:
        if local == NO_CLASS:
            return
        ent = classes.get(local)
        if ent is None:
            ent = ClassEntity(
                local,
                pre.by_local_name(local) if pre else None,
                post.by_local_name(local) if post else None,
            )
            classes[local] = ent
        ent.hunks.add(hunk)

    def add_pre(line: int, hunk: int) -> None:
        if pre is None:
            return
        hit = pre.innermost_method(line)
        if hit is not None:
            cls, m = hit
            key = (cls.local_name, "pre:" + m.signature)
            ent = methods.setdefault(key, MethodEntity(cls.local_name, "method", pre=m))
            ent.hunks.add(hunk)
            touch_class(cls.local_name, hunk)
        else:
            cls_or_none = pre.innermost_class(line)
            local = cls_or_none.local_name if cls_or_none else NO_CLASS
            ent = methods.setdefault((local, OUTSIDE), MethodEntity(local, "outside_method"))
            ent.hunks.add(hunk)
            touch_class(local, hunk)

    def add_post(line: int, hunk: int) -> None:
        if post is None:
            return
        hit = post.innermost_method(line)
        if hit is not None:
            cls, m = hit
            key = (cls.local_name, "post:" + m.signature)
            ent = methods.setdefault(key, MethodEntity(cls.local_name, "method", post=m))
            ent.hunks.add(hunk)
            touch_class(cls.local_name, hunk)
        else:
            cls_or_none = post.innermost_class(line)
            local = cls_or_none.local_name if cls_or_none else NO_CLASS
            ent = methods.setdefault((local, OUTSIDE), MethodEntity(local, "outside_method"))
            ent.hunks.add(hunk)
            touch_class(local, hunk)

    for h in fp.hunks:
        for line in h.deleted_old_lines:
            add_pre(line, h.index)
        if pre is not None:
            for before, after in h.insertion_points:
                if before is None or after is None:
                    continue
                a = pre.innermost_method(before)
                b = pre.innermost_method(after)
                if a is not None and b is not None and a[1] is b[1]:
                    add_pre(before, h.index)
        for line in h.added_new_lines:
            add_post(line, h.index)

    # Pair pre-only and post-only entries of the same method.
    merged: dict[tuple[str, str], MethodEntity] = {}
    pres = {k: e for k, e in methods.items() if e.pre is not None}
    posts = {k: e for k, e in methods.items() if e.post is not None}
    for k, e in methods.items():
        if e.kind == "outside_method":
            merged[k] = e
    for (local, _), e in sorted(pres.items(), key=lambda kv: kv[0]):
        assert e.pre is not None
        post_cls = post.by_local_name(local) if post else None
        partner = _pair(e.pre, post_cls.methods) if post_cls else None
        e.post = partner
        if partner is None:
            e.kind = "deleted_by_patch"
        else:
            pk = (local, "post:" + partner.signature)
            if pk in posts:
                e.hunks |= posts.pop(pk).hunks
        merged[(local, "m:" + e.pre.signature)] = e
    for (local, _), e in sorted(posts.items(), key=lambda kv: kv[0]):
        assert e.post is not None
        pre_cls = pre.by_local_name(local) if pre else None
        partner = _pair(e.post, pre_cls.methods) if pre_cls else None
        key = (local, "m:" + (partner.signature if partner else "+" + e.post.signature))
        if partner is not None and key in merged:
            merged[key].hunks |= e.hunks
            continue
        e.pre = partner
        e.kind = "method" if partner is not None else "added_by_patch"
        merged[key] = e
    ordered_methods = [merged[k] for k in sorted(merged)]
    ordered_classes = [classes[k] for k in sorted(classes)]
    return ordered_methods, ordered_classes
