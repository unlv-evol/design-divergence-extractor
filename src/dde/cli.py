"""Command line (spec Section 11)."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from dde import __version__
from dde.manifest import ManifestError, load_manifest, select_cases, sha256_file
from dde.models import Case

log = logging.getLogger("dde")


def _add_case_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, required=True, help="cases.csv")
    p.add_argument("--repos", type=Path, required=True, help="directory of per-pair repositories")
    p.add_argument("--cases", default="", help="comma-separated case ids (default: all eligible)")
    p.add_argument("--include-ineligible", action="store_true",
                   help="also run cases whose eligibility is not 'eligible'")


def _add_rm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rm", type=Path, default=os.environ.get("DDE_REFACTORINGMINER"),
                   help="RefactoringMiner launcher (default: $DDE_REFACTORINGMINER)")
    p.add_argument("--rm-cache", type=Path, default=Path("cache/refactoringminer"))
    p.add_argument("--rm-timeout", type=int, default=3600, help="seconds per RefactoringMiner run")
    p.add_argument("--no-refactorings", action="store_true",
                   help="identity rename maps instead of RefactoringMiner (flagged on every case)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dde", description="Design-Divergence Extractor")
    ap.add_argument("--version", action="version", version=f"dde {__version__}")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup-repos", help="create per-pair working repositories (network)")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--repos", type=Path, required=True)

    p = sub.add_parser("verify", help="stage 1 only: check revisions and patch files")
    _add_case_args(p)
    p.add_argument("--out", type=Path, default=Path("out"))

    p = sub.add_parser("refactorings", help="run RefactoringMiner ahead of time and cache it")
    _add_case_args(p)
    _add_rm_args(p)

    p = sub.add_parser("extract", help="all stages; indicator tables and audit trail")
    _add_case_args(p)
    _add_rm_args(p)
    p.add_argument("--out", type=Path, default=Path("out"))

    p = sub.add_parser("packets", help="blind pass-1 coder packets (stages 1-6)")
    _add_case_args(p)
    _add_rm_args(p)
    p.add_argument("--out", type=Path, default=Path("packets"))

    p = sub.add_parser("validate", help="indicators against frozen pass-1 codes")
    p.add_argument("--indicators", type=Path, required=True)
    p.add_argument("--codes", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--coder", default="consensus",
                   help="coder whose labels to use, if codes has a coder column")
    p.add_argument("--map", type=Path, default=None, help="JSON indicator -> codes map")

    p = sub.add_parser("gacpd-manifest", help="draft cases.csv from a GACPD run (Stage A helper)")
    p.add_argument("--gacpd-run", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("cases.draft.csv"))
    return ap


def _cases(args: argparse.Namespace) -> list[Case]:
    only = [c.strip() for c in args.cases.split(",") if c.strip()]
    return select_cases(load_manifest(args.manifest), only or None, args.include_ineligible)


def _settings(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from dde.pipeline import Settings

    return Settings(
        repos_dir=args.repos,
        rm_executable=Path(args.rm) if args.rm else None,
        rm_cache=args.rm_cache,
        rm_timeout=args.rm_timeout,
        use_refactorings=not args.no_refactorings,
    )


def _git_version() -> str:
    try:
        out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        return out.stdout.strip().removeprefix("git version ")
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    try:
        return _dispatch(args)
    except ManifestError as exc:
        log.error("%s", exc)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command
    if cmd == "gacpd-manifest":
        from dde.gacpd import draft_manifest

        n, problems = draft_manifest(args.gacpd_run, args.out)
        for p in problems:
            log.warning("%s", p)
        log.info("wrote %d draft cases to %s", n, args.out)
        return 0

    if cmd == "validate":
        from dde.validate import validate

        args.out.mkdir(parents=True, exist_ok=True)
        for path in validate(args.indicators, args.codes, args.out, args.coder, args.map):
            log.info("wrote %s", path)
        return 0

    if cmd == "setup-repos":
        from dde.git import GitError, setup_pair_repo

        pairs = {(c.variant_pair, c.source_repo, c.target_repo) for c in load_manifest(args.manifest)}
        failed = 0
        for pair, src, tgt in sorted(pairs):
            log.info("setting up %s", pair)
            try:
                setup_pair_repo(args.repos / pair, src, tgt)
            except GitError as exc:
                failed += 1
                log.error("%s: %s", pair, exc)
        return 1 if failed else 0

    cases = _cases(args)
    log.info("%d case(s) selected", len(cases))

    if cmd == "verify":
        from dde.outputs import BASE_COLUMNS, status_rows, write_csv
        from dde.pipeline import Settings, verify_case

        settings = Settings(args.repos, None, Path("."))
        results = [verify_case(c, settings, check_fix=True) for c in cases]
        path = args.out / "case_status.csv"
        write_csv(path, status_rows(results), BASE_COLUMNS["case_status.csv"])
        stopped = sum(r.stopped for r in results)
        log.info("%d ok, %d stopped; see %s", len(results) - stopped, stopped, path)
        return 0

    if cmd == "refactorings":
        return _prefetch(args, cases)

    from dde.outputs import write_extract, write_run_manifest
    from dde.packets import write_packets
    from dde.pipeline import run_case
    from dde.refminer import version_of

    settings = _settings(args)
    if settings.use_refactorings and settings.rm_executable is None:
        log.error("RefactoringMiner not configured: pass --rm, set DDE_REFACTORINGMINER, "
                  "or use --no-refactorings")
        return 2
    results = []
    for c in cases:
        log.info("case %s", c.case_id)
        results.append(run_case(c, settings, with_indicators=(cmd == "extract")))
    args.out.mkdir(parents=True, exist_ok=True)
    written = write_extract(args.out, results) if cmd == "extract" else write_packets(args.out, results)
    write_run_manifest(
        args.out, cmd, args.manifest, sha256_file(args.manifest), results,
        {"use_refactorings": settings.use_refactorings, "rm_timeout": settings.rm_timeout,
         "cases": [c.case_id for c in cases]},
        version_of(settings.rm_executable), _git_version(),
    )
    stopped = sum(r.stopped for r in results)
    log.info("wrote %d file(s) to %s; %d case(s) stopped (see case_status.csv)",
             len(written), args.out, stopped)
    return 0


def _prefetch(args: argparse.Namespace, cases: list[Case]) -> int:
    """Fill the RefactoringMiner cache, widest ranges first so later ones are reused."""
    from dde.git import Repo
    from dde.pipeline import verify_case
    from dde.refminer import RefactoringMinerError, run_range

    settings = _settings(args)
    if settings.rm_executable is None:
        log.error("RefactoringMiner not configured")
        return 2
    jobs: dict[tuple[str, str, str], int] = {}
    for c in cases:
        res = verify_case(c, settings)
        if res.revisions is None:
            log.warning("%s: revisions not verified; skipped", c.case_id)
            continue
        repo = Repo(settings.repos_dir / c.variant_pair)
        for end in (res.revisions.source_pre, res.revisions.target_pre):
            jobs[(c.variant_pair, res.revisions.ancestor, end)] = repo.commit_time(end)
    failed = 0
    for (pair, start, end), _t in sorted(jobs.items(), key=lambda kv: (kv[0][0], -kv[1])):
        log.info("%s: %s..%s", pair, start[:10], end[:10])
        try:
            run_range(settings.rm_executable, Repo(settings.repos_dir / pair), start, end,
                      settings.rm_cache / pair, settings.rm_timeout)
        except RefactoringMinerError as exc:
            failed += 1
            log.error("%s %s..%s: %s", pair, start[:10], end[:10], exc)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
