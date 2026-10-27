from dde.diff import parse_unified_diff
from dde.entities import OUTSIDE, edited_entities
from dde.java import parse_java

PRE = """class A {
  int f;
  void m() {
    a();
  }
}
class B {
  void n() {
    b();
  }
}
"""
POST = """class A {
  int f = 1;
  void m() {
    a();
    c();
  }
  void added() {
  }
}
class B {
  void n() {
    b2();
  }
}
"""
DIFF = """diff --git a/X.java b/X.java
--- a/X.java
+++ b/X.java
@@ -1,11 +1,14 @@
 class A {
-  int f;
+  int f = 1;
   void m() {
     a();
+    c();
   }
+  void added() {
+  }
 }
 class B {
   void n() {
-    b();
+    b2();
   }
 }
"""


def entities():
    (fp,) = parse_unified_diff(DIFF)
    return edited_entities(fp, parse_java("X.java", PRE), parse_java("X.java", POST))


def test_two_edited_classes_give_two_class_entities() -> None:
    _, classes = entities()
    assert [c.local_name for c in classes] == ["A", "B"]


def test_method_kinds() -> None:
    methods, _ = entities()
    kinds = {(m.class_local, m.label): m.kind for m in methods}
    assert kinds == {
        ("A", OUTSIDE): "outside_method",
        ("A", "m()"): "method",
        ("A", "added()"): "added_by_patch",
        ("B", "n()"): "method",
    }
    m = next(e for e in methods if e.label == "m()")
    assert m.pre is not None and m.post is not None and "c" in m.post.calls


def test_insertion_between_methods_is_not_attributed_to_the_previous_method() -> None:
    methods, _ = entities()
    m = next(e for e in methods if e.label == "m()")
    assert m.hunks == {1}
    added = next(e for e in methods if e.label == "added()")
    assert added.pre is None
