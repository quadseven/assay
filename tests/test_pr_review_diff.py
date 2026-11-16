"""pr_review defect localization on fixed diffs: no network."""

from __future__ import annotations

import prr_diff as d

PR = """\
diff --git a/app/core.py b/app/core.py
index 1111111..2222222 100644
--- a/app/core.py
+++ b/app/core.py
@@ -10,3 +10,6 @@ def run():
     start()
+    total = sum(items) / len(items)
+    if total > limit:
+        return None
     finish()
     cleanup()
diff --git a/app/new.py b/app/new.py
new file mode 100644
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,2 @@
+def helper():
+    return 1
"""

FIX_REWRITE = """\
diff --git a/app/core.py b/app/core.py
--- a/app/core.py
+++ b/app/core.py
@@ -40,4 +40,4 @@ def run():
     start()
-    total = sum(items) / len(items)
+    total = sum(items) / max(len(items), 1)
     if total > limit:
"""

FIX_INSERT = """\
diff --git a/app/core.py b/app/core.py
--- a/app/core.py
+++ b/app/core.py
@@ -41,3 +41,5 @@ def run():
     if total > limit:
+        log.warning("over limit")
+        alert()
         return None
"""

FIX_ELSEWHERE = """\
diff --git a/other.py b/other.py
--- a/other.py
+++ b/other.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
"""


def test_parse_diff_numbers_added_lines_on_the_head_side():
    files = d.parse_diff(PR)
    assert files["app/core.py"].added == {
        11: "    total = sum(items) / len(items)",
        12: "    if total > limit:",
        13: "        return None",
    }
    assert files["app/new.py"].added == {1: "def helper():", 2: "    return 1"}


def test_parse_diff_handles_a_deleted_file_by_its_old_path():
    text = "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n"
    assert d.parse_diff(text)["gone.py"].removed == [(1, "x = 1")]


def test_a_rewritten_line_locates_the_defect_on_the_original_head_line():
    locs, method = d.locate(d.parse_diff(PR), d.parse_diff(FIX_REWRITE))
    assert method == "removed"
    assert locs == [("app/core.py", 11, 11)]


def test_an_insert_only_fix_falls_back_to_its_context_lines():
    locs, method = d.locate(d.parse_diff(PR), d.parse_diff(FIX_INSERT))
    assert method == "context"
    assert locs == [("app/core.py", 12, 13)]


FIX_TWO_FILES = (
    FIX_REWRITE
    + """\
diff --git a/app/new.py b/app/new.py
--- a/app/new.py
+++ b/app/new.py
@@ -1,2 +1,3 @@
 def helper():
+    log.info("helper")
     return 1
"""
)


def test_each_file_falls_back_to_context_on_its_own():
    locs, method = d.locate(d.parse_diff(PR), d.parse_diff(FIX_TWO_FILES))
    assert method == "removed+context"
    assert locs == [("app/core.py", 11, 11), ("app/new.py", 1, 2)]


def test_a_fix_sharing_no_file_locates_nothing():
    assert d.locate(d.parse_diff(PR), d.parse_diff(FIX_ELSEWHERE)) == ([], "none")


def test_braces_alone_never_anchor_a_defect():
    assert not d.significant("    })")
    assert d.significant("return x")


def test_cluster_splits_on_the_gap():
    assert d.cluster([1, 2, 5, 30, 31], gap=8) == [(1, 5), (30, 31)]


def test_whole_pr_locations_cover_every_added_run():
    assert d.whole_pr_locations(d.parse_diff(PR)) == [("app/core.py", 11, 13), ("app/new.py", 1, 2)]
