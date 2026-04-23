#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(os.environ.get("DEV_ASSETS_HOOK_REPO_ROOT", ".")).expanduser().resolve()
LIB_ROOT = PACKAGE_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from dev_asset_common import (
    AUTO_END,
    AUTO_START,
    PLACEHOLDER_MARKERS,
    asset_paths,
    detect_no_git_mode,
    detect_workspace_mode,
    get_branch_paths,
    list_repos_in_workspace,
)


CONTEXT_SCRIPT = PACKAGE_ROOT / "skills" / "dev-assets-context" / "scripts" / "dev_asset_context.py"
# v2: sync/update merged into capture. All auto-block refresh and
# record-head calls now go through the capture script.
CAPTURE_SCRIPT = PACKAGE_ROOT / "skills" / "dev-assets-capture" / "scripts" / "dev_asset_capture.py"


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
    value = os.environ.get("DEV_ASSETS_PRIMARY_REPO", "").strip()
    return value or None


def strip_managed_markers(text):
    return text.replace(AUTO_START, "").replace(AUTO_END, "").replace("_尚未同步_", "").strip()


def is_placeholder(text):
    stripped = strip_managed_markers(text)
    if not stripped:
        return True
    return any(marker in stripped for marker in PLACEHOLDER_MARKERS)


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
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    lines = [line for line in normalized.splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if not lines[-1].endswith("..."):
            lines.append("...")
    compacted = "\n".join(lines)
    if len(compacted) > max_chars:
        compacted = compacted[: max_chars - 3].rstrip() + "..."
    return compacted


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
        out.append((title, body))
    return out


def _build_context_from_assets(assets, *, full=True, heading=None):
    if not assets["branch_dir"].exists():
        # v2: capture lazy-inits on first write, so branch_dir typically
        # exists after any real interaction. Missing here just means no
        # write has happened yet — no need to push setup.
        if heading is None:
            return (
                "当前仓库+分支还没有 dev-assets 记忆。"
                "下一次 `dev-assets-capture` 写入时会自动 lazy init；现有结论若值得记一笔，直接走 capture。"
            )
        return None

    paths = assets["paths"]
    keys = _FULL_SECTION_KEYS if full else _BRIEF_SECTION_KEYS
    sections = _extract_sections(paths, keys)
    max_lines, max_chars = (8, 700) if full else (3, 200)

    parts = []
    no_git = assets.get("branch_name") is None
    if heading is None:
        if no_git:
            parts.append(
                f"已加载 dev-assets（no-git 模式）：项目 `{assets['repo_key']}`。"
            )
        else:
            parts.append(
                f"已加载 dev-assets：repo `{assets['repo_key']}`，branch `{assets['branch_name']}`。"
            )
        parts.append(f"主存储目录：`{assets['branch_dir']}`")
    else:
        parts.append(heading)
    for title, body in sections:
        if body:
            parts.append(f"{title}:\n{compact_body(body, max_lines=max_lines, max_chars=max_chars)}")
    return "\n\n".join(parts)


def build_session_start_context():
    assets = resolve_assets()
    # no-git mode skips maybe_sync_context() because that path runs git commands
    # (working-tree diffing, focus-area detection) that don't apply here.
    if assets.get("branch_name") is not None:
        try:
            maybe_sync_context()
        except Exception as exc:
            log(f"[dev-assets][SessionStart] refresh skipped: {exc}")
    return _build_context_from_assets(assets, full=True)


def build_context_for_repo(repo_path, *, full=True, is_primary=False):
    """Build a per-repo context block for workspace-mode SessionStart injection.
    Returns None when the repo has no initialized branch memory or resolution fails.
    """
    try:
        assets = resolve_assets_for(repo_path)
    except Exception as exc:
        log(f"[dev-assets] resolve failed for {Path(repo_path).name}: {exc}")
        return None
    try:
        sync_context_for(repo_path)
    except Exception as exc:
        log(f"[dev-assets] context sync skipped for {Path(repo_path).name}: {exc}")
    tag = "[PRIMARY] " if is_primary else ""
    heading = (
        f"## {tag}`{Path(repo_path).name}` — repo `{assets['repo_key']}`, branch `{assets['branch_name']}`"
    )
    return _build_context_from_assets(assets, full=full, heading=heading)


def build_workspace_start_context():
    """SessionStart context for workspace mode. Primary repo gets full memory;
    others get a brief overview only. Returns None if no initialized repos.
    """
    repos = list_workspace_repos()
    if not repos:
        return None
    primary = primary_repo_name()
    primary_hit = False
    sections = []
    for repo_path in repos:
        is_primary = (primary is None) or (repo_path.name == primary)
        if is_primary:
            primary_hit = True
        ctx = build_context_for_repo(repo_path, full=is_primary, is_primary=is_primary)
        if ctx:
            sections.append(ctx)
    if not sections:
        return None
    header_parts = [
        f"已加载 dev-assets workspace 模式：共 {len(repos)} 个仓库 @ `{REPO_ROOT}`"
    ]
    if primary:
        status = "命中" if primary_hit else "未在 workspace 中找到,全部按完整模式注入"
        header_parts.append(f"Primary 仓库提示：`{primary}` ({status})")
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
                log(f"[dev-assets] {repo_path.name}: branch memory not initialized, skip")
                continue
            payload = record_head_for(repo_path)
            log(
                f"[dev-assets] {repo_path.name}: recorded HEAD "
                f"{payload.get('last_seen_head')} for {payload.get('branch')}"
            )
            results.append((repo_path.name, payload))
        except Exception as exc:
            log(f"[dev-assets] {repo_path.name}: record-head skipped: {exc}")
    return results


def sync_working_tree_all_repos():
    """PreCompact hook helper for workspace mode. Iterates all repos."""
    results = []
    for repo_path in list_workspace_repos():
        try:
            assets = resolve_assets_for(repo_path)
            if not assets["branch_dir"].exists():
                log(f"[dev-assets] {repo_path.name}: branch memory not initialized, skip")
                continue
            payload = sync_working_tree_for(repo_path)
            log(
                f"[dev-assets] {repo_path.name}: refreshed working-tree navigation for "
                f"{payload.get('branch')} ({payload.get('files_considered')} files)"
            )
            results.append((repo_path.name, payload))
        except Exception as exc:
            log(f"[dev-assets] {repo_path.name}: working-tree sync skipped: {exc}")
    return results
