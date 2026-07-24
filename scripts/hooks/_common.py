#!/usr/bin/env python3

import json
import hashlib
import os
import re
import shlex
import subprocess
import sys
import time
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
    now_iso,
)
from dev_memory_summary import extract_core_payload


CONTEXT_SCRIPT = PACKAGE_ROOT / "lib" / "dev_memory_context.py"
# v2: sync/update merged into capture. All auto-block refresh and
# record-head calls now go through the capture script.
CAPTURE_SCRIPT = PACKAGE_ROOT / "lib" / "dev_memory_capture.py"
SUMMARY_WORKER_SCRIPT = PACKAGE_ROOT / "scripts" / "hooks" / "session_summary_worker.py"
DEFAULT_CONFIG_PATH = Path(os.environ.get("DEV_MEMORY_CONFIG_PATH", "~/.dev-memory/config.json")).expanduser()
DEV_MEMORY_HOME = Path(os.environ.get("DEV_MEMORY_HOME", "~/.dev-memory")).expanduser()
WORKSPACE_CONFIG_NAMES = (".dev-memory-workspace.json", ".dev-assets-workspace.json")


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


def load_dev_memory_config():
    try:
        if not DEFAULT_CONFIG_PATH.exists():
            return {}
        data = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def session_summary_config():
    config = load_dev_memory_config()
    section = config.get("session_summary")
    return section if isinstance(section, dict) else {}


def session_summary_command():
    # Env remains a deliberate override for one-off debugging, but hooks should
    # normally use ~/.dev-memory/config.json so hook templates stay portable.
    env_command = os.environ.get("DEV_MEMORY_SESSION_SUMMARY_CMD", "").strip()
    if env_command:
        return env_command
    command = session_summary_config().get("command")
    return command.strip() if isinstance(command, str) else ""


def session_summary_max_attempts():
    env_value = os.environ.get("DEV_MEMORY_SESSION_SUMMARY_MAX_ATTEMPTS", "").strip()
    if env_value:
        return env_value
    value = session_summary_config().get("max_attempts", 3)
    try:
        return str(max(1, int(value)))
    except Exception:
        return "3"


def read_hook_input():
    """Best-effort lifecycle hook input reader.

    Claude/Codex pass hook metadata on stdin when running under their hook
    runtime. Manual terminal invocations have a TTY stdin; don't block there.
    """
    try:
        if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:
        return {"raw": raw[:4000]}


def register_session_scan_candidate(hook_input):
    """Persist a lightweight Codex Stop hint without starting summarization."""
    if not isinstance(hook_input, dict):
        return None
    session_id = hook_session_id(hook_input)
    transcript_path = _hook_transcript_path(hook_input)
    if not session_id and not transcript_path:
        return None
    source = session_id or transcript_path
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:20]
    path = DEV_MEMORY_HOME / "jobs" / "session-scan" / "candidates" / f"{digest}.json"
    _atomic_write_json(path, {
        "schema_version": 1,
        "event": "Stop",
        "registered_at": now_iso(),
        "session_id": session_id,
        "transcript_path": transcript_path,
        "transcript_state": _transcript_state(transcript_path),
        "cwd": _first_string(hook_input.get("cwd"), str(REPO_ROOT)),
    })
    return path


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
    """Basename of the focus repo. Env is a one-off override; otherwise use
    workspace-local config so different model workspaces can choose different
    primary repos under the same global hook install.
    """
    value = (
        os.environ.get("DEV_MEMORY_PRIMARY_REPO", "").strip()
        or os.environ.get("DEV_ASSETS_PRIMARY_REPO", "").strip()
    )
    if value:
        return value
    for name in WORKSPACE_CONFIG_NAMES:
        config_path = REPO_ROOT / name
        try:
            if not config_path.exists():
                continue
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            configured = data.get("primary_repo") or data.get("primaryRepo")
            if isinstance(configured, str) and configured.strip():
                return configured.strip()
        except Exception as exc:
            log(f"[dev-memory] workspace config ignored ({config_path}): {exc}")
    return None


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


