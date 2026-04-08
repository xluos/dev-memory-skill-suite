#!/usr/bin/env python3

import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CONTEXT_DIR = ".dev-assets"
AUTO_START = "<!-- AUTO-GENERATED-START -->"
AUTO_END = "<!-- AUTO-GENERATED-END -->"
MANAGED_FILES = (
    "manifest.json",
    "overview.md",
    "prd.md",
    "review-notes.md",
    "frontend-design.md",
    "backend-design.md",
    "test-cases.md",
    "development.md",
    "decision-log.md",
    "commits.md",
)


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


def ensure_local_exclude(repo_root, context_dir):
    exclude_path = Path(repo_root) / ".git" / "info" / "exclude"
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    entry = f"{context_dir}/"
    if entry not in {line.strip() for line in existing.splitlines()}:
        with exclude_path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(f"{entry}\n")


def set_repo_config(repo_root, context_dir):
    run_git(["config", "--local", "dev-assets.dir", context_dir], cwd=repo_root)


def get_context_dir(repo_root, explicit_value=None):
    if explicit_value:
        return explicit_value
    configured = run_git(["config", "--get", "dev-assets.dir"], cwd=repo_root, check=False)
    value = configured.stdout.strip()
    return value or DEFAULT_CONTEXT_DIR


def normalize_context_dir(context_dir, branch_name, branch_key):
    parts = list(Path(context_dir).parts)
    branch_parts = list(Path(branch_name).parts)

    if branch_parts and len(parts) >= len(branch_parts) and parts[-len(branch_parts) :] == branch_parts:
        parts = parts[: -len(branch_parts)]
    if parts and parts[-1] == branch_key:
        parts = parts[:-1]

    if not parts:
        return DEFAULT_CONTEXT_DIR
    return Path(*parts).as_posix()


def resolve_branch_dir(repo_root, raw_context_dir, resolved_context_dir, branch_name, branch_key):
    candidates = [
        repo_root / resolved_context_dir / branch_key,
        repo_root / resolved_context_dir / Path(branch_name),
    ]
    if raw_context_dir != resolved_context_dir:
        candidates.extend(
            [
                repo_root / raw_context_dir / branch_key,
                repo_root / raw_context_dir / Path(branch_name),
            ]
        )

    seen = set()
    for candidate in candidates:
        key = candidate.as_posix()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return candidates[0]


def get_branch_paths(repo, context_dir=None, branch=None):
    repo_root = detect_repo_root(repo)
    branch_name = branch or detect_branch(repo_root)
    branch_key = sanitize_branch_name(branch_name)
    raw_context_dir = get_context_dir(repo_root, context_dir)
    resolved_context_dir = normalize_context_dir(raw_context_dir, branch_name, branch_key)
    if not context_dir and resolved_context_dir != raw_context_dir:
        set_repo_config(repo_root, resolved_context_dir)
    branch_dir = resolve_branch_dir(repo_root, raw_context_dir, resolved_context_dir, branch_name, branch_key)
    return repo_root, branch_name, branch_key, resolved_context_dir, branch_dir


