#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(
    os.environ.get("DEV_MEMORY_HOOK_REPO_ROOT")
    or os.environ.get("DEV_ASSETS_HOOK_REPO_ROOT")
    or "."
).expanduser().resolve()
LIB_ROOT = PACKAGE_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from dev_memory_common import (
    AUTO_END,
    AUTO_START,
    PLACEHOLDER_MARKERS,
    asset_paths,
    detect_no_git_mode,
    detect_workspace_mode,
    get_branch_paths,
    list_repos_in_workspace,
)


CONTEXT_SCRIPT = PACKAGE_ROOT / "lib" / "dev_memory_context.py"
# v2: sync/update merged into capture. All auto-block refresh and
# record-head calls now go through the capture script.
CAPTURE_SCRIPT = PACKAGE_ROOT / "lib" / "dev_memory_capture.py"


def run_python(script_path, *args, cwd=None):
    work_cwd = cwd if cwd is not None else REPO_ROOT
    result = subprocess.run(
        ["python3", str(script_path), *args],
        cwd=work_cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {script_path}")
    return result.stdout.strip()


def log(message):
    print(message, file=sys.stderr)


def resolve_assets_for(repo_root):
    """Resolve asset paths for an explicit repo root (workspace-mode friendly)."""
    repo_root_str = str(repo_root)
    root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(repo_root_str)
    return {
        "repo_root": root,
        "branch_name": branch_name,
        "branch_key": branch_key,
        "storage_root": storage_root,
        "repo_key": repo_key,
        "repo_dir": repo_dir,
        "branch_dir": branch_dir,
        "paths": asset_paths(repo_dir, branch_dir),
    }


def resolve_assets():
    return resolve_assets_for(REPO_ROOT)


def is_workspace_mode():
    return detect_workspace_mode(str(REPO_ROOT))


def is_no_git_mode():
    return detect_no_git_mode(str(REPO_ROOT))


def list_workspace_repos():
    return list_repos_in_workspace(str(REPO_ROOT))


def primary_repo_name():
    """Basename of the focus repo from env; None if unset."""
    value = (
        os.environ.get("DEV_MEMORY_PRIMARY_REPO", "").strip()
        or os.environ.get("DEV_ASSETS_PRIMARY_REPO", "").strip()
    )
    return value or None


def strip_managed_markers(text):
    return text.replace(AUTO_START, "").replace(AUTO_END, "").replace("_尚未同步_", "").strip()


# Sentinels produced by render_bullets/build_auto_block when git introspection
# finds nothing to report. Not added to lib PLACEHOLDER_MARKERS because
# list_missing_docs would then false-flag these as "section missing" — the user
# can't fill them in, they're auto-derived. Filtered only at injection time.
EMPTY_SENTINELS = (
    "当前未检测到改动目录",
    "当前未检测到改动范围",
    "未检测到 origin/HEAD",
    "尚未检测到 HEAD",
)


def is_placeholder(text):
    stripped = strip_managed_markers(text)
    if not stripped:
        return True
    if any(marker in stripped for marker in PLACEHOLDER_MARKERS):
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if lines and all(any(sentinel in line for sentinel in EMPTY_SENTINELS) for line in lines):
        return True
    return False


def extract_section(path, title):
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n\n(.*?)(?=^## |\Z)", content, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return None
    body = strip_managed_markers(match.group(1)).strip()
    return None if is_placeholder(body) else body


def compact_body(text, max_lines=8, max_chars=700):
    """Compact a section body. Returns (compacted_text, was_truncated). The
    caller uses `was_truncated` to decide whether to append a "see full file"
    hint so the AI doesn't mistake the trimmed snippet for the whole story.
    """
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    lines = [line for line in normalized.splitlines() if line.strip()]
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if not lines[-1].endswith("..."):
            lines.append("...")
        truncated = True
    compacted = "\n".join(lines)
    if len(compacted) > max_chars:
        compacted = compacted[: max_chars - 3].rstrip() + "..."
        truncated = True
    return compacted, truncated


def sync_context_for(repo_root):
    return json.loads(
        run_python(CONTEXT_SCRIPT, "sync", "--repo", str(repo_root), cwd=str(repo_root))
    )


def sync_working_tree_for(repo_root):
    return json.loads(
        run_python(CAPTURE_SCRIPT, "sync-working-tree", "--repo", str(repo_root), cwd=str(repo_root))
    )


def record_head_for(repo_root):
    return json.loads(
        run_python(CAPTURE_SCRIPT, "record-head", "--repo", str(repo_root), cwd=str(repo_root))
    )


def maybe_sync_context():
    return sync_context_for(REPO_ROOT)


def maybe_sync_working_tree():
    return sync_working_tree_for(REPO_ROOT)


def maybe_record_head():
    return record_head_for(REPO_ROOT)


# v2 section map: branch files split by domain. progress.md carries
# "建议优先查看的目录", "当前进展", "下一步"; risks.md carries "阻塞与注意点" and
# "后续继续前要注意"; decisions.md carries "关键决策与原因"; glossary.md carries
# "当前有效上下文".
_FULL_SECTION_KEYS = (
    ("overview", "当前目标"),
    ("overview", "范围边界"),
    ("overview", "当前阶段"),
    ("overview", "关键约束"),
    ("progress", "建议优先查看的目录"),
    ("progress", "当前进展"),
    ("risks", "阻塞与注意点"),
    ("progress", "下一步"),
    ("glossary", "当前有效上下文"),
    ("decisions", "关键决策与原因"),
    ("risks", "后续继续前要注意"),
    ("repo_overview", "长期目标与边界"),
    ("repo_overview", "仓库级关键约束"),
    ("repo_glossary", "共享入口"),
)

_BRIEF_SECTION_KEYS = (
    ("overview", "当前目标"),
    ("overview", "当前阶段"),
    ("progress", "当前进展"),
    ("progress", "下一步"),
)


def _extract_sections(paths, keys):
    out = []
    for file_key, title in keys:
        body = extract_section(paths[file_key], title)
        out.append((title, body, file_key))
    return out


def _build_context_from_assets(assets, *, full=True, heading=None):
    if not assets["branch_dir"].exists():
        # v2: capture lazy-inits on first write, so branch_dir typically
        # exists after any real interaction. Missing here just means no
        # write has happened yet — no need to push setup.
        if heading is None:
            return (
                "当前仓库+分支还没有 dev-memory 记忆。"
                "下一次 `dev-memory-capture` 写入时会自动 lazy init；现有结论若值得记一笔，直接走 capture。"
            )
        return None

    paths = assets["paths"]
    keys = _FULL_SECTION_KEYS if full else _BRIEF_SECTION_KEYS
    sections = _extract_sections(paths, keys)
    max_lines, max_chars = (8, 700) if full else (3, 200)

    parts = []
    no_git = assets.get("branch_name") is None
    if heading is not None:
        # Workspace mode: caller passes a per-repo heading (e.g. "## [PRIMARY]
        # repo @ branch") so multi-repo blocks are still distinguishable.
        parts.append(heading)
    elif no_git:
        # No-git has nothing in the footer paths to identify scope by, keep a
        # minimal label.
        parts.append("已加载 dev-memory（no-git 模式）。")
    # Single-repo + git: skip the heading. Branch identity is derivable from
    # the footer's directory header (.../branches/<branch>/).

    any_truncated = False
    for title, body, file_key in sections:
        if not body:
            continue
        compacted, truncated = compact_body(body, max_lines=max_lines, max_chars=max_chars)
        block = f"{title}:\n{compacted}"
        if truncated:
            file_path = paths.get(file_key)
            if file_path is not None:
                # Plain-text anchor (no markdown italic). Filename-only — the
                # absolute prefix lives in the footer's directory header so we
                # avoid printing the same prefix on every truncation.
                block += f"\n↪ 完整: {file_path.name}"
            any_truncated = True
        parts.append(block)

    # Footer: dump the authoritative memory layout so the agent can Read files
    # directly. Replaces the retired dev-memory-context skill. Path layout is
    # "directory header + relative filenames" to keep the footer compact.
    if not no_git:
        archive_root = paths.get("repo_artifacts")
        archive_dir = (
            archive_root.parent.parent / "branches" / "_archived"
            if archive_root is not None
            else None
        )

        branch_specs = (
            ("progress", "hot 层：当前进展 + 下一步 + 自动同步区"),
            ("risks", "hot 层：阻塞 + 后续注意点"),
            ("decisions", "决策背景（为什么这么做）"),
            ("glossary", "术语 + 源资料入口"),
            ("overview", "分支概览（目标 / 范围 / 阶段 / 约束）"),
            ("log", "事件日志（append-only；`grep '^## \\[' log.md | tail -20` 看最近事件）"),
        )
        repo_specs = (
            ("repo_overview", "长期目标 + 跨分支约束"),
            ("repo_decisions", "跨分支通用决策"),
            ("repo_glossary", "长期背景 + 共享入口"),
            ("repo_log", "仓库事件日志（graduate / 共享层 capture）"),
        )

        def _group(specs):
            lines = []
            common_dir = None
            for key, label in specs:
                p = paths.get(key)
                if p is None:
                    continue
                if common_dir is None:
                    common_dir = p.parent
                lines.append(f"- {p.name} — {label}")
            return common_dir, lines

        branch_dir, branch_lines = _group(branch_specs)
        repo_dir, repo_lines = _group(repo_specs)

        footer_lines = ["---"]
        if full:
            opening = (
                "SessionStart 注入的浓缩摘要 — "
                + (
                    "上文标注 ↪ 的段落已截断，详情 Read 对应文件。"
                    if any_truncated
                    else "需要更多细节时直接 Read 下面列出的文件。"
                )
            )
        else:
            opening = "Brief 摘要。本 repo 完整记忆见以下文件："
        footer_lines.append(opening)
        if branch_lines and branch_dir:
            footer_lines.extend(["", f"分支层 `{branch_dir}/`：", *branch_lines])
        if repo_lines and repo_dir:
            footer_lines.extend(["", f"repo 共享层 `{repo_dir}/`：", *repo_lines])
        if archive_dir is not None:
            footer_lines.extend([
                "",
                f"归档分支查询：`grep -r 'KEYWORD' {archive_dir}/` （体量大时派 Task 子 agent）",
            ])
        footer_lines.extend(["", "新决策 / 进展 / 阻塞 → `dev-memory-capture` 写入。"])
        parts.append("\n".join(footer_lines))

    return "\n\n".join(parts)


def build_session_start_context():
    assets = resolve_assets()
    # no-git mode skips maybe_sync_context() because that path runs git commands
    # (working-tree diffing, focus-area detection) that don't apply here.
    if assets.get("branch_name") is not None:
        try:
            maybe_sync_context()
        except Exception as exc:
            log(f"[dev-memory][SessionStart] refresh skipped: {exc}")
    return _build_context_from_assets(assets, full=True)


def build_context_for_repo(repo_path, *, full=True, is_primary=False):
    """Build a per-repo context block for workspace-mode SessionStart injection.
    Returns None when the repo has no initialized branch memory or resolution fails.
    """
    try:
        assets = resolve_assets_for(repo_path)
    except Exception as exc:
        log(f"[dev-memory] resolve failed for {Path(repo_path).name}: {exc}")
        return None
    try:
        sync_context_for(repo_path)
    except Exception as exc:
        log(f"[dev-memory] context sync skipped for {Path(repo_path).name}: {exc}")
    tag = "[PRIMARY] " if is_primary else ""
    heading = (
        f"## {tag}`{Path(repo_path).name}` @ branch `{assets['branch_name']}`"
    )
    return _build_context_from_assets(assets, full=full, heading=heading)


def build_workspace_start_context():
    """SessionStart context for workspace mode. Primary repo gets full memory;
    others get a brief overview only. Returns None if no initialized repos.

    Fallback when DEV_MEMORY_PRIMARY_REPO is unset:
      - Single-repo workspace → that repo is full (user's intent is obvious).
      - Multi-repo workspace  → all brief, so N full dumps can't drown the
        session. Header tells the agent how to promote one to full.
    """
    repos = list_workspace_repos()
    if not repos:
        return None
    primary = primary_repo_name()
    only_one_repo = len(repos) == 1
    primary_hit = False
    has_brief = False
    sections = []
    for repo_path in repos:
        if primary is not None:
            is_primary = (repo_path.name == primary)
        else:
            is_primary = only_one_repo
        if is_primary:
            primary_hit = True
        else:
            has_brief = True
        ctx = build_context_for_repo(repo_path, full=is_primary, is_primary=is_primary)
        if ctx:
            sections.append(ctx)
    if not sections:
        return None
    header_parts = [
        f"已加载 dev-memory workspace 模式：共 {len(repos)} 个仓库 @ `{REPO_ROOT}`"
    ]
    if primary:
        status = "命中" if primary_hit else "未在 workspace 中找到"
        header_parts.append(f"Primary 仓库：`{primary}` ({status})")
    if has_brief:
        header_parts.append(
            "_其它仓库按 brief 摘要注入；每个 brief 末尾列出该仓库的完整记忆文件路径，"
            "聚焦时直接 Read 即可（如需 CLI：`dev-memory context show --repo <name>`）。_"
        )
    header = "\n".join(header_parts)
    return header + "\n\n---\n\n" + "\n\n---\n\n".join(sections)


def record_head_all_repos():
    """Stop/SessionEnd hook helper for workspace mode. Iterates all repos; logs per-repo
    outcome; swallows per-repo failures.
    """
    results = []
    for repo_path in list_workspace_repos():
        try:
            assets = resolve_assets_for(repo_path)
            if not assets["branch_dir"].exists():
                log(f"[dev-memory] {repo_path.name}: branch memory not initialized, skip")
                continue
            payload = record_head_for(repo_path)
            log(
                f"[dev-memory] {repo_path.name}: recorded HEAD "
                f"{payload.get('last_seen_head')} for {payload.get('branch')}"
            )
            results.append((repo_path.name, payload))
        except Exception as exc:
            log(f"[dev-memory] {repo_path.name}: record-head skipped: {exc}")
    return results


def sync_working_tree_all_repos():
    """PreCompact hook helper for workspace mode. Iterates all repos."""
    results = []
    for repo_path in list_workspace_repos():
        try:
            assets = resolve_assets_for(repo_path)
            if not assets["branch_dir"].exists():
                log(f"[dev-memory] {repo_path.name}: branch memory not initialized, skip")
                continue
            payload = sync_working_tree_for(repo_path)
            log(
                f"[dev-memory] {repo_path.name}: refreshed working-tree navigation for "
                f"{payload.get('branch')} ({payload.get('files_considered')} files)"
            )
            results.append((repo_path.name, payload))
        except Exception as exc:
            log(f"[dev-memory] {repo_path.name}: working-tree sync skipped: {exc}")
    return results
