#!/usr/bin/env python3

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_STORAGE_ROOT = Path.home() / ".dev-memory" / "repos"
DEFAULT_LEGACY_CONTEXT_DIR = ".dev-memory"
DEV_MEMORY_ID_FILE = ".dev-memory-id"

# Backward-compat: previous package name was `dev-assets`. We still recognize
# the old storage root, marker file, in-repo dir, and git config keys for one
# release so users can migrate without losing data. Migration script will move
# these forward; until then, reads silently fall through to the legacy paths.
LEGACY_STORAGE_ROOT = Path.home() / ".dev-assets" / "repos"
LEGACY_CONTEXT_DIR = ".dev-assets"
LEGACY_ID_FILE = ".dev-assets-id"
LEGACY_GIT_CONFIG_PREFIX = "dev-assets"
NO_GIT_BRANCH_SENTINEL = "_no_git"
AUTO_START = "<!-- AUTO-GENERATED-START -->"
AUTO_END = "<!-- AUTO-GENERATED-END -->"
PLACEHOLDER_MARKERS = ("待补充", "待刷新", "_尚未同步_")

# New v2 file layout: per-domain files instead of the old
# development/context/sources trio. `overview.md` stays because it's the
# cold-start snapshot (goal/scope/stage/constraints) and has no good home in
# the new four-category split.
MANAGED_FILES = (
    "manifest.json",
    "overview.md",
    "decisions.md",
    "progress.md",
    "risks.md",
    "glossary.md",
    "unsorted.md",
    "pending-promotion.md",
    "log.md",
)

# Legacy v1 files are auto-migrated on first write/read then deleted. The list
# is kept here so list_missing_docs() and other scanners can ignore them.
LEGACY_V1_FILES = ("development.md", "context.md", "sources.md")

# Bottom-up clustering of changed-file paths into a small set of "focus
# directories". The cluster never grows larger than this many entries; a
# higher number gives finer granularity at the cost of a longer hint list.
FOCUS_AREA_LIMIT = 10


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


def detect_git_dir(repo_root):
    return git_stdout(["rev-parse", "--git-dir"], cwd=repo_root, check=False)


def detect_git_common_dir(repo_root):
    return git_stdout(["rev-parse", "--git-common-dir"], cwd=repo_root, check=False)


def is_worktree(repo_root):
    """True iff this checkout is a linked worktree (not the main repo).

    Distinguishes the two by comparing --git-dir (per-worktree, e.g.
    .git/worktrees/<name>) against --git-common-dir (shared, e.g. .git).
    Older git versions print --git-common-dir as '.' when they're equal —
    that's fine, the resolve() below normalizes both.
    """
    gd = detect_git_dir(repo_root)
    gcd = detect_git_common_dir(repo_root)
    if not gd or not gcd:
        return False
    base = Path(repo_root)
    gd_path = Path(gd)
    gcd_path = Path(gcd)
    gd_abs = (gd_path if gd_path.is_absolute() else base / gd_path).resolve()
    gcd_abs = (gcd_path if gcd_path.is_absolute() else base / gcd_path).resolve()
    return gd_abs != gcd_abs


_REFLOG_CREATED_FROM_RE = re.compile(r"branch:\s*Created from\s+(\S+)")


def detect_worktree_base_branch(repo_root, branch_name):
    """Best-effort: which branch was `branch_name` created from?

    Reads the oldest reflog entry of `branch_name` — for `git worktree add -b X
    path Y`, `git checkout -b X Y`, or `git switch -c X Y`, this entry reads
    `branch: Created from Y`. Returns Y if it exists as a local branch ref and
    differs from current; otherwise None (callers should fall back to seeding
    a fresh skeleton).

    Limitations:
    - If the worktree was created without an explicit source (e.g. `worktree
      add -b X path` defaults to HEAD), the reflog shows `Created from HEAD`
      and we return None — we can't reliably resolve which branch HEAD was
      pointing at at creation time.
    - If branch_name has been amended/rebased enough that the oldest reflog
      entry rolled off, we return None.
    """
    result = run_git(
        ["reflog", "show", "--format=%gs", branch_name],
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    oldest = lines[-1]
    m = _REFLOG_CREATED_FROM_RE.search(oldest)
    if not m:
        return None
    src = m.group(1).strip()
    if not src or src == "HEAD" or src == branch_name:
        return None
    verify = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{src}"],
        cwd=repo_root,
        check=False,
    )
    if verify.returncode != 0:
        return None
    return src


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
    desired = str(storage_root)

    def current_value():
        return run_git(
            ["config", "--get", "dev-memory.root"],
            cwd=repo_root,
            check=False,
        ).stdout.strip()

    if current_value() == desired:
        return False

    last_error = ""
    for attempt in range(5):
        result = run_git(
            ["config", "--local", "dev-memory.root", desired],
            cwd=repo_root,
            check=False,
        )
        if result.returncode == 0:
            return True

        last_error = result.stderr.strip() or "git config failed"
        if "config.lock" not in last_error and "could not lock config file" not in last_error:
            raise RuntimeError(last_error)

        if current_value() == desired:
            return False
        time.sleep(0.05 * (attempt + 1))

    if current_value() == desired:
        return False
    raise RuntimeError(last_error)


def get_storage_root(repo_root, explicit_value=None):
    if explicit_value:
        return Path(explicit_value).expanduser().resolve()

    env_value = (
        os.environ.get("DEV_MEMORY_ROOT", "").strip()
        or os.environ.get("DEV_ASSETS_ROOT", "").strip()
    )
    if env_value:
        return Path(env_value).expanduser().resolve()

    for key in ("dev-memory.root", "dev-assets.root"):
        configured = run_git(["config", "--get", key], cwd=repo_root, check=False)
        configured_value = configured.stdout.strip()
        if configured_value:
            return Path(configured_value).expanduser().resolve()

    for key in ("dev-memory.dir", "dev-assets.dir"):
        legacy = run_git(["config", "--get", key], cwd=repo_root, check=False)
        legacy_value = legacy.stdout.strip()
        if legacy_value and Path(legacy_value).expanduser().is_absolute():
            return Path(legacy_value).expanduser().resolve()

    default_root = DEFAULT_STORAGE_ROOT.expanduser().resolve()
    if not default_root.exists():
        legacy_root = LEGACY_STORAGE_ROOT.expanduser().resolve()
        if legacy_root.exists():
            return legacy_root
    return default_root


def get_legacy_context_dir(repo_root):
    for key in ("dev-memory.dir", "dev-assets.dir"):
        configured = run_git(["config", "--get", key], cwd=repo_root, check=False)
        value = configured.stdout.strip()
        if value:
            return value
    return DEFAULT_LEGACY_CONTEXT_DIR


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


def detect_no_git_mode(cwd=None):
    base = Path(cwd or ".").resolve()
    if not base.exists() or not base.is_dir():
        return False
    probe = run_git(["rev-parse", "--show-toplevel"], cwd=base, check=False)
    if probe.returncode == 0 and probe.stdout.strip():
        return False
    if list_repos_in_workspace(base):
        return False
    return True