def extract_repo_file_body(path, *, newest_first=False):
    """Extract full body of a repo-level file, skipping the H1 title and
    ``## 仓库`` metadata section. Placeholder-only sections within the file
    are dropped individually so that real content in sibling sections is
    preserved. Returns None when nothing substantive remains."""
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"^## 仓库\n\n.*?(?=^## |\Z)", "", content, count=1,
                     flags=re.MULTILINE | re.DOTALL)
    content = re.sub(r"^# .+\n*", "", content, count=1)
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)
    kept = []
    for sec in sections:
        sec_body = strip_managed_markers(sec).strip()
        if sec_body and not is_placeholder(sec_body):
            if newest_first and sec_body.startswith("## "):
                heading, separator, body = sec_body.partition("\n")
                if separator and body.strip():
                    blocks = _split_recent_blocks(body)
                    sec_body = heading + "\n\n" + "\n\n".join(blocks)
            kept.append(sec_body)
    body = "\n\n".join(kept).strip()
    return body or None


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


def _split_recent_blocks(text):
    """Split accumulated markdown into entries and return newest first.

    Capture writes append-mode entries separated by blank lines. Older v2 files
    may only be plain bullet lists, so fall back to top-level bullet boundaries.
    """
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []

    paragraph_blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", normalized)
        if block.strip()
    ]
    if len(paragraph_blocks) > 1:
        return list(reversed(paragraph_blocks))

    blocks = []
    current = []
    for line in normalized.splitlines():
        if re.match(r"^\s*[-*]\s+", line) and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return list(reversed(blocks))


def compact_recent_body(text, max_lines=8, max_chars=700):
    blocks = _split_recent_blocks(text)
    if not blocks:
        return compact_body(text, max_lines=max_lines, max_chars=max_chars)

    selected = []
    selected_lines = 0
    truncated = False
    for block in blocks:
        block_lines = [line for line in block.splitlines() if line.strip()]
        if not block_lines:
            continue
        if selected and selected_lines + len(block_lines) > max_lines:
            truncated = True
            break
        if not selected and len(block_lines) > max_lines:
            selected.append("\n".join(block_lines[:max_lines]))
            selected_lines = max_lines
            truncated = True
            break
        selected.append(block)
        selected_lines += len(block_lines)

    compacted = "\n\n".join(selected).strip()
    if len(blocks) > len(selected):
        truncated = True
    if len(compacted) > max_chars:
        compacted = compacted[: max_chars - 3].rstrip() + "..."
        truncated = True
    elif truncated and compacted and not compacted.endswith("..."):
        compacted += "\n..."
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
    ("progress", "功能文件索引"),
    ("progress", "建议优先查看的目录"),
    ("repo_decisions", None),
    ("repo_glossary", None),
    ("glossary", "分支源资料入口"),
    ("overview", "当前目标"),
    ("overview", "范围边界"),
    ("overview", "关键约束"),
    ("repo_overview", None),
    ("glossary", "当前有效上下文"),
    ("risks", "阻塞与注意点"),
    ("decisions", "关键决策与原因"),
    ("risks", "后续继续前要注意"),
)

_RECENT_FIRST_SECTIONS = {
    ("decisions", "关键决策与原因"),
    ("risks", "阻塞与注意点"),
    ("risks", "后续继续前要注意"),
    ("glossary", "当前有效上下文"),
    ("glossary", "分支源资料入口"),
}

_BRANCH_REFERENCE_SECTIONS = {
    ("glossary", "当前有效上下文"),
    ("glossary", "分支源资料入口"),
    ("progress", "功能文件索引"),
}
_BRANCH_REF_MAX_LINES = 128
_BRANCH_REF_MAX_CHARS = 12000

