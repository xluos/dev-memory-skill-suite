#!/usr/bin/env python3

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_memory_common import (  # noqa: E402
    DEV_MEMORY_ID_FILE,
    LEGACY_ID_FILE,
    MANAGED_FILES,
    asset_paths,
    detect_no_git_mode,
    get_branch_paths,
    read_json,
)


BRANCH_FILE_KEYS = (
    "overview",
    "progress",
    "decisions",
    "risks",
    "glossary",
    "unsorted",
    "pending_promotion",
    "log",
    "manifest",
)

REPO_FILE_KEYS = (
    "repo_overview",
    "repo_decisions",
    "repo_glossary",
    "repo_log",
    "repo_manifest",
)

READ_ORDER = (
    "glossary",
    "decisions",
    "risks",
    "overview",
    "repo_decisions",
    "repo_glossary",
    "repo_overview",
    "unsorted",
    "pending_promotion",
    "log",
)


def _resolve_paths(repo, context_dir=None, branch=None):
    repo_path = Path(repo).expanduser().resolve()
    if detect_no_git_mode(repo_path) and not (
        (repo_path / DEV_MEMORY_ID_FILE).exists()
        or (repo_path / LEGACY_ID_FILE).exists()
    ):
        raise RuntimeError(
            "no-git memory is not initialized; read refuses to create a .dev-memory-id. "
            "Initialize by writing via capture/setup first, or pass a Git repo path."
        )
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        repo, context_dir, branch
    )
    paths = asset_paths(repo_dir, branch_dir)
    return {
        "repo_root": repo_root,
        "branch_name": branch_name,
        "branch_key": branch_key,
        "storage_root": storage_root,
        "repo_key": repo_key,
        "repo_dir": repo_dir,
        "branch_dir": branch_dir,
        "paths": paths,
    }


def _existing_branch_dirs(repo_dir):
    branches_dir = repo_dir / "branches"
    if not branches_dir.exists():
        return []
    result = []
    for path in sorted(branches_dir.iterdir(), key=lambda p: p.name):
        if not path.is_dir() or path.name == "_archived":
            continue
        manifest = read_json(path / "manifest.json")
        result.append(
            {
                "branch_key": path.name,
                "branch": manifest.get("branch") or path.name.replace("__", "/"),
                "path": str(path),
            }
        )
    return result


def _existing_files(paths, keys):
    files = {}
    for key in keys:
        path = paths[key]
        files[key] = {
            "path": str(path),
            "exists": path.exists(),
        }
    return files


def _memory_files_for_dir(memory_dir):
    for file_name in MANAGED_FILES:
        path = memory_dir / file_name
        if path.exists() and path.is_file():
            yield path


def _dedupe_paths(paths):
    seen = set()
    result = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _scope_files(resolved, scope):
    paths = resolved["paths"]
    repo_dir = resolved["repo_dir"]
    branch_dir = resolved["branch_dir"]
    files = []

    if scope in ("branch", "current"):
        files.extend(paths[key] for key in BRANCH_FILE_KEYS)
    if scope in ("repo", "current"):
        files.extend(paths[key] for key in REPO_FILE_KEYS)
    if scope == "all-branches":
        files.extend(paths[key] for key in REPO_FILE_KEYS)
        branches_dir = repo_dir / "branches"
        if branches_dir.exists():
            for child in sorted(branches_dir.iterdir(), key=lambda p: p.name):
                if child.is_dir() and child.name != "_archived":
                    files.extend(_memory_files_for_dir(child))
    if scope == "archived":
        archived_dir = repo_dir / "branches" / "_archived"
        if archived_dir.exists():
            for child in sorted(archived_dir.iterdir(), key=lambda p: p.name):
                if child.is_dir():
                    files.extend(_memory_files_for_dir(child))
    if scope == "all":
        files.extend(paths[key] for key in REPO_FILE_KEYS)
        branches_dir = repo_dir / "branches"
        if branches_dir.exists():
            for child in sorted(branches_dir.rglob("*"), key=lambda p: p.as_posix()):
                if child.is_dir() and child.name != "_archived":
                    files.extend(_memory_files_for_dir(child))

    return [p for p in _dedupe_paths(files) if p.exists() and p.is_file()]


