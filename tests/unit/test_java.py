from dde.java import base_type_name, parse_java

SRC = """package com.shop;
import java.util.*;
public class A<T> extends Base<T> implements I, java.io.Closeable {
  private int x, y;
  public A(int a) { super(); }
  public <R> Optional<R> find(final @Nullable String id, List<Map<String, Integer>> m, int... rest) {
    Foo f = Helper.make(); f.go(); return null;
  }
  void anon() { new Runnable() { public void run() { inner(); } }; }
  interface J extends K, L<T> { void j(); }
}
class Second { void s() {} }
"""


def test_classes_methods_and_members() -> None:
    jf = parse_java("A.java", SRC)
    assert jf.package == "com.shop" and not jf.has_error
    assert [c.local_name for c in jf.classes] == ["A", "A.J", "Second"]
    a = jf.classes[0]
    assert a.qualified_name == "com.shop.A"
    assert a.fields == ("x", "y")
    assert a.supertypes == ("Base", "Closeable", "I")
    assert [m.signature for m in a.methods] == [
        "<init>(int)", "find(String,List<Map<String,Integer>>,int...)", "anon()"]
    find = a.methods[1]
    assert find.return_type == "Optional<R>"
    assert find.calls == {"make", "go"}


def test_collaborators_exclude_self_type_params_and_nested() -> None:
    a = parse_java("A.java", SRC).classes[0]
    assert "A" not in a.collaborators and "T" not in a.collaborators and "R" not in a.collaborators
    assert {"Foo", "Helper", "Optional", "Runnable", "Base"} <= a.collaborators
    assert "K" not in a.collaborators  # belongs to the nested interface J


def test_innermost_lookup_uses_named_methods() -> None:
    jf = parse_java("A.java", SRC)
    cls, m = jf.innermost_method(9)  # inside the anonymous class in anon()
    assert (cls.local_name, m.name) == ("A", "anon")
    assert jf.innermost_class(10).local_name == "A.J"
    assert jf.innermost_method(4) is None  # a field line


def test_base_type_name() -> None:
    assert base_type_name("java.util.List<Map<String, Integer>>") == "List"
    assert base_type_name("String[]") == "String"


def test_parse_errors_are_reported_not_hidden() -> None:
    jf = parse_java("Bad.java", "class Bad { void f( { }")
    assert jf.has_error
