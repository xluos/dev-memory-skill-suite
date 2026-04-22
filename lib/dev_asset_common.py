#!/usr/bin/env python3

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_STORAGE_ROOT = Path.home() / ".dev-assets" / "repos"
DEFAULT_LEGACY_CONTEXT_DIR = ".dev-assets"
AUTO_START = "<!-- AUTO-GENERATED-START -->"
AUTO_END = "<!-- AUTO-GENERATED-END -->"
PLACEHOLDER_MARKERS = ("待补充", "待刷新", "_尚未同步_")
MANAGED_FILES = (
    "manifest.json",
    "overview.md",
    "development.md",
    "context.md",
    "sources.md",
)
FOCUS_PREFIXES = {"skills", "src", "apps", "packages", "services"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_git(args, cwd, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result


def git_stdout(args, cwd, check=True):
    return run_git(args, cwd, check=check).stdout.strip()


def git_lines(args, cwd, check=True):
    result = run_git(args, cwd, check=check)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def detect_repo_root(repo):
    return Path(git_stdout(["rev-parse", "--show-toplevel"], cwd=repo))


def detect_branch(repo_root):
    branch = git_stdout(["branch", "--show-current"], cwd=repo_root)
    if not branch:
        raise RuntimeError("current HEAD is detached; pass --branch explicitly")
    return branch


def sanitize_branch_name(branch_name):
    cleaned = branch_name.strip().replace("/", "__")
    cleaned = cleaned.replace(" ", "-")
    if not cleaned:
        raise ValueError("branch name is empty")
    return cleaned


def sanitize_repo_name(repo_name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name.strip()).strip("-._")
    return cleaned or "repo"


def set_storage_root_config(repo_root, storage_root):
    run_git(["config", "--local", "dev-assets.root", str(storage_root)], cwd=repo_root)


def get_storage_root(repo_root, explicit_value=None):
    if explicit_value:
        return Path(explicit_value).expanduser().resolve()

    env_value = os.environ.get("DEV_ASSETS_ROOT", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve()

    configured = run_git(["config", "--get", "dev-assets.root"], cwd=repo_root, check=False)
    configured_value = configured.stdout.strip()
    if configured_value:
        return Path(configured_value).expanduser().resolve()

    legacy = run_git(["config", "--get", "dev-assets.dir"], cwd=repo_root, check=False)
    legacy_value = legacy.stdout.strip()
    if legacy_value and Path(legacy_value).expanduser().is_absolute():
        return Path(legacy_value).expanduser().resolve()

    return DEFAULT_STORAGE_ROOT.expanduser().resolve()


def get_legacy_context_dir(repo_root):
    configured = run_git(["config", "--get", "dev-assets.dir"], cwd=repo_root, check=False)
    value = configured.stdout.strip()
    return value or DEFAULT_LEGACY_CONTEXT_DIR


def resolve_legacy_branch_dir(base_dir, branch_name, branch_key):
    candidates = [
        base_dir / branch_key,
        base_dir / Path(branch_name),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def normalize_remote_url(remote_url):
    value = remote_url.strip()
    if not value:
        return None

    if value.startswith("git@") and ":" in value:
        host_part, repo_part = value.split(":", 1)
        host = host_part.split("@", 1)[1].lower()
        normalized = f"{host}/{repo_part}"
    elif "://" in value:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        repo_part = parsed.path.lstrip("/")
        normalized = f"{host}/{repo_part}" if host else value
    else:
        normalized = value

    normalized = normalized.replace("\\", "/").rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def detect_repo_identity(repo_root):
    remote = run_git(["remote", "get-url", "origin"], cwd=repo_root, check=False).stdout.strip()
    if remote:
        identity = normalize_remote_url(remote)
        source = "origin"
    else:
        identity = repo_root.resolve().as_posix()
        source = "path"

    repo_slug = sanitize_repo_name(Path(identity).name or repo_root.name)
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return {
        "repo_identity": identity,
        "repo_identity_source": source,
        "repo_key": f"{repo_slug}-{digest}",
    }


def _resolve_workspace_repo(repo):
    """If `repo` points to a workspace root (cwd is not a git repo, but first-level
    subdirs are), redirect to the primary repo via `DEV_ASSETS_PRIMARY_REPO` env.
    Single-repo case returns `repo` unchanged. Raises in workspace mode when
    primary is unset or does not match an existing subdir, so callers see a
    clear error instead of git failing later.
    """
    if not detect_workspace_mode(repo):
        return repo
    primary = os.environ.get("DEV_ASSETS_PRIMARY_REPO", "").strip()
    repos_in_ws = list_repos_in_workspace(repo)
    names = [p.name for p in repos_in_ws]
    if not primary:
        raise RuntimeError(
            f"workspace mode detected at '{repo}': pass --repo <basename> explicitly "
            f"(one of: {names}) or set DEV_ASSETS_PRIMARY_REPO env."
        )
    match = next((p for p in repos_in_ws if p.name == primary), None)
    if match is None:
        raise RuntimeError(
            f"workspace mode: DEV_ASSETS_PRIMARY_REPO='{primary}' not found in '{repo}'. "
            f"Available: {names}."
        )
    return str(match)


def get_branch_paths(repo, context_dir=None, branch=None):
    repo = _resolve_workspace_repo(repo)
    repo_root = detect_repo_root(repo)
    branch_name = branch or detect_branch(repo_root)
    branch_key = sanitize_branch_name(branch_name)
    storage_root = get_storage_root(repo_root, context_dir)
    identity = detect_repo_identity(repo_root)
    repo_dir = storage_root / identity["repo_key"]
    branch_dir = repo_dir / "branches" / branch_key
    return repo_root, branch_name, branch_key, storage_root, identity["repo_key"], repo_dir, branch_dir


def detect_workspace_mode(cwd=None):
    """Return True iff cwd is not inside any git repo yet has first-level
    subdirectories that are git repos. Purely additive — existing single-repo
    behavior (cwd inside a git repo) returns False.
    """
    base = Path(cwd or ".").resolve()
    if not base.exists() or not base.is_dir():
        return False
    probe = run_git(["rev-parse", "--show-toplevel"], cwd=base, check=False)
    if probe.returncode == 0 and probe.stdout.strip():
        return False
    return bool(list_repos_in_workspace(base))


def list_repos_in_workspace(cwd=None):
    """First-level subdirectories of cwd that are git repos. Sorted by name.
    Returns [] when cwd has none or isn't readable. `.git` may be a dir or a
    file (worktree pointer); both count.
    """
    base = Path(cwd or ".").resolve()
    repos = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except (OSError, PermissionError):
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        if (entry / ".git").exists():
            repos.append(entry)
    return repos


def get_all_branch_paths(cwd=None, context_dir=None):
    """Batch variant of get_branch_paths() for every repo under a workspace cwd.
    Repos with detached HEAD or other resolution errors are skipped silently.
    Returns [] when not in workspace mode.
    """
    result = []
    for repo_path in list_repos_in_workspace(cwd):
        try:
            result.append(get_branch_paths(str(repo_path), context_dir=context_dir))
        except Exception:
            continue
    return result


def asset_paths(repo_dir, branch_dir):
    repo_memory_dir = repo_dir / "repo"
    return {
        "repo_manifest": repo_memory_dir / "manifest.json",
        "repo_overview": repo_memory_dir / "overview.md",
        "repo_context": repo_memory_dir / "context.md",
        "repo_sources": repo_memory_dir / "sources.md",
        "repo_artifacts": repo_memory_dir / "artifacts",
        "manifest": branch_dir / "manifest.json",
        "overview": branch_dir / "overview.md",
        "development": branch_dir / "development.md",
        "context": branch_dir / "context.md",
        "sources": branch_dir / "sources.md",
        "artifacts": branch_dir / "artifacts",
        "history": branch_dir / "artifacts" / "history",
    }


def ensure_file(path, content):
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_manifest(path, defaults):
    existing = read_json(path)
    if not existing:
        write_json(path, defaults)
        return defaults

    merged = dict(existing)
    merged.update(defaults)
    merged["initialized_at"] = existing.get("initialized_at", defaults["initialized_at"])
    write_json(path, merged)
    return merged


def render_bullets(items, empty_text="- 待补充", wrap_code=False):
    normalized = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not normalized:
        return empty_text
    lines = []
    for item in normalized:
        if wrap_code and not (item.startswith("`") and item.endswith("`")):
            item = f"`{item}`"
        lines.append(f"- {item}")
    return "\n".join(lines)


def render_title_doc(doc_title, sections, intro=None):
    parts = [f"# {doc_title}"]
    if intro:
        parts.extend(["", intro.strip()])
    for title, body in sections:
        parts.extend(["", f"## {title}", "", body.strip()])
    return "\n".join(parts).rstrip() + "\n"


def template_overview(branch_name):
    return render_title_doc(
        "概览",
        [
            ("分支", f"- {branch_name}"),
            ("当前目标", "- 待补充"),
            ("范围边界", "- 待补充"),
            ("当前阶段", "- 待补充"),
            ("关键约束", "- 待补充"),
        ],
    )


def template_development(branch_name):
    return render_title_doc(
        "当前开发状态",
        [
            ("分支", f"- {branch_name}"),
            ("建议优先查看的目录", "- 待刷新"),
            ("当前进展", "- 待补充"),
            ("阻塞与注意点", "- 待补充"),
            ("下一步", "- 待补充"),
            (
                "自动同步区",
                "本区由 `dev-assets-context` 或 `dev-assets-sync` 刷新，请不要手工编辑。\n\n"
                f"{AUTO_START}\n"
                "_尚未同步_\n"
                f"{AUTO_END}",
            ),
        ],
    )


def template_context():
    return render_title_doc(
        "分支上下文",
        [
            ("当前有效上下文", "- 待补充"),
            ("关键决策与原因", "- 待补充"),
            ("后续继续前要注意", "- 待补充"),
        ],
    )


def template_sources():
    return render_title_doc(
        "分支源资料索引",
        [
            ("当前分支优先阅读", "- 待补充"),
            (
                "提交与代码历史",
                "- 需要了解本分支改动时，优先使用 `git log --oneline <base>..HEAD`\n"
                "- 需要查看某次提交细节时，使用 `git show <sha>`",
            ),
        ],
    )


def template_repo_overview(repo_name):
    return render_title_doc(
        "仓库共享概览",
        [
            ("仓库", f"- {repo_name}"),
            ("长期目标与边界", "- 待补充"),
            ("仓库级关键约束", "- 待补充"),
        ],
    )


def template_repo_context():
    return render_title_doc(
        "仓库共享上下文",
        [
            ("长期有效背景", "- 待补充"),
            ("跨分支通用决策", "- 待补充"),
            ("共享注意点", "- 待补充"),
        ],
    )


def template_repo_sources():
    return render_title_doc(
        "仓库共享源资料索引",
        [
            ("共享入口", "- 待补充"),
            (
                "Git 导航",
                "- 查看默认基线与提交历史时，优先使用 `git log --oneline <base>..HEAD`\n"
                "- 查看具体提交细节时，使用 `git show <sha>`",
            ),
        ],
    )


def build_repo_manifest(repo_root, storage_root, repo_key, identity):
    return {
        "schema_version": 3,
        "scope": "repo",
        "storage_mode": "user-home-repo-plus-branch",
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "repo_identity": identity["repo_identity"],
        "repo_identity_source": identity["repo_identity_source"],
        "storage_root": str(storage_root),
        "initialized_at": now_iso(),
        "updated_at": now_iso(),
        "last_seen_branch": None,
        "last_seen_head": None,
        "default_base": None,
    }


def build_branch_manifest(repo_root, branch_name, branch_key, storage_root, repo_key):
    return {
        "schema_version": 3,
        "scope": "branch",
        "storage_mode": "user-home-repo-plus-branch",
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "branch_key": branch_key,
        "storage_root": str(storage_root),
        "initialized_at": now_iso(),
        "updated_at": now_iso(),
        "last_seen_head": None,
        "default_base": None,
        "scope_summary": [],
        "focus_areas": [],
    }


def migrate_legacy_branch_assets(repo_root, branch_name, branch_key, branch_dir):
    legacy_context_dir = get_legacy_context_dir(repo_root)
    if Path(legacy_context_dir).expanduser().is_absolute():
        legacy_root = Path(legacy_context_dir).expanduser().resolve()
    else:
        legacy_root = (repo_root / legacy_context_dir).resolve()

    legacy_branch_dir = resolve_legacy_branch_dir(legacy_root, branch_name, branch_key)
    if not legacy_branch_dir.exists() or not legacy_branch_dir.is_dir():
        return None

    branch_dir.mkdir(parents=True, exist_ok=True)
    migrated = []
    for file_name in MANAGED_FILES:
        source = legacy_branch_dir / file_name
        target = branch_dir / file_name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)
            migrated.append(file_name)

    legacy_history = legacy_branch_dir / "artifacts" / "history"
    target_history = branch_dir / "artifacts" / "history"
    if legacy_history.exists() and not target_history.exists():
        shutil.copytree(legacy_history, target_history, dirs_exist_ok=True)
        migrated.append("artifacts/history")

    return {"legacy_branch_dir": str(legacy_branch_dir), "migrated": migrated} if migrated else None


def initialize_assets(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir):
    repo_memory_dir = repo_dir / "repo"
    repo_memory_dir.mkdir(parents=True, exist_ok=True)
    branch_dir.mkdir(parents=True, exist_ok=True)
    set_storage_root_config(repo_root, storage_root)

    identity = detect_repo_identity(repo_root)
    migration = migrate_legacy_branch_assets(repo_root, branch_name, branch_key, branch_dir)
    paths = asset_paths(repo_dir, branch_dir)
    paths["repo_artifacts"].mkdir(exist_ok=True)
    paths["artifacts"].mkdir(exist_ok=True)
    paths["history"].mkdir(parents=True, exist_ok=True)

    ensure_manifest(paths["repo_manifest"], build_repo_manifest(repo_root, storage_root, repo_key, identity))
    ensure_file(paths["repo_overview"], template_repo_overview(repo_root.name))
    ensure_file(paths["repo_context"], template_repo_context())
    ensure_file(paths["repo_sources"], template_repo_sources())

    ensure_manifest(paths["manifest"], build_branch_manifest(repo_root, branch_name, branch_key, storage_root, repo_key))
    ensure_file(paths["overview"], template_overview(branch_name))
    ensure_file(paths["development"], template_development(branch_name))
    ensure_file(paths["context"], template_context())
    ensure_file(paths["sources"], template_sources())

    if migration:
        branch_manifest = read_json(paths["manifest"])
        branch_manifest["legacy_migration"] = migration
        branch_manifest["updated_at"] = now_iso()
        write_json(paths["manifest"], branch_manifest)

    return paths


def detect_default_base(repo_root):
    symbolic = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_root, check=False)
    ref = symbolic.stdout.strip()
    if symbolic.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)
    for candidate in ("origin/main", "origin/master"):
        probe = run_git(["rev-parse", "--verify", candidate], cwd=repo_root, check=False)
        if probe.returncode == 0:
            return candidate
    return None


def top_level_scope(path_str):
    parts = Path(path_str).parts
    return parts[0] if parts else "."


def focus_area(path_str):
    parts = Path(path_str).parts
    if not parts:
        return "."
    if parts[0] in FOCUS_PREFIXES and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def summarize_scopes(paths):
    counter = Counter(top_level_scope(path) for path in paths)
    return [{"scope": scope, "files": count} for scope, count in sorted(counter.items())]


def summarize_focus_areas(paths, limit=5):
    counter = Counter(focus_area(path) for path in paths)
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [scope for scope, _ in ranked[:limit]]


def collect_git_facts(repo_root, branch_name, _storage_root=None):
    working_tree_files = git_lines(["diff", "--name-only"], cwd=repo_root)
    staged_files = git_lines(["diff", "--cached", "--name-only"], cwd=repo_root)
    untracked_files = git_lines(["ls-files", "--others", "--exclude-standard"], cwd=repo_root)

    default_base = detect_default_base(repo_root)
    since_base_files = []
    if default_base:
        merge_base = git_lines(["merge-base", "HEAD", default_base], cwd=repo_root)
        if merge_base:
            since_base_files = git_lines(["diff", "--name-only", f"{merge_base[0]}...HEAD"], cwd=repo_root)

    all_paths = sorted(set(working_tree_files + staged_files + untracked_files + since_base_files))
    return {
        "branch": branch_name,
        "default_base": default_base,
        "last_seen_head": get_head_commit(repo_root),
        "working_tree_files": working_tree_files,
        "staged_files": staged_files,
        "untracked_files": untracked_files,
        "since_base_files": since_base_files,
        "scope_summary": summarize_scopes(all_paths),
        "focus_areas": summarize_focus_areas(all_paths),
        "updated_at": now_iso(),
    }


def build_auto_block(facts):
    base_line = facts["default_base"] or "未检测到 origin/HEAD"
    head_line = facts["last_seen_head"] or "尚未检测到 HEAD"
    focus_lines = render_bullets(facts["focus_areas"], empty_text="- 当前未检测到改动目录", wrap_code=True)
    scope_lines = render_bullets(
        [f"{item['scope']} ({item['files']} files)" for item in facts["scope_summary"]],
        empty_text="- 当前未检测到改动范围",
    )
    history_hint = (
        f"- `git log --oneline {facts['default_base']}..HEAD`"
        if facts["default_base"]
        else "- `git log --oneline --decorate -n 20`"
    )
    return (
        "### 自动生成\n\n"
        f"- 更新时间: {facts['updated_at']}\n"
        f"- 当前分支: {facts['branch']}\n"
        f"- 默认基线分支: {base_line}\n"
        f"- 当前 HEAD: {head_line}\n\n"
        "#### 建议优先查看的目录\n\n"
        f"{focus_lines}\n\n"
        "#### 顶层改动范围\n\n"
        f"{scope_lines}\n\n"
        "#### 按需查看提交历史\n\n"
        f"{history_hint}\n"
        "- `git diff --name-only`\n"
    )


def ensure_development_auto_block(path):
    content = path.read_text(encoding="utf-8")
    if AUTO_START in content and AUTO_END in content:
        return content

    marker = "## 自动同步区"
    auto_section = (
        f"\n\n{marker}\n\n"
        "本区由 `dev-assets-context` 或 `dev-assets-sync` 刷新，请不要手工编辑。\n\n"
        f"{AUTO_START}\n"
        "_尚未同步_\n"
        f"{AUTO_END}\n"
    )

    if marker in content:
        before, _ = content.split(marker, 1)
        updated = before.rstrip() + auto_section
    else:
        updated = content.rstrip() + auto_section

    path.write_text(updated + ("" if updated.endswith("\n") else "\n"), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def replace_auto_block(content, replacement):
    if AUTO_START not in content or AUTO_END not in content:
        raise RuntimeError("development.md is missing auto-generated markers")
    before, remainder = content.split(AUTO_START, 1)
    _, after = remainder.split(AUTO_END, 1)
    return f"{before}{AUTO_START}\n{replacement.rstrip()}\n{AUTO_END}{after}"


def split_sections(content):
    positions = list(re.finditer(r"^## (.+?)\n", content, re.M))
    if not positions:
        return content.rstrip(), []

    prefix = content[: positions[0].start()].rstrip()
    sections = []
    for index, match in enumerate(positions):
        end = positions[index + 1].start() if index + 1 < len(positions) else len(content)
        title = match.group(1).strip()
        body = content[match.end() : end].strip()
        sections.append((title, body))
    return prefix, sections


def join_sections(prefix, sections):
    parts = []
    prefix = prefix.rstrip()
    if prefix:
        parts.append(prefix)
    for title, body in sections:
        parts.append(f"## {title}\n\n{body.strip()}")
    return "\n\n".join(parts).rstrip() + "\n"


def upsert_markdown_section(path, title, body):
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix, sections = split_sections(content)
    target = title.strip()
    updated = []
    replaced = False
    for existing_title, existing_body in sections:
        if existing_title.strip() == target:
            if not replaced:
                updated.append((title, body))
                replaced = True
            # 同 title 的多余 section 直接丢弃，顺带根治已有的重复污染。
        else:
            updated.append((existing_title, existing_body))
    if not replaced:
        updated.append((title, body))
    path.write_text(join_sections(prefix, updated), encoding="utf-8")


def upsert_development_section(path, title, body):
    content = ensure_development_auto_block(path)
    marker = "## 自动同步区"
    if marker not in content:
        raise RuntimeError("development.md is missing the auto-sync section heading")
    before, after = content.split(marker, 1)
    prefix, sections = split_sections(before.rstrip())
    target = title.strip()
    updated = []
    replaced = False
    for existing_title, existing_body in sections:
        if existing_title.strip() == target:
            if not replaced:
                updated.append((title, body))
                replaced = True
        else:
            updated.append((existing_title, existing_body))
    if not replaced:
        updated.append((title, body))
    rewritten = join_sections(prefix, updated).rstrip() + "\n\n" + marker + after
    path.write_text(rewritten, encoding="utf-8")


def sync_development(paths, facts):
    upsert_development_section(
        paths["development"],
        "建议优先查看的目录",
        render_bullets(facts["focus_areas"], empty_text="- 当前未检测到改动目录", wrap_code=True),
    )
    current = ensure_development_auto_block(paths["development"])
    updated = replace_auto_block(current, build_auto_block(facts))
    paths["development"].write_text(updated, encoding="utf-8")


def list_missing_docs(paths):
    missing = []
    for key, path in paths.items():
        if key in {"manifest", "repo_manifest", "artifacts", "history", "repo_artifacts"}:
            continue
        if not path.exists():
            missing.append(key)
            continue
        content = path.read_text(encoding="utf-8")
        if any(marker in content for marker in PLACEHOLDER_MARKERS):
            missing.append(key)
    return missing


def get_head_commit(repo_root):
    sha = git_stdout(["rev-parse", "HEAD"], cwd=repo_root, check=False)
    return sha or None