def _make_matchers(queries, *, regex=False, case_sensitive=False):
    if not queries:
        raise ValueError("pass at least one --query")
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        return [(q, re.compile(q, flags)) for q in queries]
    if case_sensitive:
        return [(q, q) for q in queries]
    return [(q, q.lower()) for q in queries]


def _line_matches(line, matchers, *, regex=False, case_sensitive=False):
    haystack = line if case_sensitive else line.lower()
    matched = []
    for raw, matcher in matchers:
        if regex:
            if matcher.search(line):
                matched.append(raw)
        elif matcher in haystack:
            matched.append(raw)
    return matched


def _context_lines(lines, line_no, radius):
    if radius <= 0:
        return []
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    result = []
    for current in range(start, end + 1):
        if current == line_no:
            continue
        result.append({"line": current, "text": lines[current - 1]})
    return result


def command_show(args):
    resolved = _resolve_paths(args.repo, args.context_dir, args.branch)
    paths = resolved["paths"]
    payload = {
        "repo_root": str(resolved["repo_root"]),
        "repo_key": resolved["repo_key"],
        "branch": resolved["branch_name"],
        "branch_key": resolved["branch_key"],
        "storage_root": str(resolved["storage_root"]),
        "repo_dir": str(resolved["repo_dir"]),
        "branch_dir": str(resolved["branch_dir"]),
        "repo_exists": resolved["repo_dir"].exists(),
        "branch_exists": resolved["branch_dir"].exists(),
        "recommended_read_order": [
            {"key": key, "path": str(paths[key]), "exists": paths[key].exists()}
            for key in READ_ORDER
            if key in paths
        ],
        "branch_files": _existing_files(paths, BRANCH_FILE_KEYS),
        "repo_files": _existing_files(paths, REPO_FILE_KEYS),
        "existing_branches": _existing_branch_dirs(resolved["repo_dir"]),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_search(args):
    resolved = _resolve_paths(args.repo, args.context_dir, args.branch)
    matchers = _make_matchers(args.query, regex=args.regex, case_sensitive=args.case_sensitive)
    files = _scope_files(resolved, args.scope)
    hits = []

    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(lines, start=1):
            matched_queries = _line_matches(
                line,
                matchers,
                regex=args.regex,
                case_sensitive=args.case_sensitive,
            )
            if not matched_queries:
                continue
            hits.append(
                {
                    "path": str(path),
                    "line": idx,
                    "text": line,
                    "matched_queries": matched_queries,
                    "context": _context_lines(lines, idx, args.context_lines),
                }
            )
            if len(hits) >= args.max_hits:
                break
        if len(hits) >= args.max_hits:
            break

    payload = {
        "repo_root": str(resolved["repo_root"]),
        "repo_key": resolved["repo_key"],
        "branch": resolved["branch_name"],
        "branch_key": resolved["branch_key"],
        "repo_dir": str(resolved["repo_dir"]),
        "branch_dir": str(resolved["branch_dir"]),
        "scope": args.scope,
        "queries": args.query,
        "regex": args.regex,
        "searched_files": [str(p) for p in files],
        "hit_count": len(hits),
        "hits": hits,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Locate and search dev-memory files for the current repo/branch without scanning global memory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="Show authoritative memory paths for a repo/branch.")
    show.add_argument("--repo", default=".", help="Path inside the target Git repository")
    show.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-memory/repos")
    show.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")

    search = subparsers.add_parser("search", help="Search memory files under the resolved repo memory directory.")
    search.add_argument("--repo", default=".", help="Path inside the target Git repository")
    search.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-memory/repos")
    search.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")
    search.add_argument(
        "--scope",
        choices=("current", "branch", "repo", "all-branches", "archived", "all"),
        default="current",
        help="Which memory files to search. Defaults to current branch + repo shared layer.",
    )
    search.add_argument("--query", action="append", required=True, help="Literal query. Repeat for OR matching.")
    search.add_argument("--regex", action="store_true", help="Treat --query values as Python regex patterns.")
    search.add_argument("--case-sensitive", action="store_true")
    search.add_argument("--context-lines", type=int, default=1, help="Neighboring lines to include around each hit.")
    search.add_argument("--max-hits", type=int, default=80)

    args = parser.parse_args()
    try:
        if args.command == "show":
            command_show(args)
        else:
            command_search(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