def read_or_create_dev_memory_id(cwd):
    cwd_path = Path(cwd).resolve()
    id_file = cwd_path / DEV_MEMORY_ID_FILE
    legacy_id_file = cwd_path / LEGACY_ID_FILE
    if not id_file.exists() and legacy_id_file.exists():
        # Use legacy file in-place for one release; migration script renames it.
        id_file = legacy_id_file
    if id_file.exists():
        try:
            payload = json.loads(id_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload.get("id"):
            return payload
        id_file = cwd_path / DEV_MEMORY_ID_FILE
    payload = {
        "id": str(uuid.uuid4()),
        "name": cwd_path.name,
        "created_at": now_iso(),
    }
    id_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def detect_repo_identity_no_git(cwd):
    cwd_path = Path(cwd).resolve()
    payload = read_or_create_dev_memory_id(cwd_path)
    name = sanitize_repo_name(payload.get("name") or cwd_path.name)
    digest = hashlib.sha1(payload["id"].encode("utf-8")).hexdigest()[:12]
    return {
        "repo_identity": f"no-git:{payload['id']}",
        "repo_identity_source": "dev-memory-id",
        "repo_key": f"{name}-{digest}",
    }


def get_no_git_paths(cwd, context_dir=None):
    cwd_path = Path(cwd).resolve()
    if context_dir:
        storage_root = Path(context_dir).expanduser().resolve()
    else:
        env_value = (
            os.environ.get("DEV_MEMORY_ROOT", "").strip()
            or os.environ.get("DEV_ASSETS_ROOT", "").strip()
        )
        if env_value:
            storage_root = Path(env_value).expanduser().resolve()
        else:
            default_root = DEFAULT_STORAGE_ROOT.expanduser().resolve()
            legacy_root = LEGACY_STORAGE_ROOT.expanduser().resolve()
            storage_root = (
                legacy_root if (not default_root.exists() and legacy_root.exists())
                else default_root
            )
    identity = detect_repo_identity_no_git(cwd_path)
    repo_dir = storage_root / identity["repo_key"]
    # In no-git mode "branch_dir" collapses onto the repo-shared layer; the
    # rest of the code stays polymorphic via branch_name=None.
    branch_dir = repo_dir / "repo"
    return cwd_path, None, None, storage_root, identity["repo_key"], repo_dir, branch_dir


def _resolve_workspace_repo(repo):
    if not detect_workspace_mode(repo):
        return repo
    primary = (
        os.environ.get("DEV_MEMORY_PRIMARY_REPO", "").strip()
        or os.environ.get("DEV_ASSETS_PRIMARY_REPO", "").strip()
    )
    repos_in_ws = list_repos_in_workspace(repo)
    names = [p.name for p in repos_in_ws]
    if not primary:
        raise RuntimeError(
            f"workspace mode detected at '{repo}': pass --repo <basename> explicitly "
            f"(one of: {names}) or set DEV_MEMORY_PRIMARY_REPO env."
        )
    match = next((p for p in repos_in_ws if p.name == primary), None)
    if match is None:
        raise RuntimeError(
            f"workspace mode: DEV_MEMORY_PRIMARY_REPO='{primary}' not found in '{repo}'. "
            f"Available: {names}."
        )
    return str(match)


def get_branch_paths(repo, context_dir=None, branch=None):
    if branch is None and detect_no_git_mode(repo):
        return get_no_git_paths(repo, context_dir)
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
    base = Path(cwd or ".").resolve()
    if not base.exists() or not base.is_dir():
        return False
    probe = run_git(["rev-parse", "--show-toplevel"], cwd=base, check=False)
    if probe.returncode == 0 and probe.stdout.strip():
        return False
    return bool(list_repos_in_workspace(base))


def list_repos_in_workspace(cwd=None):
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
    result = []
    for repo_path in list_repos_in_workspace(cwd):
        try:
            result.append(get_branch_paths(str(repo_path), context_dir=context_dir))
        except Exception:
            continue
    return result


def asset_paths(repo_dir, branch_dir):
    """Return a flat map of path keys for both repo and branch layers.

    Key naming convention: branch-level keys are bare ("decisions",
    "progress", ...). Repo-shared keys are prefixed with "repo_". The old v1
    keys (development/context/sources) are gone — callers that still reference
    them should be updated.
    """
    repo_memory_dir = repo_dir / "repo"
    paths = {
        "repo_manifest": repo_memory_dir / "manifest.json",
        "repo_overview": repo_memory_dir / "overview.md",
        "repo_decisions": repo_memory_dir / "decisions.md",
        "repo_glossary": repo_memory_dir / "glossary.md",
        "repo_log": repo_memory_dir / "log.md",
        "repo_artifacts": repo_memory_dir / "artifacts",
    }
    # In no-git mode, branch_dir collapses onto repo_memory_dir. Progress/risks
    # live inline at the repo layer since there's no branch concept; the other
    # v2 files reuse the repo keys rather than duplicating.
    if branch_dir == repo_memory_dir:
        paths.update({
            "manifest": paths["repo_manifest"],
            "overview": paths["repo_overview"],
            "decisions": paths["repo_decisions"],
            "progress": repo_memory_dir / "progress.md",
            "risks": repo_memory_dir / "risks.md",
            "glossary": paths["repo_glossary"],
            "unsorted": repo_memory_dir / "unsorted.md",
            "pending_promotion": repo_memory_dir / "pending-promotion.md",
            "log": paths["repo_log"],
            "artifacts": paths["repo_artifacts"],
            "history": repo_memory_dir / "artifacts" / "history",
        })
        return paths
    paths.update({
        "manifest": branch_dir / "manifest.json",
        "overview": branch_dir / "overview.md",
        "decisions": branch_dir / "decisions.md",
        "progress": branch_dir / "progress.md",
        "risks": branch_dir / "risks.md",
        "glossary": branch_dir / "glossary.md",
        "unsorted": branch_dir / "unsorted.md",
        "pending_promotion": branch_dir / "pending-promotion.md",
        "log": branch_dir / "log.md",
        "artifacts": branch_dir / "artifacts",
        "history": branch_dir / "artifacts" / "history",
    })
    return paths


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

    # Fill missing keys only — never overwrite existing values. The previous
    # `merged.update(defaults)` order silently clobbered stateful fields like
    # `focus_areas`, `scope_summary`, `last_seen_head` with empty defaults
    # whenever ensure_branch_paths_exist ran (every capture / sync), making
    # downstream merge_focus_areas always see `existing=[]`.
    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
    merged["initialized_at"] = existing.get("initialized_at", defaults.get("initialized_at"))
    # Preserve setup_completed if already true — re-running init shouldn't
    # reset user's setup progress.
    if existing.get("setup_completed"):
        merged["setup_completed"] = True
        merged["setup_completed_at"] = existing.get("setup_completed_at") or merged.get("setup_completed_at")
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


# ---------------------------------------------------------------------------
# Templates (v2)
# ---------------------------------------------------------------------------

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


def template_decisions(branch_name):
    return render_title_doc(
        "分支决策",
        [
            ("分支", f"- {branch_name}"),
            ("关键决策与原因", "- 待补充"),
        ],
    )


def template_progress(branch_name):
    return render_title_doc(
        "当前进展",
        [
            ("分支", f"- {branch_name}"),
            ("建议优先查看的目录", "- 待刷新"),
            ("当前进展", "- 待补充"),
            ("下一步", "- 待补充"),
            (
                "自动同步区",
                "本区由 `dev-memory-cli capture sync-working-tree` / SessionStart hook 自动刷新，请不要手工编辑。\n\n"
                f"{AUTO_START}\n"
                "_尚未同步_\n"
                f"{AUTO_END}",
            ),
        ],
    )


def template_risks(branch_name):
    return render_title_doc(
        "阻塞与注意点",
        [
            ("分支", f"- {branch_name}"),
            ("阻塞与注意点", "- 待补充"),
            ("后续继续前要注意", "- 待补充"),
        ],
    )


def template_glossary(branch_name):
    return render_title_doc(
        "术语与源资料",
        [
            ("分支", f"- {branch_name}"),
            ("当前分支专有术语", "- 待补充"),
            ("分支源资料入口", "- 待补充"),
        ],
    )


def template_unsorted():
    return (
        "# 未分类条目\n\n"
        "本文件存放 heuristic 无法分类的内容，或用户手动甩进来尚未整理的内容。\n"
        "下次 setup 或 capture --merge 时分类到 decisions/progress/risks/glossary。\n\n"
        "## 待分类\n\n- 待补充\n"
    )


def template_pending_promotion():
    return (
        "# 候选跨分支条目\n\n"
        "本文件由 capture 在检测到内容可能跨分支复用时自动打标写入。\n"
        "graduate 时优先从此文件筛选提炼到 repo 共享层。\n\n"
        "## 候选条目\n\n- 待补充\n"
    )


def template_log(scope_label):
    """Branch-layer event log skeleton.

    Append-only. Each entry is an H2 line starting with `## [<ISO timestamp>]`
    so `grep '^## \\['` slices the timeline cleanly. Optional sub-detail
    lines start with `- ` underneath.
    """
    return (
        "# 事件日志\n\n"
        f"- scope: {scope_label}\n\n"
        "Append-only。capture / tidy / graduate 等动作落盘后追加一行。\n"
        "`grep '^## \\[' log.md | tail -20` 看最近事件。\n\n"
        "<!-- LOG ENTRIES BELOW -->\n"
    )


def template_repo_log(repo_name):
    return (
        "# 仓库共享事件日志\n\n"
        f"- repo: {repo_name}\n\n"
        "Append-only。repo-shared 写入 / graduate harvest / 归档落盘后追加一行。\n"
        "`grep '^## \\[' log.md | tail -20` 看最近事件。\n\n"
        "<!-- LOG ENTRIES BELOW -->\n"
    )


def template_progress_no_git(project_name):
    return render_title_doc(
        "当前进展（no-git 模式）",
        [
            ("项目", f"- {project_name}"),
            ("当前进展", "- 待补充"),
            ("下一步", "- 待补充"),
            (
                "自动同步区",
                "本区由 capture / context 刷新。no-git 模式下无 git facts，保持最小骨架。\n\n"
                f"{AUTO_START}\n"
                "_尚未同步_\n"
                f"{AUTO_END}",
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


def template_repo_decisions(repo_name):
    return render_title_doc(
        "跨分支通用决策",
        [
            ("仓库", f"- {repo_name}"),
            ("跨分支通用决策", "- 待补充"),
        ],
    )


def template_repo_glossary(repo_name):
    return render_title_doc(
        "仓库共享术语与入口",
        [
            ("仓库", f"- {repo_name}"),
            ("长期有效背景", "- 待补充"),
            ("共享入口", "- 待补充"),
            ("共享注意点", "- 待补充"),
        ],
    )


# ---------------------------------------------------------------------------
# Manifest builders
# ---------------------------------------------------------------------------

def build_repo_manifest(repo_root, storage_root, repo_key, identity, *, no_git=False):
    manifest = {
        "schema_version": 4,
        "scope": "repo",
        "storage_mode": "user-home-no-git" if no_git else "user-home-repo-plus-branch",
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
    if no_git:
        manifest["no_git"] = True
    return manifest


def build_branch_manifest(repo_root, branch_name, branch_key, storage_root, repo_key):
    return {
        "schema_version": 4,
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
        # v2 additions: setup_completed flips to true when user runs setup
        # merge-unsorted flow. Lazy-init writes proceed with false.
        "setup_completed": False,
        "setup_completed_at": None,
    }


def get_setup_completed(manifest_path):
    manifest = read_json(manifest_path)
    return bool(manifest.get("setup_completed"))


def mark_setup_completed(manifest_path):
    manifest = read_json(manifest_path)
    manifest["setup_completed"] = True
    manifest["setup_completed_at"] = now_iso()
    manifest["updated_at"] = now_iso()
    write_json(manifest_path, manifest)
    return manifest


# ---------------------------------------------------------------------------
# Migration: v0 (in-repo .dev-memory/) and v1 (overview/development/context/sources)
# ---------------------------------------------------------------------------

def migrate_legacy_branch_assets(repo_root, branch_name, branch_key, branch_dir):
    """v0 → v1: pull old in-repo `.dev-memory/<branch>/` into user-home storage."""
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
    # Only copy v1-era file names (development/context/sources/overview/manifest).
    for file_name in ("manifest.json", "overview.md", "development.md", "context.md", "sources.md"):
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


# v1 → v2 section routing: old section title → (target v2 file key, optional new section title)
# None as new_title means "keep original title".
_V1_BRANCH_SECTION_MAP = {
    # from development.md
    "建议优先查看的目录": ("progress", None),
    "当前进展": ("progress", None),
    "下一步": ("progress", None),
    "阻塞与注意点": ("risks", None),
    # "自动同步区" is handled specially (copied into progress.md as-is).
    # from context.md
    "当前有效上下文": ("glossary", "当前有效上下文"),
    "关键决策与原因": ("decisions", "关键决策与原因"),
    "后续继续前要注意": ("risks", "后续继续前要注意"),
    # from sources.md
    "当前分支优先阅读": ("glossary", "分支源资料入口"),
    "提交与代码历史": ("glossary", "提交与代码历史参考"),
    # "分支" section is header metadata, skip.
}

_V1_REPO_SECTION_MAP = {
    # from repo/context.md
    "长期有效背景": ("repo_glossary", "长期有效背景"),
    "跨分支通用决策": ("repo_decisions", "跨分支通用决策"),
    "共享注意点": ("repo_glossary", "共享注意点"),
    # from repo/sources.md
    "共享入口": ("repo_glossary", "共享入口"),
    # "仓库" section is header metadata, skip.
}


def _collect_v1_sections(path, section_map, *, default_target=None):
    """Read a v1 markdown file and bucket its sections by target v2 key.
    Returns {v2_key: [(title, body), ...]}.

    Unknown sections (not in section_map) are routed to `default_target` with
    their original title preserved. Since the migration then deletes the v1
    file, silently discarding unknowns would mean unrecoverable data loss for
    any user-authored custom sections. Branch-layer callers pass
    default_target="unsorted"; repo-layer callers pass "repo_glossary".
    Set default_target=None to opt back into the old silent-drop behavior.
    """
    if not path.exists():
        return {}
    buckets = {}
    _, sections = split_sections(path.read_text(encoding="utf-8"))
    for title, body in sections:
        t = title.strip()
        if t == "分支" or t == "仓库":
            continue
        mapping = section_map.get(t)
        if mapping:
            target_key, new_title = mapping
            new_title = new_title or t
        elif default_target is not None:
            # Preserve the original title so the user can tell what was
            # unrecognised when they clean up unsorted.md manually.
            target_key = default_target
            new_title = t
        else:
            continue
        buckets.setdefault(target_key, []).append((new_title, body))
    return buckets


def _extract_auto_block(path):
    """Return the content between AUTO_START/AUTO_END markers, or None."""
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    if AUTO_START not in content or AUTO_END not in content:
        return None
    _, after_start = content.split(AUTO_START, 1)
    block, _ = after_start.split(AUTO_END, 1)
    return block.strip()


def _write_v2_file_from_buckets(target_path, doc_title, header_field, buckets, fallback_body="- 待补充"):
    """Render the v2 target file from migrated section buckets + a header."""
    sections = [header_field]
    if buckets:
        sections.extend(buckets)
    else:
        # No migrated content — let initialize_assets seed the placeholder
        # template instead of writing an empty file here.
        return False
    target_path.write_text(render_title_doc(doc_title, sections), encoding="utf-8")
    return True


def migrate_v1_to_v2_branch(branch_dir, branch_name):
    """v1 → v2: split old development/context/sources into new four-file
    structure plus unsorted/pending-promotion bootstrap. Old files are deleted
    after successful migration (single-user offline cleanup — no .legacy kept).
    Idempotent: returns None if no v1 files are present.
    """
    old_dev = branch_dir / "development.md"
    old_ctx = branch_dir / "context.md"
    old_src = branch_dir / "sources.md"

    if not any(p.exists() for p in (old_dev, old_ctx, old_src)):
        return None

    # Collect sections from all v1 files, bucketed by v2 target key. Unknown
    # sections land in "unsorted" so nothing is lost when the v1 files get
    # deleted below — the user can triage them via dev-memory-setup later.
    merged_buckets = {}
    for v1_path in (old_dev, old_ctx, old_src):
        buckets = _collect_v1_sections(v1_path, _V1_BRANCH_SECTION_MAP, default_target="unsorted")
        for k, entries in buckets.items():
            merged_buckets.setdefault(k, []).extend(entries)

    # Preserve the development.md auto-sync block as-is inside progress.md.
    auto_block = _extract_auto_block(old_dev)

    # Write v2 files if there's content; skip if bucket is empty so init's
    # placeholder template seeds the file instead.
    header = ("分支", f"- {branch_name}")
    written = []

    progress_sections = list(merged_buckets.get("progress", []))
    if auto_block is not None:
        progress_sections.append((
            "自动同步区",
            "本区由 `dev-memory-cli capture sync-working-tree` / SessionStart hook 自动刷新，请不要手工编辑。\n\n"
            f"{AUTO_START}\n{auto_block}\n{AUTO_END}",
        ))
    if progress_sections:
        target = branch_dir / "progress.md"
        target.write_text(
            render_title_doc("当前进展", [header] + progress_sections),
            encoding="utf-8",
        )
        written.append("progress.md")

    for key, doc_title in (
        ("decisions", "分支决策"),
        ("risks", "阻塞与注意点"),
        ("glossary", "术语与源资料"),
    ):
        entries = merged_buckets.get(key)
        if not entries:
            continue
        target = branch_dir / f"{key}.md"
        target.write_text(
            render_title_doc(doc_title, [header] + entries),
            encoding="utf-8",
        )
        written.append(f"{key}.md")

    # Unknown legacy sections go into unsorted.md under a dedicated
    # "legacy v1 未识别段落" group. Keeping the original section titles
    # here is the whole point — the user can see what got stranded and
    # classify properly via setup merge-unsorted.
    unsorted_entries = merged_buckets.get("unsorted")
    if unsorted_entries:
        legacy_body = "\n\n".join(
            f"### {t}\n\n{b.strip()}" for t, b in unsorted_entries
        )
        target = branch_dir / "unsorted.md"
        target.write_text(
            render_title_doc(
                "未分类条目",
                [
                    header,
                    (
                        "legacy v1 未识别段落",
                        "以下 section 在 v1 → v2 迁移时无法自动分类。"
                        "请走 `dev-memory-setup merge-unsorted` 分到 decisions / "
                        "progress / risks / glossary。\n\n" + legacy_body,
                    ),
                ],
            ),
            encoding="utf-8",
        )
        written.append("unsorted.md")

    # Delete old v1 files after successful migration.
    removed = []
    for p in (old_dev, old_ctx, old_src):
        if p.exists():
            p.unlink()
            removed.append(p.name)

    return {"migrated_files": written, "removed_legacy": removed}


def migrate_v1_to_v2_repo(repo_memory_dir, repo_name):
    """v1 → v2 for repo-shared layer: old repo/context.md + repo/sources.md
    are split into repo/decisions.md + repo/glossary.md. repo/overview.md
    stays put. Idempotent.
    """
    old_ctx = repo_memory_dir / "context.md"
    old_src = repo_memory_dir / "sources.md"

    if not any(p.exists() for p in (old_ctx, old_src)):
        return None

    # Unknown repo-layer sections fall to repo_glossary — it's the closest
    # shared-layer home and gets an explicit subsection so the user can
    # triage later.
    merged_buckets = {}
    for v1_path in (old_ctx, old_src):
        buckets = _collect_v1_sections(v1_path, _V1_REPO_SECTION_MAP, default_target="repo_glossary")
        for k, entries in buckets.items():
            merged_buckets.setdefault(k, []).extend(entries)

    header = ("仓库", f"- {repo_name}")
    written = []

    for key, doc_title, file_name in (
        ("repo_decisions", "跨分支通用决策", "decisions.md"),
        ("repo_glossary", "仓库共享术语与入口", "glossary.md"),
    ):
        entries = merged_buckets.get(key)
        if not entries:
            continue
        target = repo_memory_dir / file_name
        target.write_text(
            render_title_doc(doc_title, [header] + entries),
            encoding="utf-8",
        )
        written.append(file_name)

    removed = []
    for p in (old_ctx, old_src):
        if p.exists():
            p.unlink()
            removed.append(p.name)

    return {"migrated_files": written, "removed_legacy": removed}


# ---------------------------------------------------------------------------
# Initialize assets (lazy init entrypoint)
# ---------------------------------------------------------------------------

def initialize_assets(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir):
    """Create the repo-shared layer + current branch layer on disk, seeding
    each v2 file with a placeholder template. Idempotent — safe to call on
    every write.

    Runs v0→v1 legacy migration (in-repo .dev-memory/ dir) then v1→v2
    migration (old 3-file layout) before seeding, so existing content is never
    clobbered.
    """
    repo_memory_dir = repo_dir / "repo"
    repo_memory_dir.mkdir(parents=True, exist_ok=True)
    no_git = branch_name is None and branch_dir == repo_memory_dir
    if not no_git:
        branch_dir.mkdir(parents=True, exist_ok=True)
        set_storage_root_config(repo_root, storage_root)

    if no_git:
        identity = detect_repo_identity_no_git(repo_root)
    else:
        identity = detect_repo_identity(repo_root)

    # v0 → v1 first (copies old in-repo files into branch_dir with v1 names)
    v0_migration = None if no_git else migrate_legacy_branch_assets(repo_root, branch_name, branch_key, branch_dir)
    # v1 → v2 next (splits old files into v2 four-file structure)
    v1_branch_migration = None if no_git else migrate_v1_to_v2_branch(branch_dir, branch_name)
    v1_repo_migration = migrate_v1_to_v2_repo(repo_memory_dir, repo_root.name)

    paths = asset_paths(repo_dir, branch_dir)
    paths["repo_artifacts"].mkdir(exist_ok=True)
    if not no_git:
        paths["artifacts"].mkdir(exist_ok=True)
        paths["history"].mkdir(parents=True, exist_ok=True)

    # Repo-shared layer seeding.
    ensure_manifest(paths["repo_manifest"], build_repo_manifest(repo_root, storage_root, repo_key, identity, no_git=no_git))
    ensure_file(paths["repo_overview"], template_repo_overview(repo_root.name))
    ensure_file(paths["repo_decisions"], template_repo_decisions(repo_root.name))
    ensure_file(paths["repo_glossary"], template_repo_glossary(repo_root.name))
    ensure_file(paths["repo_log"], template_repo_log(repo_root.name))

    if no_git:
        # In no-git mode, progress/risks/unsorted/pending live at the repo
        # layer since there's no branch. Seed them with degraded templates.
        # log/log.md collapses onto repo_log via asset_paths(); no extra seed.
        ensure_file(paths["progress"], template_progress_no_git(repo_root.name))
        ensure_file(paths["risks"], template_risks(repo_root.name))
        ensure_file(paths["unsorted"], template_unsorted())
        ensure_file(paths["pending_promotion"], template_pending_promotion())
        return paths

    # Branch layer seeding.
    ensure_manifest(paths["manifest"], build_branch_manifest(repo_root, branch_name, branch_key, storage_root, repo_key))
    ensure_file(paths["overview"], template_overview(branch_name))
    ensure_file(paths["decisions"], template_decisions(branch_name))
    ensure_file(paths["progress"], template_progress(branch_name))
    ensure_file(paths["risks"], template_risks(branch_name))
    ensure_file(paths["glossary"], template_glossary(branch_name))
    ensure_file(paths["unsorted"], template_unsorted())
    ensure_file(paths["pending_promotion"], template_pending_promotion())
    ensure_file(paths["log"], template_log(f"branch:{branch_name}"))

    # Stamp migration info onto the branch manifest so graduate/context can
    # surface it when relevant.
    any_migration = v0_migration or v1_branch_migration or v1_repo_migration
    if any_migration:
        branch_manifest = read_json(paths["manifest"])
        note = {}
        if v0_migration:
            note["legacy_v0"] = v0_migration
        if v1_branch_migration:
            note["legacy_v1_branch"] = v1_branch_migration
        if v1_repo_migration:
            note["legacy_v1_repo"] = v1_repo_migration
        branch_manifest["legacy_migration"] = note
        branch_manifest["updated_at"] = now_iso()
        write_json(paths["manifest"], branch_manifest)

    return paths


def _inherit_from_worktree_base(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir):
    """If this checkout is a linked worktree whose branch was just created
    from another branch that already has substantive memory, copy that memory
    dir into the new branch's slot before lazy-init seeds an empty skeleton.

    Idempotent + side-effect-free no-op when:
    - DEV_MEMORY_DISABLE_WORKTREE_INHERIT env is set (escape hatch)
    - no_git mode (branch_name is None)
    - not a worktree (main repo / standalone clone)
    - reflog doesn't reveal a base branch
    - the resolved base branch's memory dir is missing or skeleton-only
    - the new branch's memory dir somehow already exists

    Returns the source branch name on success; None on no-op. The caller still
    runs `initialize_assets` afterwards, which is idempotent — it'll only fill
    in files the inherited tree didn't already have.
    """
    if os.environ.get("DEV_MEMORY_DISABLE_WORKTREE_INHERIT", "").strip():
        return None
    if branch_name is None:
        return None
    if branch_dir.exists():
        return None
    try:
        if not is_worktree(repo_root):
            return None
    except Exception:
        return None
    source_name = detect_worktree_base_branch(repo_root, branch_name)
    if not source_name:
        return None
    source_key = sanitize_branch_name(source_name)
    if source_key == branch_key:
        return None
    source_dir = repo_dir / "branches" / source_key
    if not source_dir.exists() or not source_dir.is_dir():
        return None
    # Defer skeleton check + fork mechanics to the branch CLI module so the
    # provenance stamp / metadata rewrite stays in one place. Lazy import to
    # avoid a circular dependency at module load.
    try:
        from dev_memory_branch import (
            inspect_branch_dir,
            _rewrite_branch_metadata,
            _rewrite_manifest,
            _stamp_overview_provenance,
        )
    except Exception:
        return None
    snap = inspect_branch_dir(source_dir, source_name)
    if not snap.get("exists") or snap.get("is_skeleton"):
        return None
    branch_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source_dir), str(branch_dir))
    _rewrite_manifest(branch_dir, branch_name, source_branch_name=source_name, op="worktree-inherit")
    _rewrite_branch_metadata(branch_dir, branch_name)
    _stamp_overview_provenance(branch_dir, source_name, "worktree-inherit")
    return source_name


def ensure_branch_paths_exist(repo, context_dir=None, branch=None):
    """Lazy-init entrypoint. Returns the same tuple as get_branch_paths() plus
    a `paths` dict, creating the directory + v2 file skeleton if missing.

    This is the thing capture/context should call instead of raising on
    missing branch_dir — the whole point of the v2 design is that writes never
    require prior setup.
    """
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
        repo, context_dir, branch
    )
    if not branch_dir.exists():
        _inherit_from_worktree_base(
            repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir,
        )
        initialize_assets(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir)
    else:
        # Branch dir exists but may be v1 — run the migration silently.
        migrate_v1_to_v2_branch(branch_dir, branch_name or repo_root.name)
        migrate_v1_to_v2_repo(repo_dir / "repo", repo_root.name)
        # And make sure v2 skeleton files exist (adds missing ones without
        # clobbering existing content).
        initialize_assets(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir)
    paths = asset_paths(repo_dir, branch_dir)
    return repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths


# ---------------------------------------------------------------------------
# Heuristic classifier (capture routing)
# ---------------------------------------------------------------------------

# Order matters: the first pattern that matches wins. Keep decisions/risks
# ahead of progress since their signals are more specific.
_CLASSIFY_PATTERNS = [
    ("decision", re.compile(r"结论[:：]|决[定议][:：]|不再|改为|采用|废弃|选择.+?不选|abandoned|adopt")),
    ("risk", re.compile(r"阻塞|注意|坑|失败|风险|卡住|gotcha|caveat|warning")),
    ("glossary", re.compile(r"即[:：]|\s即\s|指的是|对应|链接|https?://|api\s*=|缩写|术语|简称|别名")),
    ("progress", re.compile(r"当前|已完成|下一步|commit|提交|实现|进展|todo|wip")),
]


def classify_content(text, *, already_setup=False):
    """Classify free-form content into one of decisions/progress/risks/
    glossary/unsorted. Used by capture when the caller doesn't pass --kind.

    Before setup, ambiguous content falls to `unsorted` so the user can sort
    it later via setup merge. After setup, the default shifts to `progress`
    because the user has signaled they want aggressive categorization.
    """
    if not text or not text.strip():
        return "unsorted"
    for label, pattern in _CLASSIFY_PATTERNS:
        if pattern.search(text):
            return label
    return "progress" if already_setup else "unsorted"


# Branch-name tokens that appear in generic technical content too often to
# reliably signal "this is branch-local". Without filtering them out, a branch
# named `feature/capture-hook` would reject any lesson that says "hook" — e.g.
# "阻塞式 hook 不能做重活" — even though that's exactly the kind of cross-branch
# insight we want to stage. Keep this list to tokens that overwhelmingly appear
# as throw-away prefixes, layer names, or ubiquitous tech nouns.
_GENERIC_BRANCH_TOKENS = frozenset({
    "feature", "feat", "fix", "bug", "bugfix", "hotfix", "chore", "refactor",
    "docs", "test", "tests", "wip", "tmp", "temp", "main", "master", "dev",
    "develop", "release", "hook", "hooks", "apis", "cli", "web", "app",
    "lib", "libs", "util", "utils", "core", "base", "auth", "data", "cache",
    "user", "users", "admin", "demo", "example", "sample", "draft",
})

_CROSS_BRANCH_SIGNAL = re.compile(
    r"经验|模式|最佳实践|教训|通用|复用|以后.{0,6}都|所有.{0,10}都.{0,4}要|gotcha|pattern|lesson|repo-wide|across\s+branches",
    re.I,
)


def is_cross_branch_candidate(text, branch_name):
    """Heuristic: return True if content looks reusable across branches.

    Conservative — returns True only when:
      - content doesn't mention *branch-specific* terms (generic tech tokens
        like "hook"/"api"/"feature" are ignored via `_GENERIC_BRANCH_TOKENS`,
        so a branch named `feature/capture-hook` doesn't reject any lesson
        mentioning "hook"), AND
      - content has lesson-learned signals (经验/模式/最佳实践/教训/通用/以后都/所有
        ...都要/gotcha/pattern/lesson/repo-wide).

    Matches use word boundaries so "hook" in `hookup` doesn't falsely trigger.

    Cross-branch candidates are copied into pending-promotion.md in addition
    to their primary target file. graduate then only scans pending-promotion
    instead of every branch file.
    """
    if not text or not branch_name:
        return False
    lowered = text.lower()
    for token in branch_name.lower().replace("/", " ").replace("_", " ").replace("-", " ").split():
        if len(token) < 4 or token in _GENERIC_BRANCH_TOKENS:
            continue
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return False
    return bool(_CROSS_BRANCH_SIGNAL.search(text))


# ---------------------------------------------------------------------------
# Git-derived facts and auto-block rendering
# ---------------------------------------------------------------------------

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


def summarize_scopes(paths):
    counter = Counter(top_level_scope(path) for path in paths)
    return [{"scope": scope, "files": count} for scope, count in sorted(counter.items())]


def _initial_parent(path_str):
    """File path → its immediate parent directory key. Files at repo root use
    their own file path as the focus key instead of '.', everything else uses
    the POSIX-style parent dir string."""
    parent = Path(path_str).parent
    if str(parent) in ("", "."):
        return Path(path_str).as_posix()
    return parent.as_posix()


def _can_roll_up(key):
    """A bucket is rollable if its key is at depth ≥ 2 (so rolling up keeps it
    above the repo root). 1-level keys like 'lib' or 'docs' are 'isolated
    shallow' buckets — keep them as-is rather than collapse to '.'."""
    if key in ("", "."):
        return False
    return len(Path(key).parts) >= 2


def _rolled_up(key):
    return Path(key).parent.as_posix()


def summarize_focus_areas(paths, limit=None):
    """Cluster changed-file paths into ≤ `limit` focus directories.

    Algorithm (bottom-up): start with each file's immediate parent dir as a
    bucket; while the bucket count exceeds `limit`, roll every roll-able
    bucket up one level, find the rolled-up key with the largest summed
    count (the "dominant cluster" — most files concentrated under a single
    deeper subtree), and merge only those originals into it. Buckets that
    were already at depth 1 or '.' are left untouched, so isolated shallow
    directories and root-level files survive. Tie-break: deeper rolled-up key
    wins, then lexicographic.
    """
    if limit is None:
        limit = FOCUS_AREA_LIMIT
    buckets = Counter(_initial_parent(p) for p in paths)
    while len(buckets) > limit:
        proposals = {}  # rolled_key -> [sum_count, [original_keys...]]
        for key, count in buckets.items():
            if not _can_roll_up(key):
                continue
            new_key = _rolled_up(key)
            entry = proposals.setdefault(new_key, [0, []])
            entry[0] += count
            entry[1].append(key)
        if not proposals:
            break
        def score(item):
            new_key, (sum_count, _) = item
            depth = len(Path(new_key).parts) if new_key not in ("", ".") else 0
            return (sum_count, depth, new_key)
        winner_key, (winner_count, originals) = max(proposals.items(), key=score)
        for k in originals:
            buckets.pop(k)
        buckets[winner_key] = buckets.get(winner_key, 0) + winner_count
    ranked = sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in ranked[:limit]]


# Number of recent commits whose touched files contribute to the "focus area"
# signal. The merge-base diff was previously the long-tail input here, but on
# long-lived branches it dilutes hot-spot detection. Recent commits are a
# better proxy for "what's currently being worked on" without dragging in
# branch-lifecycle history.
RECENT_COMMITS_FOR_FOCUS = 10


def _files_in_recent_commits(repo_root, count, base_ref=None):
    """Files touched by recent non-merge commits on the current branch.

    When a default base is known, bound the range to ``base..HEAD`` so noisy
    commits already merged into the base branch do not pollute focus areas.
    """
    cmd = [
        "log",
        f"-n{count}",
        "--no-merges",
        "--name-only",
        "--pretty=format:",
    ]
    if base_ref:
        cmd.append(f"{base_ref}..HEAD")
    out = git_lines(cmd, cwd=repo_root, check=False)
    seen = []
    seen_set = set()
    for line in out:
        if not line or line in seen_set:
            continue
        seen_set.add(line)
        seen.append(line)
    return seen


def collect_git_facts(repo_root, branch_name, _storage_root=None):
    working_tree_files = git_lines(["diff", "--name-only"], cwd=repo_root)
    staged_files = git_lines(["diff", "--cached", "--name-only"], cwd=repo_root)
    untracked_files = git_lines(["ls-files", "--others", "--exclude-standard"], cwd=repo_root)
    default_base = detect_default_base(repo_root)
    recent_commit_files = _files_in_recent_commits(repo_root, RECENT_COMMITS_FOR_FOCUS, default_base)

    all_paths = sorted(set(working_tree_files + staged_files + untracked_files + recent_commit_files))
    return {
        "branch": branch_name,
        "default_base": default_base,
        "last_seen_head": get_head_commit(repo_root),
        "working_tree_files": working_tree_files,
        "staged_files": staged_files,
        "untracked_files": untracked_files,
        "recent_commit_files": recent_commit_files,
        "scope_summary": summarize_scopes(all_paths),
        "focus_areas": summarize_focus_areas(all_paths),
        "updated_at": now_iso(),
    }


def merged_focus_areas(new_paths, existing, limit=None):
    """Compute focus_areas, mixing existing focus dirs with the new working
    tree signal.

    Algorithm (matches user's mental model — "specific signals over wide
    parents; stale entries disappear once current git facts no longer cover
    them"):

      1. Internally dedupe `existing`: when a wider parent dir (e.g. `apps`)
         coexists with either an existing or freshly-derived specific
         descendant (e.g. `apps/x/y/prompts`), drop the wider parent. The wider
         parent is almost always a leftover from an earlier roll-up that
         swallowed too much; the specific descendants carry the real signal.
      2. Score every surviving existing dir by how many `new_paths` it
         actually covers. 0 means stale, e.g. `.vscode` after a one-shot IDE
         write; stale entries are not carried forward.
      3. Compute `uncovered` = paths not under any active existing dir.
      4. Roll up the uncovered paths' immediate parents into ≤ remaining
         budget buckets, **forbidding roll-up into any ancestor of an
         existing dir** so the output cannot regenerate the over-wide
         parent we just removed in step 1.
      5. Merge active existing weights with the rolled-up uncovered buckets,
         sort by weight desc, truncate to `limit`.
    """
    if limit is None:
        limit = FOCUS_AREA_LIMIT
    if not existing:
        return summarize_focus_areas(new_paths, limit=limit)

    def _is_under(path, dir_):
        if dir_ in ("", "."):
            return "/" not in path
        return path == dir_ or path.startswith(dir_ + "/")

    fresh_focus = summarize_focus_areas(new_paths, limit=limit)

    def _drop_wider_parents(items):
        items = [d for d in dict.fromkeys(items) if d != "."]  # dedupe preserving order
        comparison_items = items + fresh_focus
        return [d for d in items if not any(other != d and _is_under(other, d) for other in comparison_items)]

    # (1) Drop wider parent entries inside existing.
    deduped_existing = _drop_wider_parents(existing)

    # (2) Real coverage weight per surviving existing dir. Drop stale existing
    # dirs before computing uncovered paths or slot budget; otherwise stale
    # manifest entries still force new hot spots to roll up too far.
    existing_weights = {d: sum(1 for p in new_paths if _is_under(p, d)) for d in deduped_existing}
    active_existing_weights = {d: w for d, w in existing_weights.items() if w > 0}

    # (3) Paths not yet covered by any active existing dir.
    active_existing_dirs = set(active_existing_weights)
    uncovered = [p for p in new_paths if not any(_is_under(p, d) for d in active_existing_dirs)]

    # (4) Forbidden roll-up targets = any ancestor of a surviving existing
    # dir. Without this guard the roll-up of `uncovered` could produce a
    # parent like `apps` and we'd be back where we started.
    forbidden_ancestors = set()
    for d in active_existing_dirs:
        if d in ("", "."):
            continue
        parent = Path(d).parent
        while True:
            key = parent.as_posix()
            if key in ("", "."):
                break
            forbidden_ancestors.add(key)
            if parent == parent.parent:
                break
            parent = parent.parent

    # Reserve slots for existing entries; uncovered fills the rest.
    remaining_budget = max(1, limit - len(active_existing_weights))

    new_buckets = Counter()
    for p in uncovered:
        new_buckets[_initial_parent(p)] += 1

    while len(new_buckets) > remaining_budget:
        proposals = {}
        for key, count in new_buckets.items():
            if not _can_roll_up(key):
                continue
            new_key = _rolled_up(key)
            if new_key in forbidden_ancestors:
                # Rolling up here would regenerate a wide parent we just
                # dropped — leave the deeper buckets where they are.
                continue
            entry = proposals.setdefault(new_key, [0, []])
            entry[0] += count
            entry[1].append(key)
        if not proposals:
            # No legal roll-up move left; live with the current bucket count
            # — final ranking + limit will handle the rest.
            break

        def score(item):
            new_key, (sum_count, _) = item
            depth = len(Path(new_key).parts) if new_key not in ("", ".") else 0
            return (sum_count, depth, new_key)

        winner_key, (winner_count, originals) = max(proposals.items(), key=score)
        for k in originals:
            new_buckets.pop(k)
        new_buckets[winner_key] = new_buckets.get(winner_key, 0) + winner_count

    # (5) Quota partition so a flurry of new hot spots can't eject the
    # user's long-running focus dirs in one shot.
    #
    #   - existing_quota = ceil(limit/2)  → reserved for existing active dirs
    #   - new_quota      = floor(limit/2) → reserved for fresh buckets
    #   - either side can borrow unused slots from the other (so we never
    #     leave a slot empty just to enforce the partition).
    existing_active = sorted(
        active_existing_weights.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )
    new_items = sorted(new_buckets.items(), key=lambda kv: (-kv[1], kv[0]))

    existing_quota = (limit + 1) // 2  # ceil(limit/2)
    new_quota = limit - existing_quota

    existing_take = list(existing_active[:existing_quota])
    new_take = list(new_items[:new_quota])

    # Lend leftover slots across the partition before falling back to stale.
    existing_overflow = existing_quota - len(existing_take)
    new_overflow = new_quota - len(new_take)
    if existing_overflow > 0:
        new_take.extend(new_items[new_quota:new_quota + existing_overflow])
    if new_overflow > 0:
        existing_take.extend(existing_active[existing_quota:existing_quota + new_overflow])

    selected = []
    seen = set()
    for d, weight in existing_take + new_take:
        if d in seen:
            continue
        seen.add(d)
        selected.append((d, weight))
    selected.sort(key=lambda kv: (-kv[1], kv[0]))
    return [d for d, _ in selected[:limit]]


def build_auto_block(facts):
    base_line = facts["default_base"] or "未检测到 origin/HEAD"
    head_line = facts["last_seen_head"] or "尚未检测到 HEAD"
    if facts["default_base"]:
        history_cmd = f"git log --oneline -n {RECENT_COMMITS_FOR_FOCUS} --no-merges {facts['default_base']}..HEAD"
    else:
        history_cmd = f"git log --oneline -n {RECENT_COMMITS_FOR_FOCUS} --no-merges"
    focus_lines = render_bullets(facts["focus_areas"], empty_text="- 当前未检测到改动目录", wrap_code=True)
    scope_lines = render_bullets(
        [f"{item['scope']} ({item['files']} files)" for item in facts["scope_summary"]],
        empty_text="- 当前未检测到改动范围",
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
        f"- `{history_cmd}`\n"
        "- `git diff --name-only`\n"
    )


def ensure_progress_auto_block(path):
    """Idempotently ensure progress.md has the auto-sync marker pair. Called
    before any auto-block replace/sync so freshly created files (or hand-
    edited ones that lost the markers) stay writable by sync_progress().
    """
    content = path.read_text(encoding="utf-8")
    if AUTO_START in content and AUTO_END in content:
        return content

    marker = "## 自动同步区"
    auto_section = (
        f"\n\n{marker}\n\n"
        "本区由 `dev-memory-cli capture sync-working-tree` / SessionStart hook 自动刷新，请不要手工编辑。\n\n"
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
        raise RuntimeError("progress.md is missing auto-generated markers")
    before, remainder = content.split(AUTO_START, 1)
    _, after = remainder.split(AUTO_END, 1)
    return f"{before}{AUTO_START}\n{replacement.rstrip()}\n{AUTO_END}{after}"


# ---------------------------------------------------------------------------
# Section-level markdown editing
# ---------------------------------------------------------------------------

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
            # drop duplicates if any.
        else:
            updated.append((existing_title, existing_body))
    if not replaced:
        updated.append((title, body))
    path.write_text(join_sections(prefix, updated), encoding="utf-8")


def _section_is_placeholder_only(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    return all(any(marker in line for marker in PLACEHOLDER_MARKERS) for line in lines)


def append_to_section(path, title, body):
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix, sections = split_sections(content)
    target = title.strip()
    matched = False
    updated = []
    for existing_title, existing_body in sections:
        if existing_title.strip() == target and not matched:
            if _section_is_placeholder_only(existing_body):
                combined = body.strip()
            else:
                combined = (existing_body.rstrip() + "\n" + body.strip()).strip()
            updated.append((existing_title, combined))
            matched = True
        else:
            updated.append((existing_title, existing_body))
    if not matched:
        updated.append((title, body.strip()))
    path.write_text(join_sections(prefix, updated), encoding="utf-8")


def upsert_progress_section(path, title, body):
    """upsert a section into progress.md while preserving the auto-sync block
    at the end of the file. Any non-auto-sync sections before the marker get
    normal upsert semantics.
    """
    content = ensure_progress_auto_block(path)
    marker = "## 自动同步区"
    if marker not in content:
        raise RuntimeError("progress.md is missing the auto-sync section heading")
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


def sync_progress(paths, facts):
    """Refresh progress.md — writes both the human-readable focus section and
    the auto-sync block. Called by capture's `sync-working-tree` subcommand
    and by context's `sync`.
    """
    upsert_progress_section(
        paths["progress"],
        "建议优先查看的目录",
        render_bullets(facts["focus_areas"], empty_text="- 当前未检测到改动目录", wrap_code=True),
    )
    current = ensure_progress_auto_block(paths["progress"])
    updated = replace_auto_block(current, build_auto_block(facts))
    paths["progress"].write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Event log (append-only timeline)
# ---------------------------------------------------------------------------

# Cap on the inline summary so a runaway body doesn't produce a 5K-char log
# line. Anything longer is truncated with `…` — the full content already
# lives in the target file the entry points at.
_LOG_SUMMARY_MAX = 160


def _summarize_for_log(text):
    """Collapse multi-line text to a single-line summary suitable for the
    H2 header. Strips bullet markers and trims to _LOG_SUMMARY_MAX chars."""
    if not text:
        return ""
    first = ""
    for line in str(text).splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("- ", "* ")):
            s = s[2:].strip()
        first = s
        break
    if len(first) > _LOG_SUMMARY_MAX:
        first = first[: _LOG_SUMMARY_MAX - 1].rstrip() + "…"
    return first


def append_log_event(log_path, action, *, kind=None, summary=None, details=None):
    """Append an event line to a log.md file.

    Format:
        ## [<ISO timestamp>] <action>[ · <kind>][ | <summary>]
        - key: value      # optional
        - key: value

    `action`  — short verb: capture / rewrite-entry / tidy / graduate / etc.
    `kind`    — sub-classifier (e.g. "decision", "apply"); optional.
    `summary` — one-line content preview; multi-line input is collapsed.
    `details` — iterable of (key, value) pairs rendered as `- key: value`
                lines under the header. Values are str()-cast.

    Idempotent on file creation: ensures the file exists with a minimal
    skeleton if missing (covers callers operating on legacy branch dirs
    that pre-date this feature).
    """
    if log_path is None:
        return
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(template_log("unknown"), encoding="utf-8")

    header_parts = [f"## [{now_iso()}] {action}"]
    if kind:
        header_parts.append(f" · {kind}")
    summary_clean = _summarize_for_log(summary) if summary else ""
    if summary_clean:
        header_parts.append(f" | {summary_clean}")
    header = "".join(header_parts)

    lines = [header]
    for key, value in (details or []):
        if value is None or value == "":
            continue
        lines.append(f"- {key}: {value}")

    block = "\n".join(lines) + "\n"
    existing = log_path.read_text(encoding="utf-8")
    if not existing.endswith("\n"):
        existing += "\n"
    log_path.write_text(existing + "\n" + block, encoding="utf-8")


# ---------------------------------------------------------------------------
# Archive (graduate)
# ---------------------------------------------------------------------------

ARCHIVE_DIR_NAME = "_archived"
ARCHIVE_INDEX_NAME = "INDEX.md"


def archive_root_dir(repo_dir):
    return repo_dir / "branches" / ARCHIVE_DIR_NAME


def build_archive_summary(branch_manifest, git_log_lines, harvest_notes=None):
    parts = ["# 归档快照", ""]
    if harvest_notes:
        parts.extend(["## Harvest 备注", "", harvest_notes.strip(), ""])
    parts.extend([
        "## 归档时元数据",
        "",
        f"- 归档时间: {now_iso()}",
        f"- 分支: {branch_manifest.get('branch', '<unknown>')}",
        f"- 最终 HEAD: {branch_manifest.get('last_seen_head') or '<unknown>'}",
        f"- 默认基线: {branch_manifest.get('default_base') or '<unknown>'}",
        f"- 最近 capture 标题: {branch_manifest.get('last_session_sync_title') or '<none>'}",
        "",
    ])
    if git_log_lines:
        parts.append("## Git log (base..HEAD, oneline)")
        parts.append("")
        parts.append("```")
        parts.extend(git_log_lines)
        parts.append("```")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def archive_branch_dir(branch_dir, archive_dst):
    archive_dst.parent.mkdir(parents=True, exist_ok=True)
    if archive_dst.exists():
        raise RuntimeError(f"archive destination already exists: {archive_dst}")
    shutil.move(str(branch_dir), str(archive_dst))


def append_archive_index(index_path, line):
    if not index_path.exists():
        index_path.write_text(
            "# 归档分支索引\n\n按归档时间倒序追加。每条记录格式：\n\n"
            "`- <YYYY-MM-DD> <branch_name> (HEAD <sha>) → harvested <N> entries: <notes>`\n\n",
            encoding="utf-8",
        )
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


# ---------------------------------------------------------------------------
# Health / metadata helpers
# ---------------------------------------------------------------------------

def list_missing_docs(paths):
    """Return keys whose file is missing or still contains placeholder text.
    Skips manifest/artifacts/history and legacy files (handled elsewhere).
    """
    missing = []
    skip_keys = {"manifest", "repo_manifest", "artifacts", "history", "repo_artifacts"}
    for key, path in paths.items():
        if key in skip_keys:
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
