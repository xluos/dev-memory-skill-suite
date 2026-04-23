#!/usr/bin/env python3
"""
dev-assets-capture: unified write entrypoint for repo+branch development
assets. Merges the old sync (checkpoint batch record) and update (targeted
section rewrite) into one skill. Also the lazy-init entry — never raises on
missing branch_dir; the directory is seeded on first write.

Subcommands:
  record          : write content into one or more sections (inline/kind/auto/batch)
  show            : dump current paths + missing docs
  sync-working-tree : refresh progress.md's auto-sync block from git facts
  record-head     : stamp last_seen_head onto branch+repo manifests
  suggest-kind    : dry-run heuristic classifier
  classify        : dry-run — run classifier AND cross-branch detector
"""

import argparse
import json
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parents[3] / "lib"
if _lib.exists() and str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

from dev_asset_common import (
    PLACEHOLDER_MARKERS,
    append_to_section,
    asset_paths,
    classify_content,
    collect_git_facts,
    ensure_branch_paths_exist,
    get_head_commit,
    get_setup_completed,
    is_cross_branch_candidate,
    join_sections,
    list_missing_docs,
    now_iso,
    read_json,
    render_bullets,
    split_sections,
    sync_progress,
    upsert_markdown_section,
    upsert_progress_section,
    write_json,
)


def _append_with_separator(path, title, body):
    """Like append_to_section but always inserts a blank line between the
    existing section body and the new entry. Drops placeholder-only bodies
    instead of padding them with a blank line.
    """
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix, sections = split_sections(content)
    target = title.strip()
    matched = False
    updated = []
    body_stripped = body.strip()

    def _is_placeholder_only(text):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return True
        return all(any(marker in ln for marker in PLACEHOLDER_MARKERS) for ln in lines)

    for existing_title, existing_body in sections:
        if existing_title.strip() == target and not matched:
            if _is_placeholder_only(existing_body):
                combined = body_stripped
            else:
                combined = existing_body.rstrip() + "\n\n" + body_stripped
            updated.append((existing_title, combined))
            matched = True
        else:
            updated.append((existing_title, existing_body))
    if not matched:
        updated.append((title, body_stripped))
    path.write_text(join_sections(prefix, updated), encoding="utf-8")


# v2 KIND_MAP. `default_mode` governs whether a new entry accumulates
# (append) or replaces the whole section (upsert). Accumulation fits
# "decisions", "risks", "glossary" where each entry is independent and
# historically meaningful; upsert fits "progress/next/overview" where the
# section represents *latest state* and older content is stale by design.
KIND_MAP = {
    # accumulation (each entry stands alone, new ones add to the list)
    "decision": {"file": "decisions", "section": "关键决策与原因", "default_mode": "append"},
    "risk": {"file": "risks", "section": "阻塞与注意点", "default_mode": "append"},
    "glossary": {"file": "glossary", "section": "当前有效上下文", "default_mode": "append"},
    "source": {"file": "glossary", "section": "分支源资料入口", "default_mode": "append"},
    # snapshot (section always reflects the latest state; new write replaces)
    "progress": {"file": "progress", "section": "当前进展", "default_mode": "upsert"},
    "next": {"file": "progress", "section": "下一步", "default_mode": "upsert"},
    "overview": {"file": "overview", "section": "当前目标", "default_mode": "upsert"},
    "scope": {"file": "overview", "section": "范围边界", "default_mode": "upsert"},
    "stage": {"file": "overview", "section": "当前阶段", "default_mode": "upsert"},
    "constraint": {"file": "overview", "section": "关键约束", "default_mode": "upsert"},
    # repo-shared: decisions/context/source accumulate, overview/constraint snapshot
    "shared-decision": {"file": "repo_decisions", "section": "跨分支通用决策", "default_mode": "append"},
    "shared-context": {"file": "repo_glossary", "section": "长期有效背景", "default_mode": "append"},
    "shared-source": {"file": "repo_glossary", "section": "共享入口", "default_mode": "append"},
    "shared-overview": {"file": "repo_overview", "section": "长期目标与边界", "default_mode": "upsert"},
    "shared-constraint": {"file": "repo_overview", "section": "仓库级关键约束", "default_mode": "upsert"},
    # fallback bins always accumulate
    "unsorted": {"file": "unsorted", "section": "待分类", "default_mode": "append"},
    "pending": {"file": "pending_promotion", "section": "候选条目", "default_mode": "append"},
}