_REPO_LEVEL_KEYS = {
    "repo_overview",
    "repo_decisions",
    "repo_glossary",
    "repo_log",
}
_REPO_DISPLAY_TITLES = {
    "repo_overview": "仓库概览",
    "repo_decisions": "跨分支通用决策",
    "repo_glossary": "仓库共享术语与入口",
}
_HIGH_PRIORITY_WRAPPERS = {
    ("progress", "功能文件索引"): "file_map",
    ("progress", "建议优先查看的目录"): "read_first_dirs",
    ("repo_decisions", None): "shared_decisions",
    ("repo_glossary", None): "long_term_context",
}
_REPO_MAX_LINES = 32
_REPO_MAX_CHARS = 3000

_BRIEF_PROFILES = {
    "expanded": {
        ("progress", "功能文件索引"): (128, 12000, False),
        ("progress", "建议优先查看的目录"): (128, 12000, False),
        ("repo_decisions", None): (32, 3000, True),
        ("repo_glossary", None): (32, 3000, True),
    },
    "standard": {
        ("progress", "功能文件索引"): (16, 1600, True),
        ("progress", "建议优先查看的目录"): (8, 800, False),
        ("repo_decisions", None): (8, 900, True),
        ("repo_glossary", None): (8, 900, True),
    },
    "minimal": {
        ("progress", "功能文件索引"): (5, 600, True),
        ("progress", "建议优先查看的目录"): (5, 600, False),
    },
}


def brief_profile_for_repo_count(repo_count):
    if repo_count <= 2:
        return "expanded"
    if repo_count <= 5:
        return "standard"
    return "minimal"


def _brief_section_keys(profile):
    return tuple(_BRIEF_PROFILES.get(profile, _BRIEF_PROFILES["standard"]).keys())


def _extract_sections(paths, keys, *, repo_newest_first=False):
    out = []
    for file_key, title in keys:
        if title is None:
            body = extract_repo_file_body(
                paths[file_key],
                newest_first=repo_newest_first and file_key in {"repo_decisions", "repo_glossary"},
            )
            display_title = _REPO_DISPLAY_TITLES.get(file_key, paths[file_key].stem)
        else:
            body = extract_section(paths[file_key], title)
            display_title = title
        wrapper = _HIGH_PRIORITY_WRAPPERS.get((file_key, title))
        out.append((display_title, body, file_key, title, wrapper))
    return out


def _build_context_from_assets(assets, *, full=True, heading=None, brief_profile="standard"):
    if not assets["branch_dir"].exists():
        # Memory is opt-in. A missing branch directory stays silent in
        # workspace briefs; the primary repo gets a concise init hint.
        if heading is None:
            return (
                "当前仓库+分支还没有 dev-memory 记忆。"
                "需要启用时运行 `dev-memory-cli init`；之后由定期 session-scan 沉淀会话语义。"
            )
        return None

    paths = assets["paths"]
    keys = _FULL_SECTION_KEYS if full else _brief_section_keys(brief_profile)
    sections = _extract_sections(paths, keys, repo_newest_first=full)
    max_lines, max_chars = (8, 700) if full else (3, 200)
    brief_limits = _BRIEF_PROFILES.get(brief_profile, _BRIEF_PROFILES["standard"])

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
    for title, body, file_key, source_title, wrapper in sections:
        if not body:
            continue
        if not full and (file_key, source_title) in brief_limits:
            eff_lines, eff_chars, prefer_recent = brief_limits[(file_key, source_title)]
        elif full and file_key in _REPO_LEVEL_KEYS:
            eff_lines, eff_chars = _REPO_MAX_LINES, _REPO_MAX_CHARS
            prefer_recent = (file_key, source_title) in _RECENT_FIRST_SECTIONS
        elif full and (file_key, source_title) in _BRANCH_REFERENCE_SECTIONS:
            eff_lines, eff_chars = _BRANCH_REF_MAX_LINES, _BRANCH_REF_MAX_CHARS
            prefer_recent = (file_key, source_title) in _RECENT_FIRST_SECTIONS
        else:
            eff_lines, eff_chars = max_lines, max_chars
            prefer_recent = (file_key, source_title) in _RECENT_FIRST_SECTIONS
        if prefer_recent:
            compacted, truncated = compact_recent_body(body, max_lines=eff_lines, max_chars=eff_chars)
        else:
            compacted, truncated = compact_body(body, max_lines=eff_lines, max_chars=eff_chars)
        if wrapper:
            block = f"<{wrapper}>\n{compacted}\n</{wrapper}>"
        elif full and file_key in _REPO_LEVEL_KEYS:
            block = compacted
        else:
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
        footer_lines.extend(["", "新增会话语义由定期 session-scan 沉淀；主动查旧记忆使用 `dev-memory-read`。"])
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


