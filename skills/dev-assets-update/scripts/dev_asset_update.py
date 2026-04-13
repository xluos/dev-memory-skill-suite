#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from dev_asset_common import (
    asset_paths,
    get_branch_paths,
    list_missing_docs,
    now_iso,
    read_json,
    upsert_development_section,
    upsert_markdown_section,
    write_json,
)


KIND_MAP = {
    "summary": {
        "targets": [
            {"file": "overview", "section": "当前摘要"},
            {"file": "context", "section": "当前有效上下文"},
        ],
        "reason": "高层摘要进入 overview，稍详细的可复用背景进入 context。",
    },
    "overview": {
        "targets": [{"file": "overview", "section": "当前摘要"}],
        "reason": "直接改写 overview 的当前摘要。",
    },
    "scope": {
        "targets": [{"file": "overview", "section": "范围边界"}],
        "reason": "范围边界应直接覆盖 overview，而不是继续追加历史。",
    },
    "stage": {
        "targets": [{"file": "overview", "section": "当前阶段"}],
        "reason": "当前阶段是冷启动先看的信息，保持短且始终最新。",
    },
    "constraint": {
        "targets": [
            {"file": "overview", "section": "关键约束"},
            {"file": "context", "section": "关键决策与原因"},
        ],
        "reason": "强约束既要让冷启动能看到，也要在 context 里保留原因。",
    },
    "development": {
        "targets": [{"file": "development", "section": "当前进展"}],
        "reason": "当前进展是工作态信息，应直接改写 development。",
    },
    "risk": {
        "targets": [
            {"file": "development", "section": "阻塞与注意点"},
            {"file": "context", "section": "后续继续前要注意"},
        ],
        "reason": "当前风险先进入 development，长期注意点同步到 context。",
    },
    "next": {
        "targets": [{"file": "development", "section": "下一步"}],
        "reason": "下一步是当前工作态信息，保持覆盖式更新。",
    },
    "context": {
        "targets": [{"file": "context", "section": "当前有效上下文"}],
        "reason": "稍详细但仍有效的分支记忆统一进入 context。",
    },
    "shared-overview": {
        "targets": [{"file": "repo_overview", "section": "长期目标与边界"}],
        "reason": "跨分支稳定成立的仓库级目标和边界进入 repo overview。",
    },
    "shared-constraint": {
        "targets": [{"file": "repo_overview", "section": "仓库级关键约束"}],
        "reason": "跨分支稳定成立的约束进入 repo overview，而不是重复散落在多个分支里。",
    },
    "shared-context": {
        "targets": [{"file": "repo_context", "section": "长期有效背景"}],
        "reason": "仓库级长期背景进入 repo context。",
    },
    "shared-decision": {
        "targets": [{"file": "repo_context", "section": "跨分支通用决策"}],
        "reason": "跨分支通用的决策与原因进入 repo context。",
    },
    "decision": {
        "targets": [{"file": "context", "section": "关键决策与原因"}],
        "reason": "为什么这么做比做了什么更适合留在 context。",
    },
    "shared-source": {
        "targets": [{"file": "repo_sources", "section": "共享入口"}],
        "reason": "仓库级共享资料入口进入 repo sources。",
    },
    "source": {
        "targets": [{"file": "repo_sources", "section": "共享入口"}],
        "reason": "源文档入口默认进入仓库共享 sources，避免同一仓库的分支重复维护同一组资料入口。",
    },
    "link": {
        "targets": [{"file": "repo_sources", "section": "共享入口"}],
        "reason": "链接和文档入口默认进入仓库共享 sources。",
    },
    "prd": {
        "targets": [
            {"file": "context", "section": "当前有效上下文"},
            {"file": "repo_sources", "section": "共享入口"},
        ],
        "reason": "PRD 正文应回源文档，分支资产保留摘要，入口默认沉淀到仓库共享 sources。",
    },
    "review": {
        "targets": [
            {"file": "context", "section": "关键决策与原因"},
            {"file": "repo_sources", "section": "共享入口"},
        ],
        "reason": "评审结论保留为当前有效结论，并把源资料入口记到仓库共享 sources。",
    },
    "frontend": {
        "targets": [{"file": "context", "section": "当前有效上下文"}],
        "reason": "前端细节应回源方案，分支资产只保留当前有效摘要。",
    },
    "backend": {
        "targets": [{"file": "context", "section": "当前有效上下文"}],
        "reason": "后端细节应回源方案，分支资产只保留当前有效摘要。",
    },
    "test": {
        "targets": [{"file": "context", "section": "后续继续前要注意"}],
        "reason": "测试口径主要作为后续继续时的注意项保留。",
    },
}


