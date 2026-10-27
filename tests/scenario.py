"""A small fork scenario, built as a real git repository.

It is the workplan's own illustration: the target renames ``flush`` (a
refactoring), moves retry handling into a new ``RetryPolicy`` collaborator
(responsibility moved), changes ``find`` to return ``Optional`` (contract
changed), and moves ``Writer`` to another package. The source patch edits all
of these places and calls a method the target does not have.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

P = "src/main/java/com/shop"

ORDER_SERVICE = """package com.shop;

import java.util.List;

public class OrderService extends BaseService implements Service {
    private int retries;
    private Repo repo;

    public void flush(int timeout) {
        repo.save(timeout);
    }

    public Order find(String id) {
        return repo.load(id);
    }

    public void retryOrder(Order o, int n) {
        for (int i = 0; i < n; i++) {
            repo.save(i);
        }
    }

    static class Audit {
        void log(String m) {
            System.out.println(m);
        }
    }
}
"""

WRITER = """package com.shop;

public class Writer {
    public void write(String s) {
        System.out.println(s);
    }
}
"""

BASE = """package com.shop;

public class BaseService {
    public void start() {
    }
}
"""


@dataclass
class Scenario:
    repo: Path
    ancestor: str
    source_pre: str
    source_post: str
    target_pre: str
    rm_spec: Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, msg: str, when: str) -> str:
    git(repo, "add", "-A")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg],
                   env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
                        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
                   check=True)
    return git(repo, "rev-parse", "HEAD")


def _loc(path: str, kind: str, element: str) -> dict[str, object]:
    return {"filePath": path, "startLine": 1, "endLine": 1, "startColumn": 0, "endColumn": 0,
            "codeElementType": kind, "description": "", "codeElement": element}


def build(root: Path) -> Scenario:
    repo = root / "shop--shopfork"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    write(repo, f"{P}/OrderService.java", ORDER_SERVICE)
    write(repo, f"{P}/Writer.java", WRITER)
    write(repo, f"{P}/BaseService.java", BASE)
    write(repo, "build.gradle", "// root\n")
    ancestor = commit(repo, "ancestor", "2024-01-01T00:00:00Z")

    # Target fork: divergent design decisions.
    git(repo, "checkout", "-q", "-b", "target")
    svc = ORDER_SERVICE.replace("public void flush(int timeout)", "public void flushAll(int timeout)")
    write(repo, f"{P}/OrderService.java", svc)
    commit(repo, "rename flush", "2024-02-01T00:00:00Z")
    svc = svc.replace(
        """    public void retryOrder(Order o, int n) {
        for (int i = 0; i < n; i++) {
            repo.save(i);
        }
    }

""", "")
    svc = svc.replace("import java.util.List;", "import java.util.List;\nimport java.util.Optional;")
    svc = svc.replace("public Order find(String id) {\n        return repo.load(id);",
                      "public Optional<Order> find(String id) {\n"
                      "        return Optional.ofNullable(repo.load(id));")
    write(repo, f"{P}/OrderService.java", svc)
    write(repo, f"{P}/RetryPolicy.java", """package com.shop;

public class RetryPolicy {
    private Repo repo;

    public void retryOrder(Order o, int n) {
        for (int i = 0; i < n; i++) {
            repo.save(i);
        }
    }
}
""")
    commit(repo, "move retry", "2024-03-01T00:00:00Z")
    (repo / P / "io").mkdir()
    git(repo, "mv", f"{P}/Writer.java", f"{P}/io/Writer.java")
    write(repo, f"{P}/io/Writer.java", WRITER.replace("package com.shop;", "package com.shop.io;"))
    target_pre = commit(repo, "move writer", "2024-04-01T00:00:00Z")

    # Source: the bug fix.
    git(repo, "checkout", "-q", "main")
    write(repo, "README.md", "source\n")
    source_pre = commit(repo, "source work", "2024-05-01T00:00:00Z")
    fixed = (ORDER_SERVICE
             .replace("        repo.save(timeout);\n",
                      "        if (timeout > 0) {\n            repo.save(timeout);\n        }\n")
             .replace("        return repo.load(id);\n",
                      "        audit(id);\n        return repo.load(id);\n")
             .replace("            repo.save(i);\n        }\n    }\n",
                      "            repo.save(i);\n            backoff(i);\n        }\n    }\n\n"
                      "    private void backoff(int i) {\n        repo.save(-i);\n    }\n")
             .replace("    private int retries;\n", "    private int retries = 3;\n"))
    write(repo, f"{P}/OrderService.java", fixed)
    write(repo, f"{P}/Writer.java", WRITER.replace("System.out.println(s);",
                                                   "System.out.println(s.trim());"))
    write(repo, f"{P}/BaseService.java", BASE.replace("public void start() {\n",
                                                       "public void start() {\n        init();\n"))
    source_post = commit(repo, "the fix", "2024-05-02T00:00:00Z")

    spec = {
        "rename flush": [{
            "type": "Rename Method",
            "description": "Rename Method public flush(timeout int) : void renamed to "
                           "public flushAll(timeout int) : void in class com.shop.OrderService",
            "leftSideLocations": [_loc(f"{P}/OrderService.java", "METHOD_DECLARATION",
                                       "public flush(timeout int) : void")],
            "rightSideLocations": [_loc(f"{P}/OrderService.java", "METHOD_DECLARATION",
                                        "public flushAll(timeout int) : void")],
        }],
        "move retry": [{
            "type": "Move Method",
            "description": "Move Method public retryOrder(o Order, n int) : void from class "
                           "com.shop.OrderService to public retryOrder(o Order, n int) : void "
                           "from class com.shop.RetryPolicy",
            "leftSideLocations": [_loc(f"{P}/OrderService.java", "METHOD_DECLARATION",
                                       "public retryOrder(o Order, n int) : void")],
            "rightSideLocations": [_loc(f"{P}/RetryPolicy.java", "METHOD_DECLARATION",
                                        "public retryOrder(o Order, n int) : void")],
        }],
        "move writer": [{
            "type": "Move Class",
            "description": "Move Class com.shop.Writer moved to com.shop.io.Writer",
            "leftSideLocations": [_loc(f"{P}/Writer.java", "TYPE_DECLARATION", "com.shop.Writer")],
            "rightSideLocations": [_loc(f"{P}/io/Writer.java", "TYPE_DECLARATION",
                                        "com.shop.io.Writer")],
        }],
    }
    rm_spec = root / "rm_spec.json"
    rm_spec.write_text(json.dumps(spec), encoding="utf-8")
    return Scenario(repo, ancestor, source_pre, source_post, target_pre, rm_spec)


def manifest(sc: Scenario, path: Path, **overrides: str) -> Path:
    row = {
        "case_id": "C01", "mo_id": "shop/1_MO", "variant_pair": "shop--shopfork",
        "direction": "shop -> shopfork", "source_repo": "x", "target_repo": "y",
        "pr_number": "1", "source_pre_sha": sc.source_pre, "source_post_sha": sc.source_post,
        "target_pre_sha": sc.target_pre, "repatch_target_sha": sc.target_pre,
        "ancestor_sha": "", "mo_files": f"{P}/OrderService.java;{P}/Writer.java",
        "non_mo_files": f"{P}/BaseService.java", "eligibility": "eligible",
        "exclusion_reason": "",
    }
    row.update(overrides)
    import csv
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    return path