def build_context_for_repo(repo_path, *, full=True, is_primary=False, brief_profile="standard"):
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
    return _build_context_from_assets(assets, full=full, heading=heading, brief_profile=brief_profile)


def build_workspace_start_context():
    """SessionStart context for workspace mode. Primary repo gets full memory;
    others get a dynamically compacted brief context. Returns None if no
    initialized repos.

    Fallback when no env/workspace-local primary is set:
      - Single-repo workspace → that repo is full (user's intent is obvious).
      - Multi-repo workspace  → all brief, so N full dumps can't drown the
        session. Header tells the agent how to promote one to full.
    """
    repos = list_workspace_repos()
    if not repos:
        return None
    primary = primary_repo_name()
    only_one_repo = len(repos) == 1
    brief_profile = brief_profile_for_repo_count(len(repos))
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
        ctx = build_context_for_repo(
            repo_path,
            full=is_primary,
            is_primary=is_primary,
            brief_profile=brief_profile,
        )
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
            f"_其它仓库按 brief/{brief_profile} 摘要注入；每个 brief 末尾列出该仓库的完整记忆文件路径，"
            "聚焦时直接 Read 即可（如需 CLI：`dev-memory-cli context show --repo <name>`）。_"
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


def _first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _hook_payload_value(payload, *keys):
    current = payload if isinstance(payload, dict) else {}
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _transcript_hints(transcript_path):
    fmt = "unknown"
    path = transcript_path or ""
    if "/.claude/" in path:
        fmt = "claude-jsonl"
    elif "/.codex/" in path:
        fmt = "codex-jsonl"
    return {
        "format": fmt,
        "core_records": (
            [
                "top-level type=user",
                "top-level type=assistant",
                "assistant message text blocks; skip tool_use/tool_result blocks",
            ]
            if fmt == "claude-jsonl"
            else [
                "type=response_item payload.type=message role=user",
                "type=response_item payload.type=message role=assistant",
                "skip event_msg/reasoning/tool-call records by default",
            ]
            if fmt == "codex-jsonl"
            else [
                "prefer user/assistant message records",
                "skip hook/tool/system records by default",
            ]
        ),
        "tool_records": (
            [
                "top-level type=attachment",
                "assistant content tool_use/tool_result",
                "file-history-snapshot/system metadata",
            ]
            if fmt == "claude-jsonl"
            else [
                "type=event_msg",
                "payload.type ending with tool/call/search/status",
                "reasoning records",
            ]
            if fmt == "codex-jsonl"
            else ["attachments", "tool calls", "system metadata"]
        ),
    }


def _session_job_id(repo_key, branch_name, hook_input):
    transcript_path = _first_string(
        hook_input.get("transcript_path"),
        hook_input.get("transcriptPath"),
        _hook_payload_value(hook_input, "payload", "transcript_path"),
        _hook_payload_value(hook_input, "payload", "transcriptPath"),
    )
    session_id = _first_string(
        hook_input.get("session_id"),
        hook_input.get("sessionId"),
        _hook_payload_value(hook_input, "payload", "session_id"),
        _hook_payload_value(hook_input, "payload", "sessionId"),
    )
    source = session_id or transcript_path or f"unknown-{time.time_ns()}"
    digest = hashlib.sha1(
        f"{repo_key}|{branch_name}|{source}".encode("utf-8")
    ).hexdigest()[:16]
    return digest, session_id, transcript_path


