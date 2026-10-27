#!/usr/bin/env python3
"""Stand-in for RefactoringMiner's ``-bc`` command, for tests.

Emits RefactoringMiner 3.x JSON for ``start..end``. Refactorings come from the
JSON file named by ``FAKE_RM_SPEC``: {commit subject: [refactoring, ...]}, each
refactoring in RefactoringMiner's own format. Commits are listed oldest first,
as the real tool does.
"""

import json
import os
import subprocess
import sys


def main() -> int:
    args = sys.argv[1:]
    if os.environ.get("FAKE_RM_FAIL"):
        print("simulated failure", file=sys.stderr)
        return 3
    if len(args) != 6 or args[0] != "-bc" or args[4] != "-json":
        print(f"unexpected arguments: {args}", file=sys.stderr)
        return 2
    _, repo, start, end, _, out = args
    with open(os.environ["FAKE_RM_SPEC"], encoding="utf-8") as fh:
        spec = json.load(fh)
    log = subprocess.run(
        ["git", "-C", repo, "log", "--reverse", "--format=%H %s", f"{start}..{end}"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    commits = []
    for line in log:
        sha, subject = line.split(" ", 1)
        commits.append({"repository": "", "sha1": sha, "url": "",
                        "refactorings": spec.get(subject, [])})
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"commits": commits}, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
