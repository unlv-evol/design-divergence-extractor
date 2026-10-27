from dde.java import parse_java
from dde.matching import match_method
from dde.models import RenameMaps

SRC = parse_java("A.java", """class A {
  void flush(int t) {}
  Order find(String id) { return null; }
  void put(int a) {}
  void put(String a) {}
  void only(int a, int b) {}
  void gone() {}
}""").classes[0]

TGT = parse_java("A.java", """class A {
  void flushAll(int t) {}
  Optional<Order> find(String id) { return null; }
  void put(long a) {}
  void put(Object a) {}
  void only(int a) {}
}""").classes[0]


def m(name: str, params: tuple[str, ...]):  # type: ignore[no-untyped-def]
    return next(x for x in SRC.methods if x.name == name and x.params == params)


def test_match_values() -> None:
    src_maps = RenameMaps()
    tgt_maps = RenameMaps(methods={("A", "flushAll", 1): "flush"}, flat_methods={"flushAll": "flush"})

    def value(name: str, params: tuple[str, ...]) -> str:
        return match_method(m(name, params), "A", TGT, src_maps, tgt_maps).value

    assert value("flush", ("int",)) == "renamed"
    assert value("find", ("String",)) == "exact"  # return type is I5's business
    assert value("put", ("int",)) == "ambiguous"  # two same-arity overloads
    assert value("only", ("int", "int")) == "name_only"
    assert value("gone", ()) == "absent"
