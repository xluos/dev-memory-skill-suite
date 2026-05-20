#!/usr/bin/env python3
"""
dev-memory-branch: atomic ops for managing branch-scoped memory directories.

Subcommands (all flag-driven, JSON output, no interactivity):
  list        : enumerate candidate branches (git ∪ memory dirs) with metadata
  inspect     : detect skeleton/used state of a single branch's memory dir
  rename      : move <source> → <target> (source dir disappears)
  fork        : copy <source> → <target> (source dir kept; provenance recorded)
  delete      : remove a branch's memory dir
  init        : reset a branch's memory dir to a fresh template skeleton

Conflict handling (rename / fork / delete / init on used target):
  default     : abort
  --force     : delete target before the op
  --backup    : move target to branches/_archived/<branch>-<UTC>/ first
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_memory_common import (
    AUTO_END,
    AUTO_START,
    PLACEHOLDER_MARKERS,
    detect_branch,
    detect_repo_identity,
    detect_repo_root,
    detect_worktree_base_branch,
    ensure_branch_paths_exist,
    get_storage_root,
    git_lines,
    is_worktree,
    now_iso,
    read_json,
    sanitize_branch_name,
    template_decisions,
    template_glossary,
    template_overview,
    template_pending_promotion,
    template_progress,
    template_risks,
    template_unsorted,
    write_json,
)


# ---------------------------------------------------------------------------
# Template fingerprints
# ---------------------------------------------------------------------------

def _template_for(name, branch_name):
    """Return the canonical template content for a managed file."""
    if name == "overview.md":
        return template_overview(branch_name)
    if name == "decisions.md":
        return template_decisions(branch_name)
    if name == "progress.md":
        return template_progress(branch_name)
    if name == "risks.md":
        return template_risks(branch_name)
    if name == "glossary.md":
        return template_glossary(branch_name)
    if name == "unsorted.md":
        return template_unsorted()
    if name == "pending-promotion.md":
        return template_pending_promotion()
    return None


SKELETON_FILES = (
    "overview.md",
    "decisions.md",
    "progress.md",
    "risks.md",
    "glossary.md",
    "unsorted.md",
    "pending-promotion.md",
)


def _normalize_progress_for_compare(text):
    """progress.md may have its auto-sync block refreshed by sync-working-tree
    even on an otherwise untouched branch. Treat the auto-sync region as
    irrelevant when judging skeleton state.
    """
    start = text.find(AUTO_START)
    end = text.find(AUTO_END)
    if start == -1 or end == -1 or end < start:
        return text
    return text[: start + len(AUTO_START)] + "\n_尚未同步_\n" + text[end:]


def _file_is_template(path, name, branch_name):
    if not path.exists():
        return True  # missing file ≡ never deviated
    template = _template_for(name, branch_name)
    if template is None:
        return True
    actual = path.read_text(encoding="utf-8")
    if name == "progress.md":
        actual = _normalize_progress_for_compare(actual)
    return actual.strip() == template.strip()


_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


def _count_meaningful_bullets(text):
    """Count `- foo` lines that are not template placeholders."""
    if not text:
        return 0
    count = 0
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            continue
        if any(marker in body for marker in PLACEHOLDER_MARKERS):
            continue
        count += 1
    return count


def _count_entries_for(branch_dir, branch_name):
    """Estimate "how many things the user has captured" on this branch.

    For each managed markdown file, count meaningful bullets in the actual
    content and subtract the bullets that ship in the template (e.g. the
    "分支: - <name>" metadata line). The result roughly tracks the number of
    captured records, without depending on artifacts/history accounting.
    """
    total = 0
    for name in SKELETON_FILES:
        path = branch_dir / name
        template = _template_for(name, branch_name)
        if template is None:
            continue
        baseline = _count_meaningful_bullets(template)
        if not path.exists():
            continue
        actual_text = path.read_text(encoding="utf-8")
        if name == "progress.md":
            actual_text = _normalize_progress_for_compare(actual_text)
        actual = _count_meaningful_bullets(actual_text)
        total += max(0, actual - baseline)
    return total


def _branch_dir_for(repo_root, branch_name, storage_root, identity):
    branch_key = sanitize_branch_name(branch_name)
    return storage_root / identity["repo_key"] / "branches" / branch_key, branch_key


def inspect_branch_dir(branch_dir, branch_name):
    """Inspect a single branch memory dir and return a structured snapshot.

    Result fields:
      exists       : the directory itself is present
      has_manifest : manifest.json present (i.e. lazy-init has run)
      is_skeleton  : every managed file matches its template (or is absent)
      deviations   : list of files that differ from their template
      entry_count  : artifacts/history/* count (rough usage signal)
      last_updated : manifest.updated_at if present
    """
    snapshot = {
        "branch": branch_name,
        "branch_key": sanitize_branch_name(branch_name),
        "path": str(branch_dir),
        "exists": branch_dir.exists() and branch_dir.is_dir(),
        "has_manifest": False,
        "is_skeleton": True,
        "deviations": [],
        "entry_count": 0,
        "last_updated": None,
    }
    if not snapshot["exists"]:
        return snapshot
    manifest_path = branch_dir / "manifest.json"
    if manifest_path.exists():
        snapshot["has_manifest"] = True
        manifest = read_json(manifest_path) or {}
        snapshot["last_updated"] = manifest.get("updated_at")
    deviations = []
    for name in SKELETON_FILES:
        if not _file_is_template(branch_dir / name, name, branch_name):
            deviations.append(name)
    snapshot["deviations"] = deviations
    snapshot["is_skeleton"] = len(deviations) == 0
    snapshot["entry_count"] = _count_entries_for(branch_dir, branch_name)
    return snapshot


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _list_git_branches(repo_root):
    try:
        return git_lines(["branch", "--format=%(refname:short)"], cwd=repo_root, check=False)
    except Exception:
        return []


def _list_memory_branches(branches_root):
    if not branches_root.exists():
        return []
    out = []
    try:
        for entry in sorted(branches_root.iterdir(), key=lambda p: p.name):
            if entry.is_dir() and not entry.name.startswith("_"):
                out.append(entry.name)
    except OSError:
        return []
    return out


def _branch_key_to_display_name(branch_key, branches_root):
    """Recover the original branch name from manifest.json if available;
    otherwise fall back to the un-sanitized key (replacing '__' → '/')."""
    manifest = read_json(branches_root / branch_key / "manifest.json") or {}
    if manifest.get("branch"):
        return manifest["branch"]
    return branch_key.replace("__", "/")


def cmd_list(args):
    repo_root = detect_repo_root(args.repo or ".")
    storage_root = get_storage_root(repo_root, args.context_dir)
    identity = detect_repo_identity(repo_root)
    branches_root = storage_root / identity["repo_key"] / "branches"

    current_branch = None
    try:
        current_branch = detect_branch(repo_root)
    except Exception:
        current_branch = None

    seen = {}
    for name in _list_git_branches(repo_root):
        seen[name] = {"name": name, "git_exists": True, "memory_exists": False}
    for key in _list_memory_branches(branches_root):
        display = _branch_key_to_display_name(key, branches_root)
        if display in seen:
            seen[display]["memory_exists"] = True
        else:
            seen[display] = {"name": display, "git_exists": False, "memory_exists": True}

    rows = []
    for name in sorted(seen.keys()):
        entry = seen[name]
        branch_dir, _ = _branch_dir_for(repo_root, name, storage_root, identity)
        snapshot = inspect_branch_dir(branch_dir, name)
        rows.append({
            "name": name,
            "git_exists": entry["git_exists"],
            "memory_exists": snapshot["exists"],
            "is_current": name == current_branch,
            "is_skeleton": snapshot["is_skeleton"],
            "deviations": snapshot["deviations"],
            "entry_count": snapshot["entry_count"],
            "last_updated": snapshot["last_updated"],
        })

    print(json.dumps({
        "repo_root": str(repo_root),
        "storage_root": str(storage_root),
        "repo_key": identity["repo_key"],
        "current_branch": current_branch,
        "branches": rows,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_inspect(args):
    repo_root = detect_repo_root(args.repo or ".")
    storage_root = get_storage_root(repo_root, args.context_dir)
    identity = detect_repo_identity(repo_root)
    branch_name = args.branch or detect_branch(repo_root)
    branch_dir, _ = _branch_dir_for(repo_root, branch_name, storage_root, identity)
    print(json.dumps(inspect_branch_dir(branch_dir, branch_name), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

# /tmp safety net for --force. Even when the user opted to skip the proper
# _archived/ backup, we still copy the doomed dir here before rmtree so a
# fresh "oops" within the same boot cycle is recoverable. /tmp gets wiped by
# the OS eventually, which matches the user's "ephemeral is fine" intent.
FORCE_SAFETY_ROOT = Path("/tmp/dev-memory-force-backup")


def _archive_path(branches_root, target_branch_key):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return branches_root / "_archived" / f"{target_branch_key}-{stamp}"


def _force_safety_path(repo_key, target_branch_key):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return FORCE_SAFETY_ROOT / repo_key / f"{target_branch_key}-{stamp}"


def _force_destroy(target_dir, repo_key):
    """rmtree, but copy to /tmp first so an accidental --force is recoverable.
    Returns the safety-net path so callers can surface it to the user."""
    safety = _force_safety_path(repo_key, target_dir.name)
    safety.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(target_dir), str(safety))
    shutil.rmtree(target_dir)
    return safety


def _resolve_conflict(target_dir, target_snapshot, mode, repo_key):
    """Return (None, safety_path|None) on success, or (reason, None) to abort.

    safety_path is set when --force was honored — it points to the /tmp copy
    we squirreled away before rmtree.
    """
    if not target_snapshot["exists"]:
        return None, None
    if target_snapshot["is_skeleton"]:
        # Empty skeleton: silently overwrite, regardless of mode.
        shutil.rmtree(target_dir)
        return None, None
    # Used target.
    if mode == "force":
        safety = _force_destroy(target_dir, repo_key)
        return None, safety
    if mode == "backup":
        archive = _archive_path(target_dir.parent, target_dir.name)
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_dir), str(archive))
        return None, None
    reason = (
        f"target branch '{target_snapshot['branch']}' already has memory "
        f"({len(target_snapshot['deviations'])} files diverged from template). "
        f"Pass --force or --backup to proceed."
    )
    return reason, None


# ---------------------------------------------------------------------------
# Manifest update
# ---------------------------------------------------------------------------

def _rewrite_manifest(branch_dir, new_branch_name, source_branch_name=None, op=None):
    manifest_path = branch_dir / "manifest.json"
    manifest = read_json(manifest_path) or {}
    new_key = sanitize_branch_name(new_branch_name)
    manifest["branch"] = new_branch_name
    manifest["branch_key"] = new_key
    manifest["updated_at"] = now_iso()
    if op:
        prov = manifest.get("provenance") or []
        if isinstance(prov, list):
            prov.append({
                "op": op,
                "from": source_branch_name,
                "at": manifest["updated_at"],
            })
            manifest["provenance"] = prov
    write_json(manifest_path, manifest)


# ---------------------------------------------------------------------------
# Mechanical metadata rewrite (after fork/rename)
# ---------------------------------------------------------------------------

# 5 of the 7 managed files have a "## 分支" / "- <branch>" self-identifier
# stamped by their template. After fork/rename the bullet still reads as the
# source branch — we rewrite it to the new branch so the file no longer claims
# to belong to the old one.
_BRANCH_SECTION_FILES = (
    "overview.md",
    "decisions.md",
    "progress.md",
    "risks.md",
    "glossary.md",
)

_BRANCH_SECTION_RE = re.compile(
    r"(^## 分支\s*\n+\s*-\s+)([^\n]+)",
    re.MULTILINE,
)


def _rewrite_branch_self_identifier(path, new_branch_name):
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new_text, n = _BRANCH_SECTION_RE.subn(
        lambda m: f"{m.group(1)}{new_branch_name}",
        text,
        count=1,
    )
    if n == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def _reset_progress_auto_sync(branch_dir):
    """The auto-sync block holds git facts (HEAD, changed paths, commits) for
    the *source* branch at fork/rename time. After transfer those facts are
    stale; reset to the placeholder so the next `capture sync-working-tree`
    repopulates from the new branch."""
    progress = branch_dir / "progress.md"
    if not progress.exists():
        return
    text = progress.read_text(encoding="utf-8")
    start = text.find(AUTO_START)
    end = text.find(AUTO_END)
    if start == -1 or end == -1 or end < start:
        return
    rebuilt = (
        text[: start + len(AUTO_START)]
        + "\n_尚未同步_\n"
        + text[end:]
    )
    progress.write_text(rebuilt, encoding="utf-8")


def _rewrite_branch_metadata(branch_dir, new_branch_name):
    for name in _BRANCH_SECTION_FILES:
        _rewrite_branch_self_identifier(branch_dir / name, new_branch_name)
    _reset_progress_auto_sync(branch_dir)


# ---------------------------------------------------------------------------
# Provenance note (overview.md)
# ---------------------------------------------------------------------------

PROVENANCE_HEADER = "## 分支起源"


def _provenance_block(source_branch_name, op):
    if op == "rename":
        verb = "renamed"
        suffix = "原分支已不存在（仅改名）"
    elif op == "worktree-inherit":
        verb = "auto-inherited (worktree)"
        suffix = "Worktree 首次 lazy-init 时自动从源分支拉取记忆；源分支保留"
    else:
        verb = "forked"
        suffix = "原分支保留；本分支为独立延伸"
    return (
        f"{PROVENANCE_HEADER}\n\n"
        f"- {verb} from `{source_branch_name}` at {now_iso()}\n"
        f"- 模板锚定字段已重写为新分支名；用户自由文本未改，引用以源分支语境为准\n"
        f"- {suffix}\n"
    )


def _stamp_overview_provenance(branch_dir, source_branch_name, op):
    """Insert (or replace) the 分支起源 section in overview.md right after the
    "## 分支" self-identifier, so cold-start readers see who/where-from on the
    same screen."""
    overview = branch_dir / "overview.md"
    if not overview.exists():
        return
    text = overview.read_text(encoding="utf-8")
    block = _provenance_block(source_branch_name, op)

    # Strip any pre-existing 分支起源 section so re-running fork/rename
    # doesn't accumulate duplicates.
    text = re.sub(
        r"## 分支起源\s*\n.*?(?=^## |\Z)",
        "",
        text,
        flags=re.MULTILINE | re.DOTALL,
    ).rstrip() + "\n"

    # Insert immediately after the "## 分支\n\n- <name>" block.
    insert_re = re.compile(
        r"(^## 分支\s*\n+\s*-\s+[^\n]+\n+)",
        re.MULTILINE,
    )
    if insert_re.search(text):
        new_text = insert_re.sub(lambda m: m.group(1) + "\n" + block + "\n", text, count=1)
    else:
        # Fallback: append at end.
        new_text = text.rstrip() + "\n\n" + block
    overview.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# rename / fork
# ---------------------------------------------------------------------------

def _resolve_conflict_mode(args):
    if getattr(args, "force", False):
        return "force"
    if getattr(args, "backup", False):
        return "backup"
    return "abort"


def _do_transfer(args, *, op):
    repo_root = detect_repo_root(args.repo or ".")
    storage_root = get_storage_root(repo_root, args.context_dir)
    identity = detect_repo_identity(repo_root)
    source_name = args.source
    target_name = args.target
    if not source_name or not target_name:
        raise ValueError("both --source and --target are required")
    if source_name == target_name:
        raise ValueError("source and target are identical")

    source_dir, source_key = _branch_dir_for(repo_root, source_name, storage_root, identity)
    target_dir, target_key = _branch_dir_for(repo_root, target_name, storage_root, identity)

    source_snapshot = inspect_branch_dir(source_dir, source_name)
    if not source_snapshot["exists"]:
        raise ValueError(f"source branch '{source_name}' has no memory dir at {source_dir}")
    if source_snapshot["is_skeleton"] and not args.allow_empty_source:
        raise ValueError(
            f"source branch '{source_name}' is an empty skeleton — nothing to transfer. "
            f"Pass --allow-empty-source to proceed anyway."
        )

    target_snapshot = inspect_branch_dir(target_dir, target_name)
    abort_reason, safety = _resolve_conflict(
        target_dir, target_snapshot, _resolve_conflict_mode(args), identity["repo_key"],
    )
    if abort_reason:
        raise RuntimeError(abort_reason)

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if op == "rename":
        shutil.move(str(source_dir), str(target_dir))
    elif op == "fork":
        shutil.copytree(str(source_dir), str(target_dir))
    else:
        raise ValueError(f"unknown op: {op}")

    _rewrite_manifest(target_dir, target_name, source_branch_name=source_name, op=op)
    _rewrite_branch_metadata(target_dir, target_name)
    _stamp_overview_provenance(target_dir, source_name, op)

    result = {
        "op": op,
        "source": source_name,
        "target": target_name,
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "source_branch_key": source_key,
        "target_branch_key": target_key,
    }
    if safety:
        result["force_safety_backup"] = str(safety)
    return result


def cmd_rename(args):
    result = _do_transfer(args, op="rename")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_fork(args):
    result = _do_transfer(args, op="fork")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# delete / init
# ---------------------------------------------------------------------------

def _resolve_target_branch(args):
    repo_root = detect_repo_root(args.repo or ".")
    storage_root = get_storage_root(repo_root, args.context_dir)
    identity = detect_repo_identity(repo_root)
    branch_name = args.branch or detect_branch(repo_root)
    branch_dir, branch_key = _branch_dir_for(repo_root, branch_name, storage_root, identity)
    return repo_root, storage_root, identity, branch_name, branch_key, branch_dir


def cmd_delete(args):
    repo_root, _, identity, branch_name, branch_key, branch_dir = _resolve_target_branch(args)
    snapshot = inspect_branch_dir(branch_dir, branch_name)
    if not snapshot["exists"]:
        print(json.dumps({
            "op": "delete",
            "branch": branch_name,
            "branch_key": branch_key,
            "branch_dir": str(branch_dir),
            "mode": "noop",
            "detail": "branch has no memory dir",
        }, ensure_ascii=False, indent=2))
        return 0
    mode = _resolve_conflict_mode(args)
    if snapshot["is_skeleton"]:
        # Empty skeleton has nothing to lose — just remove and report.
        shutil.rmtree(branch_dir)
        print(json.dumps({
            "op": "delete",
            "branch": branch_name,
            "branch_key": branch_key,
            "branch_dir": str(branch_dir),
            "mode": mode if mode != "abort" else "skeleton",
            "entry_count_before": 0,
        }, ensure_ascii=False, indent=2))
        return 0
    if mode == "abort":
        raise RuntimeError(
            f"branch '{branch_name}' has {snapshot['entry_count']} memory entries. "
            f"Pass --backup (move to _archived/) or --force (delete outright)."
        )
    abort_reason, safety = _resolve_conflict(branch_dir, snapshot, mode, identity["repo_key"])
    if abort_reason:
        raise RuntimeError(abort_reason)
    result = {
        "op": "delete",
        "branch": branch_name,
        "branch_key": branch_key,
        "branch_dir": str(branch_dir),
        "mode": mode,
        "entry_count_before": snapshot["entry_count"],
    }
    if safety:
        result["force_safety_backup"] = str(safety)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args):
    repo_root, _, identity, branch_name, branch_key, branch_dir = _resolve_target_branch(args)
    snapshot = inspect_branch_dir(branch_dir, branch_name)
    # If branch is already a fresh skeleton (or never initialized) just lazy-init
    # in place so the caller gets a deterministic clean dir without needing flags.
    if not snapshot["exists"] or snapshot["is_skeleton"]:
        ensure_branch_paths_exist(str(repo_root), context_dir=args.context_dir, branch=branch_name)
        print(json.dumps({
            "op": "init",
            "branch": branch_name,
            "branch_key": branch_key,
            "branch_dir": str(branch_dir),
            "mode": "noop",
            "detail": "branch was already empty; skeleton ensured",
        }, ensure_ascii=False, indent=2))
        return 0
    mode = _resolve_conflict_mode(args)
    if mode == "abort":
        raise RuntimeError(
            f"branch '{branch_name}' has {snapshot['entry_count']} memory entries that would be wiped. "
            f"Pass --force or --backup to proceed."
        )
    abort_reason, safety = _resolve_conflict(branch_dir, snapshot, mode, identity["repo_key"])
    if abort_reason:
        raise RuntimeError(abort_reason)
    # Re-create the skeleton in place.
    ensure_branch_paths_exist(str(repo_root), context_dir=args.context_dir, branch=branch_name)
    result = {
        "op": "init",
        "branch": branch_name,
        "branch_key": branch_key,
        "branch_dir": str(branch_dir),
        "mode": mode,
        "entry_count_before": snapshot["entry_count"],
    }
    if safety:
        result["force_safety_backup"] = str(safety)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# inherit-worktree-base
# ---------------------------------------------------------------------------

def cmd_inherit_worktree_base(args):
    """Explicit user-triggered worktree memory inheritance.

    Mirrors what lazy-init does automatically on first touch, but usable
    *after* the worktree session already started (auto-inherit only fires
    when branch_dir didn't yet exist). Detects the source branch from reflog
    by default; --source overrides.

    Conflict handling on a non-empty target follows the same flags as fork:
    --backup (recommended), --force, default abort.
    """
    repo_root = detect_repo_root(args.repo or ".")
    storage_root = get_storage_root(repo_root, args.context_dir)
    identity = detect_repo_identity(repo_root)
    target_name = args.branch or detect_branch(repo_root)
    target_dir, target_key = _branch_dir_for(repo_root, target_name, storage_root, identity)

    in_worktree = False
    try:
        in_worktree = is_worktree(repo_root)
    except Exception:
        in_worktree = False
    if not in_worktree and not args.allow_non_worktree:
        raise RuntimeError(
            f"'{repo_root}' is not a linked worktree. Pass --allow-non-worktree "
            f"if you really want to inherit memory into a main-repo checkout."
        )

    source_name = args.source or detect_worktree_base_branch(repo_root, target_name)
    if not source_name:
        raise RuntimeError(
            f"could not auto-detect the base branch for '{target_name}' from reflog. "
            f"Pass --source <branch> explicitly. (Common cause: the worktree was "
            f"created without -b, or the reflog rolled off.)"
        )
    if source_name == target_name:
        raise ValueError("source and target are identical")

    source_dir, source_key = _branch_dir_for(repo_root, source_name, storage_root, identity)
    source_snapshot = inspect_branch_dir(source_dir, source_name)
    if not source_snapshot["exists"]:
        raise ValueError(f"source branch '{source_name}' has no memory dir at {source_dir}")
    if source_snapshot["is_skeleton"] and not args.allow_empty_source:
        raise ValueError(
            f"source branch '{source_name}' is an empty skeleton — nothing to inherit. "
            f"Pass --allow-empty-source to proceed anyway."
        )

    target_snapshot = inspect_branch_dir(target_dir, target_name)
    abort_reason, safety = _resolve_conflict(
        target_dir, target_snapshot, _resolve_conflict_mode(args), identity["repo_key"],
    )
    if abort_reason:
        raise RuntimeError(abort_reason)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source_dir), str(target_dir))
    _rewrite_manifest(target_dir, target_name, source_branch_name=source_name, op="worktree-inherit")
    _rewrite_branch_metadata(target_dir, target_name)
    _stamp_overview_provenance(target_dir, source_name, "worktree-inherit")

    result = {
        "op": "inherit-worktree-base",
        "source": source_name,
        "target": target_name,
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "source_branch_key": source_key,
        "target_branch_key": target_key,
        "source_detected_via": "reflog" if not args.source else "user",
    }
    if safety:
        result["force_safety_backup"] = str(safety)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------

def _add_common_args(parser):
    parser.add_argument("--repo", help="Repo root (defaults to cwd)")
    parser.add_argument("--context-dir", help="Storage root (defaults to ~/.dev-memory/repos)")


def _add_transfer_args(parser):
    _add_common_args(parser)
    parser.add_argument("--source", required=True, help="Source branch name")
    parser.add_argument("--target", required=True, help="Target branch name")
    parser.add_argument("--force", action="store_true", help="Overwrite target if it has content")
    parser.add_argument("--backup", action="store_true", help="Move target to _archived/ before overwriting")
    parser.add_argument(
        "--allow-empty-source",
        action="store_true",
        help="Proceed even if source is an empty skeleton",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Move or clone branch-scoped dev-memory between branches.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_p = subparsers.add_parser("list", help="Enumerate branches with memory metadata")
    _add_common_args(list_p)

    inspect_p = subparsers.add_parser("inspect", help="Inspect a single branch's memory state")
    _add_common_args(inspect_p)
    inspect_p.add_argument("--branch", help="Branch name (defaults to current)")

    rename_p = subparsers.add_parser("rename", help="Move source memory dir onto target")
    _add_transfer_args(rename_p)

    fork_p = subparsers.add_parser("fork", help="Copy source memory dir onto target")
    _add_transfer_args(fork_p)

    delete_p = subparsers.add_parser("delete", help="Delete a branch's memory dir")
    _add_common_args(delete_p)
    delete_p.add_argument("--branch", help="Branch name (defaults to current)")
    delete_p.add_argument("--force", action="store_true", help="Delete outright")
    delete_p.add_argument("--backup", action="store_true", help="Move to _archived/ instead of deleting")

    init_p = subparsers.add_parser("init", help="Reset a branch's memory dir to a fresh skeleton")
    _add_common_args(init_p)
    init_p.add_argument("--branch", help="Branch name (defaults to current)")
    init_p.add_argument("--force", action="store_true", help="Wipe existing content before re-creating skeleton")
    init_p.add_argument("--backup", action="store_true", help="Move existing content to _archived/ before re-creating")

    inherit_p = subparsers.add_parser(
        "inherit-worktree-base",
        help="Copy the worktree's base-branch memory into the current branch's slot",
    )
    _add_common_args(inherit_p)
    inherit_p.add_argument("--branch", help="Target branch (defaults to current)")
    inherit_p.add_argument("--source", help="Source branch to inherit from (defaults to reflog detection)")
    inherit_p.add_argument("--force", action="store_true", help="Overwrite target even if it already has content")
    inherit_p.add_argument("--backup", action="store_true", help="Move existing target to _archived/ first")
    inherit_p.add_argument(
        "--allow-empty-source",
        action="store_true",
        help="Proceed even if source branch memory is an empty skeleton",
    )
    inherit_p.add_argument(
        "--allow-non-worktree",
        action="store_true",
        help="Allow running this in the main repo checkout (not a linked worktree)",
    )

    args = parser.parse_args()
    handlers = {
        "list": cmd_list,
        "inspect": cmd_inspect,
        "rename": cmd_rename,
        "fork": cmd_fork,
        "delete": cmd_delete,
        "init": cmd_init,
        "inherit-worktree-base": cmd_inherit_worktree_base,
    }
    try:
        return handlers[args.command](args) or 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
