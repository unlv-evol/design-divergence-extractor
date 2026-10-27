"""Stage 1: resolve and verify the pinned revisions (spec Section 5)."""

from __future__ import annotations

from dde.git import GitError, Repo
from dde.models import Case, Flag, ResolvedRevisions


def verify_revisions(case: Case, repo: Repo) -> tuple[ResolvedRevisions | None, list[Flag]]:
    flags: list[Flag] = []

    def stop(flag: str, detail: str = "") -> tuple[None, list[Flag]]:
        flags.append(Flag(case.case_id, "revisions", flag, detail, stops_case=True))
        return None, flags

    if not repo.exists():
        return stop("repository_missing", str(repo.path))
    resolved: dict[str, str] = {}
    for role in ("source_pre_sha", "source_post_sha", "target_pre_sha"):
        sha = getattr(case, role)
        if not sha:
            return stop("sha_missing", role)
        if not repo.has_commit(sha):
            return stop("sha_not_in_repository", f"{role}={sha}")
        resolved[role] = repo.resolve(sha)

    # R2: the target must be the revision RePatch integrated onto.
    if case.repatch_target_sha:
        rp = case.repatch_target_sha
        rp_full = repo.resolve(rp) if repo.has_commit(rp) else rp
        if rp_full != resolved["target_pre_sha"]:
            return stop(
                "target_sha_differs_from_repatch",
                f"manifest {resolved['target_pre_sha']} vs RePatch {rp}",
            )
    else:
        flags.append(Flag(case.case_id, "revisions", "no_repatch_target_sha",
                          "case has no RePatch run; characterization only"))

    if not repo.is_ancestor(resolved["source_pre_sha"], resolved["source_post_sha"]):
        flags.append(Flag(case.case_id, "revisions", "source_pre_not_ancestor_of_post"))

    try:
        base = repo.merge_base(resolved["source_pre_sha"], resolved["target_pre_sha"])
    except GitError:
        base = None
    from_metadata = False
    if base is None:
        if case.ancestor_sha and repo.has_commit(case.ancestor_sha):
            base = repo.resolve(case.ancestor_sha)
            from_metadata = True
            flags.append(Flag(case.case_id, "revisions", "ancestor_from_metadata", base))
        else:
            return stop("ancestor_unknown", "no merge-base and no usable ancestor_sha")
    elif case.ancestor_sha and repo.has_commit(case.ancestor_sha):
        given = repo.resolve(case.ancestor_sha)
        if given != base:
            flags.append(Flag(case.case_id, "revisions", "ancestor_differs_from_manifest",
                              f"merge-base {base} vs manifest {given}; merge-base used"))
    return (
        ResolvedRevisions(
            source_pre=resolved["source_pre_sha"],
            source_post=resolved["source_post_sha"],
            target_pre=resolved["target_pre_sha"],
            ancestor=base,
            ancestor_from_metadata=from_metadata,
        ),
        flags,
    )
