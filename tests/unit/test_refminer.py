import json
from pathlib import Path

from dde.refminer import Location, Refactoring, build_rename_maps, normalize, parse_method_element

DATA = Path(__file__).parent.parent / "data"


def ref(order: int, type_: str, desc: str, left: list[Location], right: list[Location]) -> Refactoring:
    return Refactoring("target", f"c{order}", order, type_, desc, tuple(left), tuple(right))


def cls(path: str, name: str) -> Location:
    return Location(path, "TYPE_DECLARATION", name, 1, 1)


def meth(path: str, element: str) -> Location:
    return Location(path, "METHOD_DECLARATION", element, 1, 1)


def test_parse_method_element() -> None:
    assert parse_method_element("public flush(timeout int) : void") == ("flush", 1)
    assert parse_method_element("public f() : void") == ("f", 0)
    assert parse_method_element("m(a Map<String, Integer>, b int) : List<X>") == ("m", 2)


def test_real_refactoringminer_json_is_read() -> None:
    """Recorded from RefactoringMiner 3.1.6 on a toy repository."""
    data = json.loads((DATA / "rm_3.1.6_rename_move_class.json").read_text())
    refs = normalize(data["commits"], "target", [c["sha1"] for c in data["commits"]])
    maps, used = build_rename_maps(refs)
    assert maps.methods[("com.shop.OrderService", "flushAll", 1)] == "flush"
    assert maps.flat_methods["flushAll"] == "flush"
    assert maps.classes["com.shop.io.Writer"] == "com.shop.Writer"
    assert maps.files["src/main/java/com/shop/io/Writer.java"] == "src/main/java/com/shop/Writer.java"
    assert len(used) == 2

    data = json.loads((DATA / "rm_3.1.6_rename_class_move_method.json").read_text())
    refs = normalize(data["commits"], "target", [c["sha1"] for c in data["commits"]])
    maps, used = build_rename_maps(refs)
    assert maps.classes == {"com.shop.OrderManager": "com.shop.OrderService"}
    assert maps.simple_classes == {"OrderManager": "OrderService"}
    assert {r.type for i, r in enumerate(refs) if i in used} == {"Rename Class"}  # Move Method not


def test_chains_follow_successive_renames() -> None:
    refs = [
        ref(0, "Rename Class", "", [cls("A.java", "p.A")], [cls("B.java", "p.B")]),
        ref(1, "Move Class", "", [cls("B.java", "p.B")], [cls("q/B.java", "q.B")]),
        ref(2, "Rename Method", "Rename Method f() : void renamed to g() : void in class q.B",
            [meth("q/B.java", "f() : void")], [meth("q/B.java", "g() : void")]),
    ]
    maps, _ = build_rename_maps(refs)
    assert maps.classes == {"q.B": "p.A"}
    assert maps.files == {"q/B.java": "A.java"}
    assert maps.methods == {("p.A", "g", 0): "f"}  # keyed by the ancestor class


def test_a_name_claimed_by_two_originals_is_dropped() -> None:
    refs = [
        ref(0, "Rename Class", "", [cls("A.java", "p.A")], [cls("C.java", "p.C")]),
        ref(1, "Rename Class", "", [cls("B.java", "p.B")], [cls("C.java", "p.C")]),
    ]
    maps, _ = build_rename_maps(refs)
    assert "p.C" not in maps.classes


def test_inverse_maps() -> None:
    refs = [ref(0, "Rename Method", "Rename Method f() : void renamed to g() : void in class p.A",
                [meth("A.java", "f() : void")], [meth("A.java", "g() : void")])]
    maps, _ = build_rename_maps(refs)
    assert maps.anc_to_flat_method_names("f") == {"f", "g"}
