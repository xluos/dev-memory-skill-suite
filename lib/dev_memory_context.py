#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_memory_common import (
    collect_git_facts,
    ensure_branch_paths_exist,
    get_setup_completed,
    list_missing_docs,
    merged_focus_areas,
    read_json,
    sync_progress,
    write_json,
)


def command_show(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    payload = {
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "branch_key": branch_key,
        "storage_root": str(storage_root),
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch_dir),
        "setup_completed": get_setup_completed(paths["manifest"]),
        "files": {key: str(value) for key, value in paths.items()},
        "missing_or_placeholder": list_missing_docs(paths),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_sync(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    if branch_name is None:
        # no-git mode — no git facts to derive.
        print(json.dumps({"mode": "no-git", "skipped": True}, ensure_ascii=False))
        return

    prior_manifest = read_json(paths["manifest"]) or {}
    facts = collect_git_facts(repo_root, branch_name, storage_root)
    all_paths = sorted(set(
        facts["working_tree_files"]
        + facts["staged_files"]
        + facts["untracked_files"]
        + facts["recent_commit_files"]
    ))
    facts["focus_areas"] = merged_focus_areas(all_paths, prior_manifest.get("focus_areas") or [])
    sync_progress(paths, facts)

    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": facts["updated_at"],
            "last_seen_head": facts["last_seen_head"],
            "default_base": facts["default_base"],
            "scope_summary": facts["scope_summary"],
            "focus_areas": facts["focus_areas"],
        }
    )
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": facts["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": facts["last_seen_head"],
            "default_base": facts["default_base"],
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    payload = {
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "storage_root": str(storage_root),
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch_dir),
        "missing_or_placeholder": list_missing_docs(paths),
        "focus_areas": facts["focus_areas"],
        "files_considered": len(all_paths),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_injection_preview(args):
    """Render the SessionStart injection context for a given repo_key + branch,
    without needing an actual git working tree. Used by the UI panel."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "hooks"))
    from _common import _build_context_from_assets  # noqa: E402
    from dev_memory_common import asset_paths as _asset_paths  # noqa: E402

    storage_root = Path(args.context_dir) if args.context_dir else Path.home() / ".dev-memory" / "repos"
    repo_dir = storage_root / args.repo_key
    branch_dir = repo_dir / "branches" / args.branch.replace("/", "__")

    if not branch_dir.exists():
        print(json.dumps({"error": f"branch dir not found: {branch_dir}"}, ensure_ascii=False))
        return 1

    paths = _asset_paths(repo_dir, branch_dir)
    assets = {
        "repo_dir": repo_dir,
        "branch_dir": branch_dir,
        "branch_name": args.branch,
        "paths": paths,
    }
    context = _build_context_from_assets(assets, full=True)
    print(json.dumps({"context": context or ""}, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Read or refresh repo+branch development assets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("show", "sync"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=".", help="Path inside the target Git repository")
        sub.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-memory/repos")
        sub.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")

    preview_sub = subparsers.add_parser("injection-preview")
    preview_sub.add_argument("--repo-key", required=True, help="Repo key (directory name under storage root)")
    preview_sub.add_argument("--branch", required=True, help="Branch name (original, with slashes)")
    preview_sub.add_argument("--context-dir", help="Storage root. Defaults to ~/.dev-memory/repos")

    args = parser.parse_args()
    try:
        if args.command == "show":
            command_show(args)
        elif args.command == "sync":
            command_sync(args)
        elif args.command == "injection-preview":
            return command_injection_preview(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