# Session-payload → kind mapping, used by `record --summary-json`. Each entry
# is (payload_key, kind, optional_transform). Several keys may route to the
# same kind; that's fine — the write layer upserts.
SESSION_PAYLOAD_MAP = [
    ("overview_summary", "overview", None),
    ("implementation_notes", "progress", None),
    ("changes", "progress", None),
    ("next_steps", "next", None),
    ("risks", "risk", None),
    ("memory", "glossary", None),
    ("context_updates", "glossary", None),
    ("review_notes", "decision", None),
    ("frontend_updates", "glossary", "前端相关"),
    ("backend_updates", "glossary", "后端相关"),
    ("test_updates", "glossary", "测试相关"),
    ("sources", "shared-source", None),
    ("source_updates", "shared-source", None),
]


def normalize_items(items):
    if items is None:
        return []
    if isinstance(items, str):
        stripped = items.strip()
        return [stripped] if stripped else []
    return [str(item).strip() for item in items if str(item).strip()]


def bullets(items, empty_text="- 待补充", wrap_code=False):
    return render_bullets(normalize_items(items), empty_text=empty_text, wrap_code=wrap_code)


def decision_body(item):
    parts = [f"- 结论: {item['decision']}"]
    if item.get("reason"):
        parts.append(f"- 原因: {item['reason']}")
    if item.get("impact"):
        parts.append(f"- 影响范围: {item['impact']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Write primitives
# ---------------------------------------------------------------------------

def _resolve_target(paths, kind, title_override=None):
    """Return (target_path, section_title) for a given kind. Raises on
    unknown kind so the caller can surface a clear error."""
    spec = KIND_MAP.get(kind)
    if not spec:
        raise RuntimeError(f"unsupported kind: {kind}")
    target_path = paths[spec["file"]]
    section_title = (title_override or spec["section"]).strip()
    return spec["file"], target_path, section_title


def _write_one(paths, kind, body, title_override=None, *, mode_override=None):
    """Write a body into the kind's target file. Picks append vs upsert from
    KIND_MAP[kind].default_mode unless overridden. progress.md uses a
    dedicated upsert that preserves the auto-sync marker.

    When appending multi-line bodies, a blank line is inserted between the
    existing content and the new entry so each entry reads as its own unit.
    """
    file_key, target_path, section_title = _resolve_target(paths, kind, title_override)
    spec = KIND_MAP.get(kind) or {}
    mode = mode_override or spec.get("default_mode", "upsert")

    if mode == "append":
        # Auto-prefix a bullet if the body isn't already one — accumulation
        # sections are bullet lists by convention. The separator helper
        # handles the blank-line spacing so entries remain visually distinct.
        body_to_write = body.strip()
        if not body_to_write.startswith(("- ", "* ", "#")):
            body_to_write = "- " + body_to_write
        _append_with_separator(target_path, section_title, body_to_write)
    else:
        if file_key == "progress":
            upsert_progress_section(target_path, section_title, body)
        else:
            upsert_markdown_section(target_path, section_title, body)
    return {"file": _label(file_key), "section": section_title, "mode": mode}


def _label(file_key):
    if file_key.startswith("repo_"):
        return f"repo/{file_key[5:]}.md"
    if file_key == "pending_promotion":
        return "branch/pending-promotion.md"
    return f"branch/{file_key}.md"


def _maybe_stage_pending(paths, body, branch_name):
    """If body looks cross-branch-reusable, append a copy to pending-promotion
    with a lightweight marker. Returns the touch record or None.
    """
    if not is_cross_branch_candidate(body, branch_name):
        return None
    # Append rather than upsert — pending should accumulate candidates, not
    # overwrite.
    entry = f"- {now_iso()[:10]}: {body.strip().splitlines()[0][:160]}"
    # Keep full text as a nested detail so graduate has the original.
    full = f"{entry}\n  - 原文: {body.strip().replace(chr(10), ' / ')[:500]}"
    append_to_section(paths["pending_promotion"], "候选条目", full)
    return {"file": "branch/pending-promotion.md", "section": "候选条目", "mode": "append-candidate"}


# ---------------------------------------------------------------------------
# Content loaders
# ---------------------------------------------------------------------------

def _load_optional_text(value, file_path=None):
    if value:
        stripped = value.strip()
        return stripped or None
    if file_path:
        return (Path(file_path).read_text(encoding="utf-8").strip()) or None
    return None


def _load_free_content(args):
    """Load content for single-kind writes. Supports:
      --content / --content-file : inline body
      --summary / --summary-file : session-derived summary (prefixed)
      --user-input / --user-input-file : user turn (prefixed)
    When both summary and user-input are given, they're combined; inline
    content becomes an additional 补充备注 section.
    """
    inline = _load_optional_text(args.content, getattr(args, "content_file", None))
    summary = _load_optional_text(getattr(args, "summary", None), getattr(args, "summary_file", None))
    user_input = _load_optional_text(getattr(args, "user_input", None), getattr(args, "user_input_file", None))

    if summary or user_input:
        blocks = []
        if user_input:
            blocks.append(f"### 用户这次输入\n\n{user_input}")
        if summary:
            blocks.append(f"### 基于当前会话整理\n\n{summary}")
        if inline:
            blocks.append(f"### 补充备注\n\n{inline}")
        return "\n\n".join(blocks), "session+input"
    if inline:
        return inline, "content-only"
    return None, None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
    return 0


def command_suggest_kind(args):
    content = _load_optional_text(args.content, args.content_file)
    if not content:
        raise RuntimeError("one of --content / --content-file is required")
    already_setup = bool(args.already_setup)
    kind = classify_content(content, already_setup=already_setup)
    spec = KIND_MAP.get(kind, {"file": "unsorted", "section": "待分类"})
    branch_name = args.branch_name or ""
    cross_branch = is_cross_branch_candidate(content, branch_name) if branch_name else None
    print(
        json.dumps(
            {
                "kind": kind,
                "target_file": _label(spec["file"]),
                "section": spec["section"],
                "cross_branch_candidate": cross_branch,
                "already_setup": already_setup,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_classify(args):
    # Like suggest-kind but always resolves through the lazy-init pipeline so
    # the caller also gets the computed paths (useful for the capture skill's
    # inner loop).
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    content = _load_optional_text(args.content, args.content_file)
    if not content:
        raise RuntimeError("one of --content / --content-file is required")
    already_setup = get_setup_completed(paths["manifest"])
    kind = classify_content(content, already_setup=already_setup)
    spec = KIND_MAP.get(kind, KIND_MAP["unsorted"])
    cross_branch = is_cross_branch_candidate(content, branch_name or "")
    print(
        json.dumps(
            {
                "branch": branch_name,
                "already_setup": already_setup,
                "kind": kind,
                "target_file": _label(spec["file"]),
                "section": spec["section"],
                "cross_branch_candidate": cross_branch,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_record(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    already_setup = get_setup_completed(paths["manifest"])

    touched = []
    mode_used = None

    # Mode 1: batch session payload (merges old sync record-session).
    payload = None
    if args.summary_json:
        payload = json.loads(args.summary_json)
    elif args.summary_file:
        payload = json.loads(Path(args.summary_file).read_text(encoding="utf-8"))

    if payload:
        mode_used = "session-payload"
        title = payload.get("title") or "checkpoint"

        # Simple scalar mappings.
        for key, kind, subsection_title in SESSION_PAYLOAD_MAP:
            items = normalize_items(payload.get(key))
            if not items:
                continue
            body = bullets(items) if not subsection_title else f"### {subsection_title}\n\n{bullets(items)}"
            touched.append(_write_one(paths, kind, body))
            # Cross-branch staging — apply to each item separately for best recall.
            for item in items:
                rec = _maybe_stage_pending(paths, item, branch_name or "")
                if rec:
                    touched.append(rec)

        # Risks also go into the 后续继续前要注意 section (same as v1 behavior).
        risk_items = normalize_items(payload.get("risks"))
        if risk_items:
            touched.append(_write_one(paths, "risk", bullets(risk_items), title_override="后续继续前要注意"))

        # Structured decisions (decision/reason/impact trios).
        decision_items = [decision_body(item) for item in (payload.get("decisions") or []) if item.get("decision")]
        if decision_items:
            body = "\n\n".join(decision_items)
            touched.append(_write_one(paths, "decision", body))
            for item in payload.get("decisions") or []:
                if item.get("decision"):
                    rec = _maybe_stage_pending(paths, item["decision"], branch_name or "")
                    if rec:
                        touched.append(rec)

        extra_manifest = {"last_session_sync_title": title, "last_session_sync_mode": "capture-session"}

    else:
        # Mode 2/3: free-form content with kind (explicit or auto-classify).
        content, update_mode = _load_free_content(args)
        if content is None:
            raise RuntimeError(
                "provide one of: --summary-json/-file (batch), --kind+--content (targeted), "
                "or --auto --content (heuristic classify)"
            )

        kind = args.kind.lower() if args.kind else None
        if args.auto or not kind:
            classified = classify_content(content, already_setup=already_setup)
            kind = kind or classified
            mode_used = f"auto-classified-{classified}"
        else:
            mode_used = "explicit-kind"

        touched.append(_write_one(paths, kind, content, title_override=args.title))
        rec = _maybe_stage_pending(paths, content, branch_name or "")
        if rec:
            touched.append(rec)

        extra_manifest = {"last_capture_kind": kind, "last_capture_mode": mode_used, "last_capture_update_mode": update_mode}

    # Manifest bookkeeping.
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
            "last_seen_head": get_head_commit(repo_root) if repo_root else None,
        }
    )
    manifest.update(extra_manifest)
    manifest["last_capture_targets"] = touched
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": manifest["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": manifest["last_seen_head"],
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": mode_used,
                "setup_completed": already_setup,
                "touched_targets": touched,
                "updated_at": manifest["updated_at"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_sync_working_tree(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    if branch_name is None:
        # no-git mode — no git facts to derive.
        print(json.dumps({"mode": "no-git", "skipped": True}, ensure_ascii=False))
        return 0

    facts = collect_git_facts(repo_root, branch_name, storage_root)
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

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": "sync-working-tree",
                "focus_areas": manifest["focus_areas"],
                "files_considered": len(
                    set(
                        facts["working_tree_files"]
                        + facts["staged_files"]
                        + facts["untracked_files"]
                        + facts["since_base_files"]
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_record_head(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    manifest = read_json(paths["manifest"])
    head = args.commit or (get_head_commit(repo_root) if repo_root else None)
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
            "last_seen_head": head,
        }
    )
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": manifest["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": head,
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": "record-head",
                "last_seen_head": head,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _add_common_args(parser):
    parser.add_argument("--repo", default=".", help="Path inside the target Git repository")
    parser.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    parser.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")


def main():
    parser = argparse.ArgumentParser(
        description="Unified write entrypoint for dev-assets repo+branch memory (merges old sync + update).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="Show current paths + missing docs")
    _add_common_args(show)

    suggest = subparsers.add_parser("suggest-kind", help="Dry-run classify a content string")
    suggest.add_argument("--content", help="Inline content to classify")
    suggest.add_argument("--content-file", help="File containing content to classify")
    suggest.add_argument("--already-setup", action="store_true", help="Hint that setup is completed (shifts default from unsorted to progress)")
    suggest.add_argument("--branch-name", help="Branch name for cross-branch candidate check")

    classify = subparsers.add_parser("classify", help="Classify content against the current branch context")
    _add_common_args(classify)
    classify.add_argument("--content", help="Inline content to classify")
    classify.add_argument("--content-file", help="File containing content to classify")

    record = subparsers.add_parser("record", help="Write content into one or more sections")
    _add_common_args(record)
    record.add_argument("--kind", help=f"Explicit kind. One of: {', '.join(sorted(KIND_MAP.keys()))}")
    record.add_argument("--auto", action="store_true", help="Ignore --kind; run classifier on content")
    record.add_argument("--title", help="Override the default section title")
    record.add_argument("--content", help="Inline markdown content")
    record.add_argument("--content-file", help="File containing markdown content")
    record.add_argument("--summary", help="Session-derived summary")
    record.add_argument("--summary-file", help="File with a session-derived summary")
    record.add_argument("--user-input", help="Latest user input to store alongside summary")
    record.add_argument("--user-input-file", help="File containing the latest user input")
    record.add_argument("--summary-json", help="Inline JSON session payload (batch record)")

    sync = subparsers.add_parser("sync-working-tree", help="Refresh progress.md auto-sync block from git facts")
    _add_common_args(sync)

    head = subparsers.add_parser("record-head", help="Stamp last_seen_head onto branch+repo manifests")
    _add_common_args(head)
    head.add_argument("--commit", help="Explicit commit sha to record (defaults to HEAD)")

    args = parser.parse_args()
    try:
        handlers = {
            "show": command_show,
            "suggest-kind": command_suggest_kind,
            "classify": command_classify,
            "record": command_record,
            "sync-working-tree": command_sync_working_tree,
            "record-head": command_record_head,
        }
        return handlers[args.command](args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
