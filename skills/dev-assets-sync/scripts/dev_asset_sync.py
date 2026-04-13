#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from dev_asset_common import (
    asset_paths,
    collect_git_facts,
    get_branch_paths,
    get_head_commit,
    now_iso,
    read_json,
    render_bullets,
    sync_development,
    upsert_development_section,
    upsert_markdown_section,
    write_json,
)


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


def load_session_payload(args):
    if args.summary_json:
        return json.loads(args.summary_json)
    if args.summary_file:
        return json.loads(Path(args.summary_file).read_text(encoding="utf-8"))
    raise RuntimeError("one of --summary-json or --summary-file is required")


def build_context_body(payload):
    blocks = []
    mapping = [
        ("当前记忆", payload.get("memory") or payload.get("context_updates")),
        ("评审相关", payload.get("review_notes")),
        ("前端相关", payload.get("frontend_updates")),
        ("后端相关", payload.get("backend_updates")),
        ("测试相关", payload.get("test_updates")),
    ]
    for title, items in mapping:
        normalized = normalize_items(items)
        if normalized:
            blocks.append(f"### {title}\n\n{bullets(normalized)}")
    return "\n\n".join(blocks).strip()


def build_sources_history_body(facts):
    history_cmd = (
        f"`git log --oneline {facts['default_base']}..HEAD`"
        if facts["default_base"]
        else "`git log --oneline --decorate -n 20`"
    )
    head = facts["last_seen_head"] or "HEAD"
    return (
        f"- 查看本分支提交历史：{history_cmd}\n"
        "- 查看当前工作区改动：`git diff --name-only`\n"
        f"- 当前 HEAD：`{head}`"
    )


def sync_manifest(paths, repo_root, branch_name, branch_key, storage_root, repo_key, facts, extra=None):
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
    if extra:
        manifest.update(extra)
    write_json(paths["manifest"], manifest)
    return manifest


def touch_manifest(paths, repo_root, branch_name, branch_key, storage_root, repo_key, extra=None):
    extra_payload = dict(extra or {})
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": extra_payload.pop("updated_at", now_iso()),
        }
    )
    if extra_payload:
        manifest.update(extra_payload)
    write_json(paths["manifest"], manifest)
    return manifest


def touch_repo_manifest(paths, repo_root, branch_name, storage_root, repo_key, extra=None):
    extra_payload = dict(extra or {})
    manifest = read_json(paths["repo_manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": extra_payload.pop("updated_at", now_iso()),
            "last_seen_branch": branch_name,
        }
    )
    if extra_payload:
        manifest.update(extra_payload)
    write_json(paths["repo_manifest"], manifest)
    return manifest


def refresh_git_derived_views(paths, facts):
    sync_development(paths, facts)
    upsert_markdown_section(paths["sources"], "提交与代码历史", build_sources_history_body(facts))


def command_record_session(args):
    payload = load_session_payload(args)
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    paths = asset_paths(repo_dir, branch_dir)
    title = payload.get("title") or "提交同步"
    touched = []

    overview_items = normalize_items(payload.get("overview_summary"))
    if overview_items:
        upsert_markdown_section(paths["overview"], "当前摘要", bullets(overview_items))
        touched.append({"file": "overview.md", "section": "当前摘要"})

    progress_items = normalize_items(payload.get("implementation_notes")) or normalize_items(payload.get("changes"))
    if progress_items:
        upsert_development_section(paths["development"], "当前进展", bullets(progress_items))
        touched.append({"file": "development.md", "section": "当前进展"})

    risk_items = normalize_items(payload.get("risks"))
    if risk_items:
        risk_body = bullets(risk_items)
        upsert_development_section(paths["development"], "阻塞与注意点", risk_body)
        upsert_markdown_section(paths["context"], "后续继续前要注意", risk_body)
        touched.append({"file": "development.md", "section": "阻塞与注意点"})
        touched.append({"file": "context.md", "section": "后续继续前要注意"})

    next_items = normalize_items(payload.get("next_steps"))
    if next_items:
        upsert_development_section(paths["development"], "下一步", bullets(next_items))
        touched.append({"file": "development.md", "section": "下一步"})

    source_items = normalize_items(payload.get("sources") or payload.get("source_updates"))
    if source_items:
        upsert_markdown_section(paths["repo_sources"], "共享入口", bullets(source_items))
        touched.append({"file": "repo/sources.md", "section": "共享入口"})

    context_body = build_context_body(payload)
    if context_body:
        upsert_markdown_section(paths["context"], "当前有效上下文", context_body)
        touched.append({"file": "context.md", "section": "当前有效上下文"})

    decision_items = [decision_body(item) for item in (payload.get("decisions") or []) if item.get("decision")]
    if decision_items:
        upsert_markdown_section(paths["context"], "关键决策与原因", "\n\n".join(decision_items))
        touched.append({"file": "context.md", "section": "关键决策与原因"})

    manifest = touch_manifest(
        paths,
        repo_root,
        branch_name,
        branch_key,
        storage_root,
        repo_key,
        extra={
            "last_seen_head": get_head_commit(repo_root),
            "last_session_sync_title": title,
            "last_session_sync_mode": "commit-local",
        },
    )
    touch_repo_manifest(
        paths,
        repo_root,
        branch_name,
        storage_root,
        repo_key,
        extra={
            "updated_at": manifest["updated_at"],
            "last_seen_head": manifest["last_seen_head"],
            "default_base": manifest.get("default_base"),
        },
    )

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": "record-session",
                "title": title,
                "touched_targets": touched,
                "updated_at": manifest["updated_at"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_sync_working_tree(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    paths = asset_paths(repo_dir, branch_dir)
    facts = collect_git_facts(repo_root, branch_name, storage_root)
    refresh_git_derived_views(paths, facts)
    manifest = sync_manifest(paths, repo_root, branch_name, branch_key, storage_root, repo_key, facts)
    touch_repo_manifest(
        paths,
        repo_root,
        branch_name,
        storage_root,
        repo_key,
        extra={
            "updated_at": manifest["updated_at"],
            "last_seen_head": facts["last_seen_head"],
            "default_base": facts["default_base"],
        },
    )

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


def command_record_head(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    paths = asset_paths(repo_dir, branch_dir)
    manifest = touch_manifest(
        paths,
        repo_root,
        branch_name,
        branch_key,
        storage_root,
        repo_key,
        extra={"last_seen_head": args.commit or get_head_commit(repo_root)},
    )
    touch_repo_manifest(
        paths,
        repo_root,
        branch_name,
        storage_root,
        repo_key,
        extra={"updated_at": manifest["updated_at"], "last_seen_head": manifest["last_seen_head"]},
    )

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
                "last_seen_head": manifest["last_seen_head"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="Sync repo+branch development assets at persistence checkpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("sync-working-tree", "record-head", "record-session"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--repo", default=".", help="Path inside the target Git repository")
        sub.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
        sub.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")
        if name == "record-head":
            sub.add_argument("--commit", help="Explicit commit sha to record")
        if name == "record-session":
            sub.add_argument("--summary-file", help="Path to a JSON file containing the session summary payload")
            sub.add_argument("--summary-json", help="Inline JSON session summary payload")

    args = parser.parse_args()
    try:
        if args.command == "sync-working-tree":
            command_sync_working_tree(args)
        elif args.command == "record-session":
            command_record_session(args)
        else:
            command_record_head(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
