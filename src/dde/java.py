"""Stage 5: Java profiles from tree-sitter.

Name-level only: tree-sitter resolves no types, so everything here is a
syntactic reading of one file (spec, Known limitations).
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import metadata

import tree_sitter_java
from tree_sitter import Language, Node, Parser

from dde.models import ClassDecl, JavaFile, MethodDecl

_TYPE_DECLS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_BODY_TYPES = {"class_body", "interface_body", "enum_body", "enum_body_declarations",
               "annotation_type_body"}
_GENERICS = re.compile(r"<[^<>]*>")


@lru_cache(maxsize=1)
def _parser() -> Parser:
    return Parser(Language(tree_sitter_java.language()))


def grammar_versions() -> dict[str, str]:
    out = {}
    for dist in ("tree-sitter", "tree-sitter-java"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "missing"
    return out


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", "replace")


def normalize_type(text: str) -> str:
    """Type text with whitespace and annotations removed."""
    text = re.sub(r"@\w+(\([^)]*\))?", "", text)
    return re.sub(r"\s+", "", text)


def base_type_name(text: str) -> str:
    """``java.util.List<String>`` -> ``List``."""
    t = normalize_type(text)
    while _GENERICS.search(t):
        t = _GENERICS.sub("", t)
    t = t.replace("[]", "")
    return t.rsplit(".", 1)[-1]


def parse_java(path: str, source: str) -> JavaFile:
    tree = _parser().parse(source.encode("utf-8"))
    root = tree.root_node
    package = ""
    for child in root.named_children:
        if child.type == "package_declaration":
            for c in child.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    package = _text(c)
    classes: list[ClassDecl] = []
    for child in root.named_children:
        if child.type in _TYPE_DECLS:
            _collect_class(child, package, None, classes)
    return JavaFile(path=path, package=package, classes=tuple(classes), has_error=root.has_error)


def _collect_class(
    node: Node, package: str, outer: str | None, out: list[ClassDecl]
) -> None:
    simple = _text(node.child_by_field_name("name"))
    local = f"{outer}.{simple}" if outer else simple
    body = node.child_by_field_name("body")
    type_params = _type_params(node)
    methods: list[MethodDecl] = []
    fields: list[str] = []
    nested: list[Node] = []
    if body is not None:
        for member in _members(body):
            if member.type in ("method_declaration", "constructor_declaration",
                               "compact_constructor_declaration"):
                methods.append(_method(member))
            elif member.type in ("field_declaration", "constant_declaration"):
                for d in member.named_children:
                    if d.type == "variable_declarator":
                        fields.append(_text(d.child_by_field_name("name")))
            elif member.type in _TYPE_DECLS:
                nested.append(member)
    if node.type == "record_declaration":
        params = node.child_by_field_name("parameters")
        if params is not None:
            for p in params.named_children:
                if p.type == "formal_parameter":
                    fields.append(_text(p.child_by_field_name("name")))
    decl = ClassDecl(
        local_name=local,
        simple_name=simple,
        qualified_name=f"{package}.{local}" if package else local,
        kind=_TYPE_DECLS[node.type],
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        methods=tuple(methods),
        fields=tuple(fields),
        supertypes=tuple(sorted(_supertypes(node))),
        collaborators=frozenset(_collaborators(node, simple, set(type_params))),
        type_params=tuple(type_params),
        has_error=node.has_error,
        text=" " * node.start_point[1] + _text(node),
    )
    out.append(decl)
    for n in nested:
        _collect_class(n, package, local, out)


def _members(body: Node) -> list[Node]:
    members = []
    for c in body.named_children:
        if c.type in _BODY_TYPES:  # enum_body_declarations nests the members
            members.extend(_members(c))
        else:
            members.append(c)
    return members


def _type_params(node: Node) -> list[str]:
    tp = node.child_by_field_name("type_parameters")
    if tp is None:
        return []
    names = []
    for p in tp.named_children:
        if p.type == "type_parameter":
            ident = next((c for c in p.named_children if c.type in ("type_identifier", "identifier")), None)
            if ident is not None:
                names.append(_text(ident))
    return names


def _method(node: Node) -> MethodDecl:
    is_ctor = node.type != "method_declaration"
    name = "<init>" if is_ctor else _text(node.child_by_field_name("name"))
    params: list[str] = []
    plist = node.child_by_field_name("parameters")
    if plist is not None:
        for p in plist.named_children:
            if p.type == "formal_parameter":
                params.append(normalize_type(_text(p.child_by_field_name("type"))))
            elif p.type == "spread_parameter":
                t = next((c for c in p.named_children
                          if c.type not in ("modifiers", "variable_declarator")), None)
                params.append(normalize_type(_text(t)) + "...")
    ret = "" if is_ctor else normalize_type(_text(node.child_by_field_name("type")))
    calls: set[str] = set()
    body = node.child_by_field_name("body")
    if body is not None:
        _walk_calls(body, calls)
    return MethodDecl(
        name=name,
        params=tuple(params),
        return_type=ret,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        calls=frozenset(calls),
        text=" " * node.start_point[1] + _text(node),
    )


def _walk_calls(node: Node, calls: set[str]) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            name = n.child_by_field_name("name")
            if name is not None:
                calls.add(_text(name))
        stack.extend(n.named_children)


def _supertypes(node: Node) -> set[str]:
    names: set[str] = set()
    for field in ("superclass", "interfaces"):
        sub = node.child_by_field_name(field)
        if sub is not None:
            names.update(_type_list_names(sub))
    for c in node.named_children:  # interface_declaration: extends_interfaces
        if c.type in ("extends_interfaces", "super_interfaces", "superclass"):
            names.update(_type_list_names(c))
    return {n for n in names if n}


def _type_list_names(node: Node) -> list[str]:
    out = []
    for c in node.named_children:
        if c.type == "type_list":
            out.extend(_type_list_names(c))
        elif c.type in ("type_identifier", "generic_type", "scoped_type_identifier"):
            out.append(base_type_name(_text(c)))
    return out


def _collaborators(cls: Node, own: str, type_params: set[str]) -> set[str]:
    """Type names used in the class body, plus capitalized static-call receivers.

    Nested type declarations are separate entities and are not descended into.
    """
    found: set[str] = set()
    body = cls.child_by_field_name("body")
    heads = [c for c in (cls.child_by_field_name("superclass"),
                         cls.child_by_field_name("interfaces")) if c is not None]
    stack = ([body] if body is not None else []) + heads
    while stack:
        n = stack.pop()
        if n is not body and n.type in _TYPE_DECLS:
            continue
        if n.type == "type_identifier":
            found.add(_text(n))
        elif n.type == "method_invocation":
            obj = n.child_by_field_name("object")
            if obj is not None and obj.type == "identifier":
                t = _text(obj)
                if t[:1].isupper():
                    found.add(t)
        stack.extend(n.named_children)
    local_types = set(type_params)
    stack = [body] if body is not None else []
    while stack:  # method-level type parameters are not collaborators either
        n = stack.pop()
        if n.type in ("method_declaration", "constructor_declaration"):
            local_types.update(_type_params(n))
        if n is body or n.type not in _TYPE_DECLS:
            stack.extend(n.named_children)
    return {t for t in found if t[:1].isupper() and t != own and t not in local_types}