def _atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_queue_event(queue_dir, event):
    queue_dir.mkdir(parents=True, exist_ok=True)
    events_path = queue_dir / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _load_prior_summary_job(queue_dir, job_id):
    for state in ("pending", "done", "skipped", "failed"):
        path = queue_dir / state / f"{job_id}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_path"] = str(path)
                data["_state"] = state
                return data
        except Exception:
            return {"_path": str(path), "_state": state}
    return {}


def hook_session_id(hook_input):
    return _first_string(
        hook_input.get("session_id") if isinstance(hook_input, dict) else None,
        hook_input.get("sessionId") if isinstance(hook_input, dict) else None,
        _hook_payload_value(hook_input, "payload", "session_id"),
        _hook_payload_value(hook_input, "payload", "sessionId"),
    )


def _hook_transcript_path(hook_input):
    return _first_string(
        hook_input.get("transcript_path") if isinstance(hook_input, dict) else None,
        hook_input.get("transcriptPath") if isinstance(hook_input, dict) else None,
        _hook_payload_value(hook_input, "payload", "transcript_path"),
        _hook_payload_value(hook_input, "payload", "transcriptPath"),
    )


def _session_start_source(hook_input):
    return hook_session_id(hook_input) or _hook_transcript_path(hook_input)


def _session_start_marker_path(assets, source):
    branch_key = assets.get("branch_key") or assets.get("branch_name") or "no-branch"
    repo_key = assets.get("repo_key") or Path(assets["repo_dir"]).name
    digest = hashlib.sha1(
        f"{repo_key}|{branch_key}|{source}".encode("utf-8")
    ).hexdigest()[:16]
    return Path(assets["repo_dir"]) / "jobs" / "session-start" / "injected" / f"{digest}.json"


def session_start_already_injected(assets, hook_input):
    source = _session_start_source(hook_input)
    if not source:
        return False
    return _session_start_marker_path(assets, source).exists()


def record_session_start_injected(assets, hook_input):
    source = _session_start_source(hook_input)
    if not source:
        return None
    marker_path = _session_start_marker_path(assets, source)
    payload = {
        "schema_version": 1,
        "event": "SessionStart",
        "repo_root": str(assets.get("repo_root")),
        "repo_key": assets.get("repo_key"),
        "branch": assets.get("branch_name"),
        "branch_key": assets.get("branch_key"),
        "session_id": hook_session_id(hook_input),
        "transcript_path": _hook_transcript_path(hook_input),
        "injected_at": now_iso(),
    }
    _atomic_write_json(marker_path, payload)
    return marker_path


def _transcript_state(transcript_path):
    if not transcript_path:
        return None
    path = Path(transcript_path).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size": stat.st_size,
        "mtime_ms": int(stat.st_mtime * 1000),
    }


def _int_env(name, default):
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def build_summary_input(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    return extract_core_payload(
        job,
        max_messages=_int_env("DEV_MEMORY_SESSION_SUMMARY_MAX_MESSAGES", 0),
        max_message_chars=_int_env("DEV_MEMORY_SESSION_SUMMARY_MAX_MESSAGE_CHARS", 0),
        max_memory_chars=_int_env("DEV_MEMORY_SESSION_SUMMARY_MAX_MEMORY_CHARS", 4000),
        since_size=(
            ((job.get("previous_job") or {}).get("processed") or {}).get("transcript_size", 0)
            if isinstance(job.get("previous_job"), dict)
            else 0
        ),
    )


def _write_summary_input(queue_dir, job_id, summary_input):
    inputs_dir = Path(queue_dir) / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9A-Za-z_-]", "", now_iso())
    path = inputs_dir / f"{job_id}-{stamp}.json"
    _atomic_write_json(path, summary_input)
    return path


