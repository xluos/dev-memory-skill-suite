#!/usr/bin/env python3
"""
dev-assets-setup: initialize the skeleton AND merge any unsorted.md content
into the structured v2 files. In v2, setup is no longer a gate — capture
lazy-inits files on first write. Setup's job is now:

  1. Ensure skeleton exists (idempotent).
  2. Scan unsorted.md and present each entry to the user for classification.
  3. Route user's choices into decisions/progress/risks/glossary/shared-*.
  4. Mark manifest.setup_completed = true.
"""

import argparse
import json
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parents[3] / "lib"
if _lib.exists() and str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

from dev_asset_common import (
    ensure_branch_paths_exist,
    get_setup_completed,
    mark_setup_completed,
    split_sections,
    upsert_markdown_section,
)


def _extract_unsorted_entries(unsorted_path):
    """Parse unsorted.md into a list of individual entries. Each entry is a
    bullet line or a paragraph; we split on top-level bullets to give the
    user one-at-a-time classification. Returns [] if empty or only
    placeholder text.
    """
    if not unsorted_path.exists():
        return []
    content = unsorted_path.read_text(encoding="utf-8")
    _, sections = split_sections(content)
    entries = []
    for _, body in sections:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in ("- 待补充", "- 待刷新"):
                continue
            # Only include top-level bullets; nested lines get combined.
            if stripped.startswith("- "):
                entries.append(stripped[2:].strip())
            elif entries:
                # Continuation of the previous bullet.
                entries[-1] = entries[-1] + " " + stripped
    return entries


def _report_skeleton(paths, setup_completed):
    """Build the `files` key for the setup output."""
    return {
        "setup_completed": setup_completed,
        "files": {key: str(value) for key, value in paths.items()},
    }


def command_init(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    report = _report_skeleton(paths, get_setup_completed(paths["manifest"]))

    unsorted_entries = _extract_unsorted_entries(paths["unsorted"])
    report.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "storage_root": str(storage_root),
            "repo_dir": str(repo_dir),
            "branch_dir": str(branch_dir),
            "unsorted_entries": unsorted_entries,
            "unsorted_count": len(unsorted_entries),
            "mode": "init",
        }
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# Merge plan: the caller (agent) receives unsorted entries from `init`,
# asks the user (or classifies with LLM) which bucket each goes to, then
# submits the classification as a JSON file to `merge-unsorted`.
#
# plan.json format:
# {
#   "classifications": [
#     {"entry": "original bullet text", "kind": "decision|progress|next|risk|glossary|source|shared-*|skip"},
#     ...
#   ],
#   "clear_unsorted_on_done": true
# }
#
# The kind values match dev-assets-capture's KIND_MAP.

_SETUP_KIND_TO_TARGET = {
    "decision": ("decisions", "关键决策与原因"),
    "progress": ("progress", "当前进展"),
    "next": ("progress", "下一步"),
    "risk": ("risks", "阻塞与注意点"),
    "glossary": ("glossary", "当前有效上下文"),
    "source": ("glossary", "分支源资料入口"),
    "shared-decision": ("repo_decisions", "跨分支通用决策"),
    "shared-context": ("repo_glossary", "长期有效背景"),
    "shared-source": ("repo_glossary", "共享入口"),
}


def _apply_classifications(paths, classifications):
    """Group classifications by target section, then upsert-merge each group
    into the target file. Returns a per-section tally.
    """
    groups = {}  # (file_key, section) -> list of entry strings
    skipped = 0
    for item in classifications or []:
        entry = (item.get("entry") or "").strip()
        kind = (item.get("kind") or "").strip()
        if not entry:
            continue
        if kind == "skip" or not kind:
            skipped += 1
            continue
        target = _SETUP_KIND_TO_TARGET.get(kind)
        if not target:
            skipped += 1
            continue
        groups.setdefault(target, []).append(entry)

    tally = {}
    for (file_key, section), entries in groups.items():
        # Append to existing section rather than replace — preserves whatever
        # was already in decisions/progress/etc. before setup merge ran.
        from dev_asset_common import append_to_section
        body = "\n".join(f"- {e}" for e in entries)
        path = paths[file_key]
        append_to_section(path, section, body)
        tally[f"{file_key}:{section}"] = len(entries)
    return {"applied": tally, "skipped": skipped}


def _clear_unsorted(unsorted_path):
    """After merge, reset unsorted.md to its placeholder template."""
    from dev_asset_common import template_unsorted
    unsorted_path.write_text(template_unsorted(), encoding="utf-8")


def command_merge_unsorted(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )

    plan = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    classifications = plan.get("classifications") or []
    clear_on_done = plan.get("clear_unsorted_on_done", True)

    result = _apply_classifications(paths, classifications)

    if clear_on_done and result["applied"]:
        _clear_unsorted(paths["unsorted"])

    mark_setup_completed(paths["manifest"])

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "branch_dir": str(branch_dir),
                "mode": "merge-unsorted",
                "applied": result["applied"],
                "skipped": result["skipped"],
                "unsorted_cleared": clear_on_done and bool(result["applied"]),
                "setup_completed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_mark_completed(args):
    """Just flip setup_completed to true without touching unsorted. Use when
    there's nothing to merge but the user wants to formally mark setup done
    so classifier defaults shift from unsorted to progress."""
    repo_root, branch_name, _, _, _, _, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    mark_setup_completed(paths["manifest"])
    print(
        json.dumps(
            {
                "branch": branch_name,
                "branch_dir": str(branch_dir),
                "mode": "mark-completed",
                "setup_completed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _add_common_args(p):
    p.add_argument("--repo", default=".", help="Path inside the target Git repository")
    p.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    p.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")


def main():
    parser = argparse.ArgumentParser(
        description="Initialize repo+branch dev-assets skeleton, merge unsorted content, and mark setup done.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Ensure skeleton exists, return paths + unsorted entries for review")
    _add_common_args(p_init)

    p_merge = sub.add_parser("merge-unsorted", help="Apply a classification plan over unsorted entries")
    _add_common_args(p_merge)
    p_merge.add_argument("--plan-file", required=True, help="Path to JSON plan with classifications")

    p_mark = sub.add_parser("mark-completed", help="Flip manifest.setup_completed = true without merging")
    _add_common_args(p_mark)

    # Backward-compat: old callers ran `init_dev_assets.py --repo X` with no
    # subcommand. If argv starts with a flag (not a known subcommand or help),
    # inject `init` so the legacy invocation still works.
    argv = sys.argv[1:]
    known_cmds = {"init", "merge-unsorted", "mark-completed"}
    if argv and argv[0] not in known_cmds and argv[0] not in ("-h", "--help"):
        argv = ["init"] + argv

    args = parser.parse_args(argv)
    try:
        return {
            "init": command_init,
            "merge-unsorted": command_merge_unsorted,
            "mark-completed": command_mark_completed,
        }[args.command](args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
