"""Blind pass-1 coder packets (spec Section 9, Outputs).

A packet shows pre-integration evidence only: the source method before and
after the patch, the target counterpart region, class summaries on both
sides, and refactorings since the ancestor. It never shows indicator values,
match labels, outcome labels, or conflict output.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from dde.pipeline import CaseResult


def _fence(text: str | None, lang: str = "java") -> str:
    if text is None:
        return "_(none)_\n"
    return f"```{lang}\n{textwrap.dedent(text).rstrip()}\n```\n"


def render_packet(res: CaseResult) -> str:
    case, rev = res.case, res.revisions
    assert rev is not None
    out = [
        f"# Case {case.case_id}\n",
        f"- Variant pair: `{case.variant_pair}` ({case.direction})",
        f"- Source before patch: `{rev.source_pre}`",
        f"- Source after patch: `{rev.source_post}`",
        f"- Target (before integration): `{rev.target_pre}`",
        f"- Shared ancestor: `{rev.ancestor}`\n",
        "Code what you see. Record design codes, the role stereotype of each edited class on "
        "each side, and whether a divergence plausibly affects this patch or merely exists "
        "nearby.\n",
    ]
    for fp in res.packet_files:
        out.append(f"## File `{fp.source_path}`\n")
        out.append(f"Target counterpart: `{fp.target_path}`\n" if fp.target_path
                   else "Target counterpart: none located\n")
        out.append("### Patch\n")
        out.append(_fence(fp.diff_text, "diff"))
        for cp in fp.classes:
            out.append(f"### Class `{cp.local_name}`\n")
            out.append("Source (before patch):\n")
            out.append(_fence(cp.source_summary, "text"))
            out.append("Target:\n")
            out.append(_fence(cp.target_summary, "text") if cp.target_summary else f"{cp.target_note}\n")
        for mp in fp.methods:
            if mp.kind == "outside_method":
                out.append("### Edits outside any method\n")
                out.append("See the patch above (fields, initializers, imports, or declarations).\n")
                continue
            out.append(f"### Method `{mp.label}`\n")
            if mp.target_note:
                out.append(f"_{mp.target_note}_\n")
            out.append("Source, before the patch:\n")
            out.append(_fence(mp.pre_text))
            out.append("Source, after the patch:\n")
            out.append(_fence(mp.post_text))
            out.append("Target counterpart region:\n")
            out.append(_fence(mp.target_text))
    out.append("## Refactorings since the ancestor touching these files\n")
    if res.packet_refactorings:
        out.extend(f"- {line}" for line in res.packet_refactorings)
        out.append("")
    else:
        out.append("_None recorded._\n")
    return "\n".join(out)


def write_packets(out: Path, results: list[CaseResult]) -> list[Path]:
    written = []
    index = ["case_id,packet"]
    for res in results:
        if res.stopped or res.revisions is None:
            continue
        path = out / res.case.case_id / "packet.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_packet(res), encoding="utf-8")
        written.append(path)
        index.append(f"{res.case.case_id},{res.case.case_id}/packet.md")
    (out / "index.csv").write_text("\n".join(index) + "\n", encoding="utf-8")
    return written