def normalize_body(text):
    stripped = text.strip()
    if not stripped:
        raise RuntimeError("content is empty")
    return stripped


def load_optional_text(value, file_path=None):
    if value:
        return normalize_body(value)
    if file_path:
        return normalize_body(Path(file_path).read_text(encoding="utf-8"))
    return None


def load_content(args):
    inline_content = load_optional_text(args.content, args.content_file)
    session_summary = load_optional_text(args.summary, args.summary_file)
    user_input = load_optional_text(args.user_input, args.user_input_file)

    if session_summary or user_input:
        sections = []
        if user_input:
            sections.append("### 用户这次输入\n\n" + user_input)
        if session_summary:
            sections.append("### 基于当前会话整理\n\n" + session_summary)
        if inline_content:
            sections.append("### 补充备注\n\n" + inline_content)
        return "\n\n".join(sections), "session+input"

    if inline_content:
        return inline_content, "content-only"

    raise RuntimeError(
        "one of --content/--content-file or --summary/--summary-file or --user-input/--user-input-file is required"
    )


def resolve_targets(kind):
    payload = KIND_MAP.get(kind)
    if not payload:
        raise RuntimeError(f"unsupported kind: {kind}")
    return payload["targets"]


def write_target(paths, target_file, section_title, content):
    path = paths[target_file]
    if target_file == "development":
        upsert_development_section(path, section_title, content)
    else:
        upsert_markdown_section(path, section_title, content)


def target_label(target_file):
    if target_file.startswith("repo_"):
        return f"repo/{target_file.replace('repo_', '')}.md"
    return f"branch/{target_file}.md"


def command_show(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    paths = asset_paths(repo_dir, branch_dir)
    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "branch_key": branch_key,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "files": {key: str(value) for key, value in paths.items()},
                "missing_or_placeholder": list_missing_docs(paths),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_suggest_target(args):
    kind = args.kind.lower()
    if kind not in KIND_MAP:
        raise RuntimeError(f"unsupported kind: {args.kind}")
    payload = KIND_MAP[kind]
    print(json.dumps({"kind": kind, **payload}, ensure_ascii=False, indent=2))


def command_write(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    kind = args.kind.lower()
    targets = resolve_targets(kind)
    paths = asset_paths(repo_dir, branch_dir)
    content, update_mode = load_content(args)

    touched = []
    for target in targets:
        section_title = (args.title or target["section"]).strip()
        write_target(paths, target["file"], section_title, content)
        touched.append({"file": target_label(target["file"]), "section": section_title})

    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
            "last_update_kind": kind,
            "last_update_mode": update_mode,
            "last_update_targets": touched,
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
                "mode": "write",
                "update_mode": update_mode,
                "kind": kind,
                "touched_targets": touched,
                "updated_at": manifest["updated_at"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_touch_manifest(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        args.repo, args.context_dir, args.branch
    )
    if not branch_dir.exists():
        raise RuntimeError(f"asset directory does not exist: {branch_dir}. Run dev-assets-setup first.")

    paths = asset_paths(repo_dir, branch_dir)
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
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
                "mode": "touch-manifest",
                "updated_at": manifest["updated_at"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description="Update repo+branch development asset documents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show")
    show.add_argument("--repo", default=".", help="Path inside the target Git repository")
    show.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    show.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")

    suggest = subparsers.add_parser("suggest-target")
    suggest.add_argument("--kind", required=True, help="Update kind to classify")

    write = subparsers.add_parser("write")
    write.add_argument("--repo", default=".", help="Path inside the target Git repository")
    write.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    write.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")
    write.add_argument("--kind", required=True, help="Kind of update to write")
    write.add_argument("--title", help="Override the default section title")
    write.add_argument("--content", help="Inline markdown content to store")
    write.add_argument("--content-file", help="File containing markdown content to store")
    write.add_argument("--summary", help="Session-derived summary to store")
    write.add_argument("--summary-file", help="File containing a session-derived summary")
    write.add_argument("--user-input", help="Latest user input to store alongside the summary")
    write.add_argument("--user-input-file", help="File containing the latest user input")

    touch = subparsers.add_parser("touch-manifest")
    touch.add_argument("--repo", default=".", help="Path inside the target Git repository")
    touch.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    touch.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")

    args = parser.parse_args()
    try:
        if args.command == "show":
            command_show(args)
        elif args.command == "suggest-target":
            command_suggest_target(args)
        elif args.command == "write":
            command_write(args)
        else:
            command_touch_manifest(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
