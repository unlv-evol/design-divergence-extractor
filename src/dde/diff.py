"""Stage 2: parse the source patch into files and hunks."""

from __future__ import annotations

import re

from dde.models import DiffLine, FilePatch, Hunk

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _strip_prefix(path: str) -> str | None:
    if path == "/dev/null":
        return None
    path = path.split("\t", 1)[0]
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> list[FilePatch]:
    """Parse ``git diff`` output (no renames, no colour) into file patches."""
    files: list[FilePatch] = []
    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = []
    cur: dict[str, object] | None = None
    cur_lines: list[DiffLine] = []
    old_no = new_no = 0
    in_file = False

    def close_hunk() -> None:
        nonlocal cur, cur_lines
        if cur is not None:
            hunks.append(
                Hunk(
                    index=len(hunks) + 1,
                    old_start=int(cur["old_start"]),  # type: ignore[call-overload]
                    old_count=int(cur["old_count"]),  # type: ignore[call-overload]
                    new_start=int(cur["new_start"]),  # type: ignore[call-overload]
                    new_count=int(cur["new_count"]),  # type: ignore[call-overload]
                    section=str(cur["section"]),
                    lines=tuple(cur_lines),
                )
            )
        cur, cur_lines = None, []

    def close_file() -> None:
        nonlocal hunks, in_file
        close_hunk()
        if in_file and (old_path or new_path):
            files.append(FilePatch(old_path=old_path, new_path=new_path, hunks=tuple(hunks)))
        hunks = []
        in_file = False

    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            close_file()
            in_file = True
            old_path = new_path = None
            continue
        if not in_file:
            continue
        if cur is None and raw.startswith("--- "):
            old_path = _strip_prefix(raw[4:])
            continue
        if cur is None and raw.startswith("+++ "):
            new_path = _strip_prefix(raw[4:])
            continue
        m = _HUNK_HEADER.match(raw)
        if m:
            close_hunk()
            cur = {
                "old_start": m.group(1),
                "old_count": m.group(2) if m.group(2) is not None else 1,
                "new_start": m.group(3),
                "new_count": m.group(4) if m.group(4) is not None else 1,
                "section": m.group(5).strip(),
            }
            old_no, new_no = int(m.group(1)), int(m.group(3))
            continue
        if cur is None:
            continue  # extended headers: index, mode, binary notices
        if raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        if not raw:
            continue  # only the final newline produces an empty line
        tag, body = raw[:1], raw[1:]
        if tag == "+":
            cur_lines.append(DiffLine("+", body, None, new_no))
            new_no += 1
        elif tag == "-":
            cur_lines.append(DiffLine("-", body, old_no, None))
            old_no += 1
        elif tag == " ":
            cur_lines.append(DiffLine(" ", body, old_no, new_no))
            old_no += 1
            new_no += 1
        else:
            close_hunk()
    close_file()
    return files