def build_summary_prompt(job_path, summary_input=None, summary_input_path=None):
    if summary_input is None:
        summary_input = build_summary_input(job_path)
    summary_input_json = json.dumps(summary_input, ensure_ascii=False, indent=2)
    input_path_line = f"- summary input JSON: {summary_input_path}\n" if summary_input_path else ""
    return f"""DEV_MEMORY_INTERNAL_SESSION_SUMMARY_V1
你是 dev-memory 的后台会话总结 worker。

输入：
- job JSON: {job_path}
{input_path_line}- 下方 `SUMMARY_INPUT_JSON` 是 hook 已经确定性提取和拼接好的材料。

禁止事项：
- 不要调用 `dev-memory-cli summary extract-core`。
- 不要自己全量解析 transcript。
- 不要把工具调用流水账写入记忆。

你只需要基于 `SUMMARY_INPUT_JSON` 中的 existing_memory 与 core_messages 判断要写什么：
- existing_memory 是现有 dev-memory 摘要，已读取 progress/risks/decisions/glossary/overview/repo shared 文件。
- core_messages 已过滤掉 hook/tool/system/reasoning，只保留核心 user/assistant 文本。
- 如果 job.previous_processed 存在，用 job.transcript_state.size/mtime_ms 和 previous_processed 的 cursor 判断增量，避免同一会话多次 resume/end 后重复全量总结。

transcript 过滤（extract-core 已执行；这里是核对规则）：
- Claude jsonl：关注顶层 type=user / type=assistant 的文本消息；忽略 attachment、hook 输出、system、file-history-snapshot；assistant content 里忽略 tool_use/tool_result。
- Codex jsonl：关注 type=response_item 且 payload.type=message 且 role=user/assistant；忽略 event_msg、reasoning、tool/function call、hook/status/progress 事件。
- 工具调用细节通常不写入记忆，除非工具输出暴露了稳定结论、失败根因、重要命令或用户显式要求保留。

写入原则：
- 先结合现有记忆判断每条信息是新增、改写、删除/归档还是跳过。
- 已完成且不再影响后续工作的状态，不要追加——通过 rewrite-entry/tidy 删除旧条目。
- 旧结论失效时优先 rewrite-entry 或 tidy 删除，不要追加一条相反结论让两条并存。
- decision / risk / glossary 是累计条目，注意避免重复。
- 只在确有新增或更新时写入。没有有效新增时只输出 `skip_reason`，代码会把 job 标记为 skipped。
- 不要写”当前进展”、”下一步”、”当前阶段”等时效性状态——这些天然会过期；记忆应聚焦稳定的决策、约束、上下文和参考信息。

repo 共享层淘汰规则：
- `shared_decisions` / `shared_context` / `shared_sources` 是跨分支长期记忆，不是会话归档，也不是分支任务备忘。
- 只有“以后所有分支都需要优先知道”的规则、背景、入口才写 shared；普通业务细节、一次性调试结论、某分支当前状态写 branch 层或跳过。
- repo 共享层默认只保留最重要的少量条目（约 10-20 条）；新增 shared 前先看 existing_memory，若旧条目已过时、重复、太长，优先输出 `rewrites` / `deletes` 压缩旧内容。
- `shared_decisions` 的 summary 必须是一条短完整规则；除非必要，不要填 reason/impact。不要把“结论/原因/影响范围”拆成三条长期条目。
- `shared_context` / `shared_sources` 每条尽量不超过 1 句；不要把交接说明、模块清单、会议纪要整段塞入 repo 共享层。
- 代码落盘后还会自动对 repo shared section 做确定性修剪：保留最近约 20 条，单条过长会压成首句。因此你应主动写得精炼，避免重要信息被截断。

file_map（功能文件索引）：
- 用途：让后续 agent 听到”改下操作弹窗”就能直接定位文件，不用搜索。
- 每条 label 是业务语义名（页面/弹窗/侧拉/组件/表单等），paths 是对应文件的仓库相对路径。
- 粒度到组件/功能即可，不要写具体逻辑，agent 定位到文件后自己读。
- 增量 merge：读 existing_memory 里已有的功能文件索引，结合本次会话改动判断——新涉及的组件加入、已有条目路径变了就更新、描述不准的修正、分支里已删除的组件移除。
- 输出合并后的**完整映射**（不是增量 diff），代码侧直接覆盖整个 section。
- 没有文件变更或映射无变化时省略此字段。

输出要求：
- 只输出一个 summary-output JSON 对象，不要输出 markdown fence、解释文字或命令。
- summary-output 格式：
  {{
    “title”: “简短标题”,
    “file_map”: [{{“label”: “功能/组件名”, “paths”: [“相对路径”]}}],
    “decisions”: [{{“summary”: “结论”, “reason”: “为什么”, “impact”: “影响范围”}}],
    “risks”: [“风险/坑/阻塞”],
    “glossary”: [“术语/上下文/命令/外部系统入口”],
    “shared_decisions”: [{{“summary”: “短句跨分支规则”}}],
    “shared_context”: [“一句话仓库级长期背景”],
    “shared_sources”: [“一句话仓库级共享入口”],
    “upserts”: [{{“kind”: “overview”, “content”: “覆盖某个 kind”}}],
    “appends”: [{{“kind”: “decision”, “content”: “追加某个 kind”}}],
    “rewrites”: [{{“id”: “decisions::0::2”, “content”: “新条目”, “reason”: “旧结论失效”}}],
    “deletes”: [{{“id”: “risks::0::1”, “reason”: “风险已解除”}}],
    “skip_reason”: “没有新增有效内容”
  }}
- 字段可省略；不要输出空字段。
- 发现旧条目需要改写/删除时，不要追加矛盾条目。优先在 summary-output 的 rewrites/deletes 中表达。
- 不要调用任何 dev-memory-cli 命令；代码会校验 JSON、落盘、处理 dedup，并移动 job。

SUMMARY_INPUT_JSON:
```json
{summary_input_json}
```
"""