def asset_paths(branch_dir):
    return {
        "manifest": branch_dir / "manifest.json",
        "overview": branch_dir / "overview.md",
        "prd": branch_dir / "prd.md",
        "review_notes": branch_dir / "review-notes.md",
        "frontend_design": branch_dir / "frontend-design.md",
        "backend_design": branch_dir / "backend-design.md",
        "test_cases": branch_dir / "test-cases.md",
        "development": branch_dir / "development.md",
        "decision_log": branch_dir / "decision-log.md",
        "commits": branch_dir / "commits.md",
        "artifacts": branch_dir / "artifacts",
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


def build_manifest(repo_root, branch_name, branch_key, context_dir):
    return {
        "repo_root": str(repo_root),
        "branch": branch_name,
        "branch_key": branch_key,
        "context_dir": context_dir,
        "initialized_at": now_iso(),
        "updated_at": now_iso(),
        "last_recorded_commit": None,
    }


def template_overview(branch_name):
    return f"""# 概览

## 分支

- {branch_name}

## 需求摘要

- 待补充

## 当前阶段

- 待补充

## 本目录里的重点资产

- `prd.md`
- `review-notes.md`
- `frontend-design.md`
- `backend-design.md`
- `test-cases.md`
- `development.md`
- `decision-log.md`
- `commits.md`
"""


def template_named_doc(title, bullets):
    bullet_lines = "\n".join(f"- {item}" for item in bullets)
    return f"""# {title}

{bullet_lines}
"""


def template_development(branch_name):
    return f"""# 开发过程

## 分支

- {branch_name}

## 当前需求点

- 待补充

## 实现备注

- 待补充

## 风险与阻塞

- 待补充

## 自动同步区

本区由 `dev-assets-context` 或 `dev-assets-sync` 刷新，请不要手工编辑。

{AUTO_START}
_尚未同步_
{AUTO_END}
"""


def template_decision_log():
    return """# 决策记录

## 记录规范

- 每条记录写明日期、结论、原因、影响范围
- 只记录后续会反复引用的结论，不写流水账
"""


def template_commits():
    return """# 提交记录

## 记录规范

- 记录 commit sha、message、时间、涉及需求点
- 提交前后的重要结论可一起写入
"""


def initialize_assets(repo_root, branch_name, branch_key, context_dir, branch_dir):
    branch_dir.mkdir(parents=True, exist_ok=True)
    ensure_local_exclude(repo_root, context_dir)
    set_repo_config(repo_root, context_dir)

    paths = asset_paths(branch_dir)
    paths["artifacts"].mkdir(exist_ok=True)

    if not paths["manifest"].exists():
        write_json(paths["manifest"], build_manifest(repo_root, branch_name, branch_key, context_dir))
    ensure_file(paths["overview"], template_overview(branch_name))
    ensure_file(
        paths["prd"],
        template_named_doc("PRD / 需求文档", ["待补充产品背景", "待补充目标与范围", "待补充验收口径"]),
    )
    ensure_file(
        paths["review_notes"],
        template_named_doc("评审记录", ["待补充评审结论", "待补充争议点", "待补充后续 action"]),
    )
    ensure_file(
        paths["frontend_design"],
        template_named_doc("前端方案", ["待补充页面范围", "待补充交互/状态", "待补充接口依赖"]),
    )
    ensure_file(
        paths["backend_design"],
        template_named_doc("后端方案", ["待补充接口/模型", "待补充兼容性考虑", "待补充发布影响"]),
    )
    ensure_file(
        paths["test_cases"],
        template_named_doc("测试用例", ["待补充主流程", "待补充边界场景", "待补充回归范围"]),
    )
    ensure_file(paths["development"], template_development(branch_name))
    ensure_file(paths["decision_log"], template_decision_log())
    ensure_file(paths["commits"], template_commits())
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


def summarize_scopes(paths):
    counter = Counter(top_level_scope(path) for path in paths)
    return [{"scope": scope, "files": count} for scope, count in sorted(counter.items())]


def collect_git_facts(repo_root, branch_name, context_dir):
    context_prefix = f"{context_dir}/"
    working_tree_files = [
        path for path in git_lines(["diff", "--name-only"], cwd=repo_root) if not path.startswith(context_prefix)
    ]
    staged_files = [
        path for path in git_lines(["diff", "--cached", "--name-only"], cwd=repo_root) if not path.startswith(context_prefix)
    ]
    untracked_files = [
        path
        for path in git_lines(["ls-files", "--others", "--exclude-standard"], cwd=repo_root)
        if not path.startswith(context_prefix)
    ]

    default_base = detect_default_base(repo_root)
    since_base_files = []
    if default_base:
        merge_base = git_lines(["merge-base", "HEAD", default_base], cwd=repo_root)
        if merge_base:
            since_base_files = [
                path
                for path in git_lines(["diff", "--name-only", f"{merge_base[0]}...HEAD"], cwd=repo_root)
                if not path.startswith(context_prefix)
            ]

    all_paths = sorted(set(working_tree_files + staged_files + untracked_files + since_base_files))
    return {
        "branch": branch_name,
        "default_base": default_base,
        "working_tree_files": working_tree_files,
        "staged_files": staged_files,
        "untracked_files": untracked_files,
        "since_base_files": since_base_files,
        "scope_summary": summarize_scopes(all_paths),
        "updated_at": now_iso(),
    }


def format_list(items):
    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def build_auto_block(facts):
    base_line = facts["default_base"] or "未检测到 origin/HEAD"
    scope_lines = format_list([f"{item['scope']} ({item['files']} files)" for item in facts["scope_summary"]])
    return f"""### 自动生成

- 更新时间: {facts['updated_at']}
- 当前分支: {facts['branch']}
- 默认基线分支: {base_line}

#### 工作区改动文件

{format_list(facts['working_tree_files'])}

#### 已暂存文件

{format_list(facts['staged_files'])}

#### 未跟踪文件

{format_list(facts['untracked_files'])}

#### 相对默认基线的改动文件

{format_list(facts['since_base_files'])}

#### 改动范围汇总

{scope_lines}
"""


def ensure_development_auto_block(path, branch_name):
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
    return f"{before}{AUTO_START}\n{replacement}\n{AUTO_END}{after}"


def sync_development(paths, facts):
    current = ensure_development_auto_block(paths["development"], facts["branch"])
    updated = replace_auto_block(current, build_auto_block(facts))
    paths["development"].write_text(updated, encoding="utf-8")


def list_missing_docs(paths):
    missing = []
    placeholder_markers = ("待补充", "_尚未同步_")
    for key, path in paths.items():
        if key in {"manifest", "artifacts"}:
            continue
        if not path.exists():
            missing.append(key)
            continue
        content = path.read_text(encoding="utf-8")
        if any(marker in content for marker in placeholder_markers):
            missing.append(key)
    return missing


def get_head_commit(repo_root):
    sha = git_stdout(["rev-parse", "HEAD"], cwd=repo_root, check=False)
    return sha or None


def get_commit_payload(repo_root, commit_sha):
    if not commit_sha:
        return None
    subject = git_stdout(["show", "-s", "--format=%s", commit_sha], cwd=repo_root)
    body = git_stdout(["show", "-s", "--format=%b", commit_sha], cwd=repo_root, check=False)
    author_time = git_stdout(["show", "-s", "--format=%cI", commit_sha], cwd=repo_root)
    files = git_lines(["show", "--name-only", "--format=", commit_sha], cwd=repo_root)
    return {
        "sha": commit_sha,
        "subject": subject,
        "body": body,
        "author_time": author_time,
        "files": files,
    }


def append_commit_record(commits_path, payload):
    content = commits_path.read_text(encoding="utf-8") if commits_path.exists() else "# 提交记录\n"
    if payload["sha"] in content:
        return
    entry = (
        f"\n## {payload['sha'][:12]} {payload['subject']}\n\n"
        f"- 时间: {payload['author_time']}\n"
        f"- 完整 SHA: {payload['sha']}\n"
        f"- 涉及文件数: {len(payload['files'])}\n\n"
        f"### 文件\n\n{format_list(payload['files'])}\n"
    )
    if payload["body"]:
        entry += f"\n### 说明\n\n{payload['body'].strip()}\n"
    commits_path.write_text(content.rstrip() + "\n" + entry, encoding="utf-8")


def append_markdown_section(path, title, body):
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    section = f"## {title}\n\n{body.strip()}\n"
    prefix = content.rstrip()
    if prefix:
        prefix += "\n\n"
    path.write_text(prefix + section + "\n", encoding="utf-8")


def append_development_section(path, title, body):
    content = path.read_text(encoding="utf-8")
    if AUTO_START not in content or AUTO_END not in content:
        raise RuntimeError("development.md is missing auto-generated markers")
    marker = "## 自动同步区"
    if marker not in content:
        raise RuntimeError("development.md is missing the auto-sync section heading")
    before, after = content.split(marker, 1)
    section = f"## {title}\n\n{body.strip()}\n"
    updated = before.rstrip() + "\n\n" + section + "\n" + marker + after
    path.write_text(updated, encoding="utf-8")
