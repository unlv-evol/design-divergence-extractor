from dde.diff import parse_unified_diff

DIFF = """diff --git a/A.java b/A.java
index 1..2 100644
--- a/A.java
+++ b/A.java
@@ -2,4 +2,5 @@ class A {
   void f() {
-    x();
+    y();
+    z();
   }
@@ -10,2 +11,3 @@ void g() {
   int a;
+  int b;
   int c;
diff --git a/B.java b/B.java
new file mode 100644
--- /dev/null
+++ b/B.java
@@ -0,0 +1,2 @@
+class B {
+}
\\ No newline at end of file
"""


def test_files_hunks_and_counts() -> None:
    files = parse_unified_diff(DIFF)
    assert [(f.old_path, f.new_path) for f in files] == [("A.java", "A.java"), (None, "B.java")]
    a, b = files
    assert [h.index for h in a.hunks] == [1, 2]
    assert (a.added, a.deleted) == (3, 1)
    assert a.hunks[0].deleted_old_lines == [3]
    assert a.hunks[0].added_new_lines == [3, 4]
    assert a.hunks[0].section == "class A {"
    assert (b.added, b.deleted, b.path) == (2, 0, "B.java")


def test_insertion_points_name_both_neighbours() -> None:
    a = parse_unified_diff(DIFF)[0]
    assert a.hunks[0].insertion_points == []  # a replacement, not an insertion
    assert a.hunks[1].insertion_points == [(10, 11)]
    b = parse_unified_diff(DIFF)[1]
    assert b.hunks[0].insertion_points == [(None, None)]