def _is_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _check_worker_lock(queue_dir, job_id):
    """Return True if a live worker already holds the lock for this job."""
    lock_path = Path(queue_dir) / "locks" / f"{job_id}.lock"
    if not lock_path.exists():
        return False
    try:
        content = lock_path.read_text(encoding="utf-8").strip()
        pid = int(content)
        if _is_pid_alive(pid):
            return True
        lock_path.unlink(missing_ok=True)
    except (ValueError, OSError):
        lock_path.unlink(missing_ok=True)
    return False


def _is_job_already_terminal(queue_dir, job_id):
    """Return the terminal state if this job is already done/skipped, else None."""
    for state in ("done", "skipped"):
        if (Path(queue_dir) / state / f"{job_id}.json").exists():
            return state
    return None


def maybe_start_summary_agent(job_path, queue_dir=None, job_id=None):
    if os.environ.get("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "").strip():
        return None
    command = session_summary_command()
    if not command:
        return None
    effective_queue_dir = queue_dir or Path(job_path).parent.parent
    effective_job_id = job_id or Path(job_path).stem

    terminal = _is_job_already_terminal(effective_queue_dir, effective_job_id)
    if terminal:
        log(f"[dev-memory] summary agent skipped: job {effective_job_id} already {terminal}")
        return None

    if _check_worker_lock(effective_queue_dir, effective_job_id):
        log(f"[dev-memory] summary agent skipped: worker already running for {effective_job_id}")
        return None

    summary_session_id = f"dev-memory-summary-{effective_job_id}"
    log_path = None
    if queue_dir is not None and job_id:
        runs_dir = Path(queue_dir) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = re.sub(r"[^0-9A-Za-z_-]", "", now_iso())
        log_path = runs_dir / f"{job_id}-{stamp}.log"
    args = [
        "python3",
        str(SUMMARY_WORKER_SCRIPT),
        "--job",
        str(job_path),
        "--queue-dir",
        str(effective_queue_dir),
        "--job-id",
        str(effective_job_id),
        "--agent-command",
        command,
        "--summary-session-id",
        summary_session_id,
        "--max-attempts",
        session_summary_max_attempts(),
    ]
    stdout_target = open(log_path, "ab") if log_path else open(os.devnull, "wb")
    with open(os.devnull, "rb") as stdin, stdout_target as stdout:
        stdout.write(("[dev-memory] command: " + " ".join(shlex.quote(a) for a in args) + "\n\n").encode("utf-8"))
        stdout.flush()
        subprocess.Popen(
            args,
            cwd=str(REPO_ROOT),
            stdin=stdin,
            stdout=stdout,
            stderr=stdout,
            start_new_session=True,
        )
    return {
        "command": command.split()[0] if command.split() else "summary-worker",
        "log_path": str(log_path) if log_path else None,
        "summary_session_id": summary_session_id,
    }


def enqueue_session_summary_job(payload, hook_input, *, event_name="SessionEnd"):
    """Queue a post-session summarization job and return immediately.

    The queue is per repo, under <repo_dir>/jobs/session-summary. Job filenames
    are stable for the same repo+branch+session so repeated hook firings update
    the same pending job instead of producing conflicting work.
    """
    repo_dir = Path(payload["repo_dir"])
    repo_key = payload.get("repo_key") or repo_dir.name
    branch_name = payload.get("branch") or "unknown"
    job_id, session_id, transcript_path = _session_job_id(repo_key, branch_name, hook_input)
    queue_dir = repo_dir / "jobs" / "session-summary"
    pending_dir = queue_dir / "pending"
    job_path = pending_dir / f"{job_id}.json"
    now = now_iso()
    prior = _load_prior_summary_job(queue_dir, job_id)
    prior_processed = prior.get("processed") if isinstance(prior.get("processed"), dict) else None
    job = {
        "schema_version": 1,
        "job_id": job_id,
        "status": "pending",
        "event": event_name,
        "created_at": prior.get("created_at") or now,
        "updated_at": now,
        "attempts": prior.get("attempts", 0),
        "repo_root": payload.get("repo_root"),
        "repo_key": repo_key,
        "branch": branch_name,
        "storage_root": payload.get("storage_root"),
        "repo_dir": str(repo_dir),
        "branch_dir": payload.get("branch_dir"),
        "last_seen_head": payload.get("last_seen_head"),
        "session_id": session_id,
        "transcript_path": transcript_path,
        "transcript_state": _transcript_state(transcript_path),
        "hook_input_keys": sorted(hook_input.keys()) if isinstance(hook_input, dict) else [],
        "transcript_hints": _transcript_hints(transcript_path),
        "previous_job": (
            {
                "state": prior.get("_state"),
                "path": prior.get("_path"),
                "updated_at": prior.get("updated_at"),
                "processed": prior_processed,
            }
            if prior
            else None
        ),
        "debounce": {
            "stable_after_seconds": 10,
            "same_session_updates_same_job": True,
            "resume_end_updates_same_job": True,
        },
    }
    _atomic_write_json(job_path, job)
    started = None
    try:
        started = maybe_start_summary_agent(job_path, queue_dir=queue_dir, job_id=job_id)
    except Exception as exc:
        log(f"[dev-memory][{event_name}] summary agent launch skipped: {exc}")
    _append_queue_event(queue_dir, {
        "at": now,
        "event": "queued",
        "job_id": job_id,
        "repo_key": repo_key,
        "branch": branch_name,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "job_path": str(job_path),
        "agent_started": started.get("command") if isinstance(started, dict) else started,
        "agent_log": started.get("log_path") if isinstance(started, dict) else None,
        "summary_session_id": started.get("summary_session_id") if isinstance(started, dict) else None,
    })
    return {
        "job_id": job_id,
        "job_path": str(job_path),
        "agent_started": started.get("command") if isinstance(started, dict) else started,
        "agent_log": started.get("log_path") if isinstance(started, dict) else None,
        "summary_session_id": started.get("summary_session_id") if isinstance(started, dict) else None,
    }


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
