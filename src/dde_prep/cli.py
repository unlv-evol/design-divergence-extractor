"""``dde-prep``: data preparation for the study (workplan Stage A) and the coding pilot.

Typical Stage A order::

    dde-prep draft --gacpd-run GACPD/ --out cases.csv
    dde-prep setup-repos --cases cases.csv --repos repos/
    dde-prep apply-shas --cases cases.csv --runs restricted/outcomes.csv --repos repos/
    dde-prep reverify --cases cases.csv --repos repos/ --out verify/ [--apply]
    dde-prep check --cases cases.csv
    dde-prep checkpoint1 --cases cases.csv --runs restricted/outcomes.csv --status verify/case_status.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dde.manifest import ManifestError
from dde_prep import __version__
from dde_prep.runs import RunsError

log = logging.getLogger("dde-prep")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dde-prep", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--version", action="version", version=f"dde-prep {__version__}")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("draft", help="draft cases.csv from a GACPD run, duplicates marked")
    p.add_argument("--gacpd-run", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("cases.csv"))

    p = sub.add_parser("setup-repos", help="one working repository per variant pair (network)")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--repos", type=Path, required=True)

    p = sub.add_parser("apply-shas", help="fill revisions from the RePatch runs table")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--runs", type=Path, required=True, help="restricted/outcomes.csv")
    p.add_argument("--repos", type=Path, help="per-pair repositories, to expand SHAs and find parents")
    p.add_argument("--out", type=Path, help="default: overwrite --cases")

    p = sub.add_parser("reverify", help="dde's revision check plus MO re-verification at the target")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--repos", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("verify"))
    p.add_argument(
        "--apply",
        action="store_true",
        help="write exclusions that need no judgement (missing commits) back to --cases",
    )

    p = sub.add_parser("check", help="is cases.csv consistent and ready for dde?")
    p.add_argument("--cases", type=Path, required=True)

    p = sub.add_parser("checkpoint1", help="Checkpoint 1 report and the proposed pilot sample")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--status", type=Path, help="case_status.csv from reverify")
    p.add_argument("--out", type=Path, default=Path("checkpoint1"))
    p.add_argument("--restricted", type=Path, default=Path("restricted"))
    p.add_argument("--seed", type=int, default=20261028)
    p.add_argument("--pilot-size", type=int, default=15)

    for name, helptext in (
        ("pilot-sheets", "coding workbooks for the pilot (see --help)"),
        ("pilot-agreement", "kappa, Checkpoint 2, disagreements, codes for dde validate"),
    ):
        sub.add_parser(name, help=helptext)  # dispatched before parsing, see main()
    return ap


_PASSTHROUGH = {"pilot-sheets": "dde_prep.pilot.sheets", "pilot-agreement": "dde_prep.pilot.agreement"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _PASSTHROUGH:  # these keep their own argument parsers
        import importlib

        module = importlib.import_module(_PASSTHROUGH[argv[0]])
        return int(module.main(argv[1:]) or 0)
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    try:
        return _dispatch(args)
    except (ManifestError, RunsError) as exc:
        log.error("%s", exc)
        return 2


def _report(problems: list[str], ok: str) -> int:
    for p in problems:
        log.warning("%s", p)
    log.info("%s; %d problem(s)", ok, len(problems))
    return 1 if problems else 0


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "draft":
        from dde_prep.gacpd import draft_manifest

        n, problems = draft_manifest(args.gacpd_run, args.out)
        return _report(problems, f"wrote {n} draft cases to {args.out}")
    if cmd == "setup-repos":
        from dde.git import GitError
        from dde.manifest import load_manifest
        from dde_prep.repos import setup_pair_repo

        pairs = {(c.variant_pair, c.source_repo, c.target_repo) for c in load_manifest(args.cases)}
        failed = []
        for pair, src, tgt in sorted(pairs):
            log.info("setting up %s", pair)
            try:
                setup_pair_repo(args.repos / pair, src, tgt)
            except GitError as exc:
                failed.append(f"{pair}: {exc}")
        return _report(failed, f"{len(pairs)} pair(s)")
    if cmd == "apply-shas":
        from dde_prep.shas import apply_shas

        out = args.out or args.cases
        return _report(apply_shas(args.cases, args.runs, args.repos, out), f"wrote {out}")
    if cmd == "reverify":
        from dde_prep.reverify import reverify

        results = reverify(args.cases, args.repos, args.out, args.apply)
        stopped = sum(r.stopped for r in results)
        log.info("%d case(s), %d stopped; flags in %s", len(results), stopped, args.out / "case_status.csv")
        return 0
    if cmd == "check":
        from dde_prep.check import check_manifest

        return _report(check_manifest(args.cases), f"checked {args.cases}")
    if cmd == "checkpoint1":
        from dde_prep.checkpoint import checkpoint1

        passed, problems = checkpoint1(
            args.cases, args.runs, args.status, args.out, args.restricted, args.seed, args.pilot_size
        )
        return _report(
            problems,
            f"Checkpoint 1: {'PASS' if passed else 'FAIL'}; report in {args.out / 'checkpoint1_report.md'}",
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
