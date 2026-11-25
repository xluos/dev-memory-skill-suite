#!/usr/bin/env python3
"""Daily housekeeping for dev-memory:
1. Prune completely empty repo directories past a grace period.
2. Detect repo-key drift (same local repo_root mapped to multiple repo keys).
3. Auto-tidy ONLY repo+branch memories that actually changed since their last tidy.
"""

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dev_memory_common import PLACEHOLDER_MARKERS, get_branch_paths, now_iso

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = PACKAGE_ROOT / "lib" / "dev_memory_capture.py"
SCHEMA_VERSION = 1
EMPTY_SENTINELS = (
    "当前未检测到改动目录",
    "当前未检测到改动范围",
    "未检测到 origin/HEAD",
    "尚未检测到 HEAD",
)
REPO_SEMANTIC_FILES = ("overview.md", "decisions.md", "glossary.md")
BRANCH_SEMANTIC_FILES = ("overview.md", "decisions.md", "risks.md", "glossary.md", "unsorted.md")
BRANCH_BACKUP_FILES = ("overview.md", "decisions.md", "risks.md", "glossary.md", "unsorted.md", "pending-promotion.md")
TIDY_GROUPS = (
    ("decisions", ("decision",)),
    ("risks", ("risk",)),
    ("glossary", ("glossary", "source")),
    ("pending", ("pending",)),
    ("shared", ("shared-decision", "shared-context", "shared-source")),
)
TIDY_KINDS = tuple(kind for _group, kinds in TIDY_GROUPS for kind in kinds)
TIDY_BATCH_SIZE = 60
MAX_PURE_DELETE_RATIO = 0.80
EXACT_DIRTY_PLACEHOLDERS = {
    "可独立理解的完整结论",
    "简短标题",
    "decision",
    "summary",
    "content",
}


def _storage_root():
    raw = os.environ.get("DEV_MEMORY_ROOT") or os.environ.get("DEV_ASSETS_ROOT") or "~/.dev-memory/repos"
    return Path(raw).expanduser()


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _count_semantic_blocks(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    body = re.sub(r"^# .*\n", "", text)
    sections = re.split(r"(?=^## )", body, flags=re.M)
    count = 0
    for sec in sections:
        sec = sec.strip()
        if not sec.startswith("## "):
            continue
        title, _, content = sec.partition("\n")
        if title.strip() in ("## 仓库", "## 分支", "## 元信息"):
            continue
        cleaned = re.sub(r"<!--.*?-->", "", content, flags=re.S).strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if not lines:
            continue
        if all(
            any(m in line for m in PLACEHOLDER_MARKERS) or any(s in line for s in EMPTY_SENTINELS)
            for line in lines
        ):
            continue
        blocks = [b.strip() for b in re.split(r"\n\s*\n", cleaned) if b.strip()]
        count += len(blocks)
    return count


def _latest_mtime(directory: Path) -> float:
    latest = 0.0
    try:
        latest = directory.stat().st_mtime
    except OSError:
        return 0.0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                mt = (Path(root) / name).stat().st_mtime
                if mt > latest:
                    latest = mt
            except OSError:
                continue
    return latest


def prune_empty_repos(storage_root=None, *, grace_hours=24, dry_run=False, now_epoch=None):
    """Remove completely empty repo directories (no active branches, 0 semantic blocks)."""
    root = Path(storage_root) if storage_root else _storage_root()
    now = time.time() if now_epoch is None else float(now_epoch)
    grace_seconds = max(0, float(grace_hours)) * 3600
    pruned = []
    skipped_grace = []
    if not root.is_dir():
        return {"pruned": pruned, "skipped_grace": skipped_grace, "dry_run": bool(dry_run)}

    for repo_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        branches_dir = repo_dir / "branches"
        active_branches = []
        archived_branches = []
        if branches_dir.is_dir():
            for child in branches_dir.iterdir():
                if not child.is_dir():
                    continue
                if child.name == "_archived":
                    archived_branches.extend([c for c in child.iterdir() if c.is_dir()])
                else:
                    active_branches.append(child)
        if active_branches or archived_branches:
            continue
        repo_blocks = sum(_count_semantic_blocks(repo_dir / "repo" / fn) for fn in REPO_SEMANTIC_FILES)
        if repo_blocks > 0:
            continue
        latest = _latest_mtime(repo_dir)
        age_seconds = max(0.0, now - latest)
        if age_seconds < grace_seconds:
            skipped_grace.append({
                "repo_key": repo_dir.name,
                "age_hours": round(age_seconds / 3600, 2),
            })
            continue
        pruned.append({
            "repo_key": repo_dir.name,
            "path": str(repo_dir),
            "age_hours": round(age_seconds / 3600, 1),
        })
        if not dry_run:
            shutil.rmtree(repo_dir, ignore_errors=True)
    return {
        "pruned_count": len(pruned),
        "skipped_grace_count": len(skipped_grace),
        "pruned": pruned,
        "skipped_grace": skipped_grace,
        "dry_run": bool(dry_run),
    }


def detect_repo_key_drift(storage_root=None):
    """Detect local repo roots that have accumulated multiple repo-key directories."""
    root = Path(storage_root) if storage_root else _storage_root()
    if not root.is_dir():
        return []
    by_repo_root = {}
    for repo_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = _read_json(repo_dir / "repo" / "manifest.json", {}) or {}
        repo_root = manifest.get("repo_root")
        if not isinstance(repo_root, str) or not repo_root.strip():
            continue
        norm_root = str(Path(repo_root).expanduser())
        repo_blocks = sum(_count_semantic_blocks(repo_dir / "repo" / fn) for fn in REPO_SEMANTIC_FILES)
        branch_blocks = 0
        bdir = repo_dir / "branches"
        if bdir.is_dir():
            for b in bdir.iterdir():
                if not b.is_dir() or b.name == "_archived":
                    continue
                branch_blocks += sum(_count_semantic_blocks(b / fn) for fn in BRANCH_SEMANTIC_FILES)
        total_blocks = repo_blocks + branch_blocks
        if total_blocks == 0:
            continue
        by_repo_root.setdefault(norm_root, []).append({
            "repo_key": repo_dir.name,
            "path": str(repo_dir),
            "semantic_blocks": total_blocks,
            "updated_at": manifest.get("updated_at"),
        })

    drifts = []
    for repo_root, entries in sorted(by_repo_root.items()):
        if len(entries) < 2:
            continue
        active_key = None
        if Path(repo_root).is_dir():
            try:
                _, _, _, _, active_key, _, _ = get_branch_paths(repo_root, str(root))
            except Exception:
                active_key = None
        drifts.append({
            "repo_root": repo_root,
            "active_repo_key": active_key,
            "repo_keys": sorted(entries, key=lambda x: x["semantic_blocks"], reverse=True),
            "orphaned_keys": [e["repo_key"] for e in entries if active_key and e["repo_key"] != active_key],
        })
    return drifts


def cleanup_placeholder_entries(storage_root=None, *, dry_run=False):
    """Deterministically remove obvious schema placeholder lines (e.g. `- 可独立理解的完整结论`)."""
    root = Path(storage_root) if storage_root else _storage_root()
    cleaned = []
    if not root.is_dir():
        return {"cleaned_count": 0, "files": cleaned, "dry_run": bool(dry_run)}
    for repo_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        bdir = repo_dir / "branches"
        if not bdir.is_dir():
            continue
        for branch_dir in sorted(p for p in bdir.iterdir() if p.is_dir() and p.name != "_archived"):
            for fn in ("decisions.md", "risks.md", "glossary.md"):
                fpath = branch_dir / fn
                if not fpath.is_file():
                    continue
                try:
                    raw = fpath.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                lines = raw.splitlines(keepends=True)
                kept = []
                removed = 0
                for line in lines:
                    stripped = re.sub(r"^[\s\-*]+", "", line).strip()
                    if stripped in EXACT_DIRTY_PLACEHOLDERS:
                        removed += 1
                        continue
                    kept.append(line)
                if removed:
                    new_text = re.sub(r"\n{3,}", "\n\n", "".join(kept))
                    cleaned.append({
                        "repo_key": repo_dir.name,
                        "branch_key": branch_dir.name,
                        "file": fn,
                        "path": str(fpath),
                        "removed_lines": removed,
                    })
                    if not dry_run:
                        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
                        bkp = branch_dir / "artifacts" / "history" / f"placeholder-cleanup-{stamp}"
                        bkp.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fpath, bkp / fn)
                        fpath.write_text(new_text, encoding="utf-8")
    return {
        "cleaned_count": sum(item["removed_lines"] for item in cleaned),
        "files": cleaned,
        "dry_run": bool(dry_run),
    }


def _branch_semantic_fingerprint(repo_dir: Path, branch_dir: Path):
    """Compute a deterministic hash, latest mtime, total blocks, and total bytes for a branch.

    latest_mtime only considers branch-local files so updating repo/ shared memory
    does not falsely mark dozens of dormant branches as recently changed.
    """
    h = hashlib.sha256()
    latest_mtime = 0.0
    total_blocks = 0
    total_bytes = 0
    for path in (branch_dir / fn for fn in BRANCH_SEMANTIC_FILES):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
            mt = path.stat().st_mtime
        except OSError:
            continue
        total_bytes += len(content)
        if mt > latest_mtime:
            latest_mtime = mt
        h.update(b"branch:")
        h.update(str(path.name).encode("utf-8"))
        h.update(b"\x00")
        h.update(content)
        h.update(b"\x00")
        total_blocks += _count_semantic_blocks(path)
    return h.hexdigest(), latest_mtime, total_blocks, total_bytes


def discover_changed_tidy_targets(
    storage_root=None,
    tidy_state=None,
    *,
    lookback_hours=24,
    min_blocks=8,
    bloat_bytes=50 * 1024,
    bloat_blocks=80,
    bloat_recheck_days=7,
    max_targets=5,
    force=False,
    now_epoch=None,
):
    """Find repo+branch memories that changed since last tidy OR crossed bloat thresholds.

    Unchanged normal-sized repos/branches are never touched.
    """
    root = Path(storage_root) if storage_root else _storage_root()
    state = tidy_state if isinstance(tidy_state, dict) else {}
    now = time.time() if now_epoch is None else float(now_epoch)
    cutoff = now - max(1, float(lookback_hours)) * 3600
    bloat_cutoff = now - max(1, float(bloat_recheck_days)) * 86400
    candidates = []
    skipped_unchanged = 0
    if not root.is_dir():
        return [], 0

    for repo_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        repo_manifest = _read_json(repo_dir / "repo" / "manifest.json", {}) or {}
        repo_root = repo_manifest.get("repo_root")
        if not isinstance(repo_root, str) or not Path(repo_root).is_dir():
            continue
        bdir = repo_dir / "branches"
        if not bdir.is_dir():
            continue
        for branch_dir in sorted(p for p in bdir.iterdir() if p.is_dir() and p.name != "_archived"):
            bmanifest = _read_json(branch_dir / "manifest.json", {}) or {}
            branch_name = bmanifest.get("branch") or branch_dir.name
            fp, latest_mtime, total_blocks, total_bytes = _branch_semantic_fingerprint(repo_dir, branch_dir)
            if total_blocks < min_blocks:
                continue
            target_key = f"{repo_dir.name}::{branch_dir.name}"
            prev = state.get(target_key) or {}
            prev_tidied_epoch = 0.0
            if isinstance(prev.get("tidied_at"), str):
                try:
                    prev_tidied_epoch = dt.datetime.fromisoformat(prev["tidied_at"].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    prev_tidied_epoch = 0.0
            is_bloated = total_bytes >= bloat_bytes or total_blocks >= bloat_blocks
            bloat_due = is_bloated and prev_tidied_epoch < bloat_cutoff
            recently_changed = (prev.get("fingerprint") != fp) and (latest_mtime >= cutoff)
            if not force and not recently_changed and not bloat_due:
                skipped_unchanged += 1
                continue
            candidates.append({
                "target_key": target_key,
                "repo_key": repo_dir.name,
                "repo_root": repo_root,
                "repo_dir": str(repo_dir),
                "branch": branch_name,
                "branch_key": branch_dir.name,
                "branch_dir": str(branch_dir),
                "fingerprint": fp,
                "latest_mtime": latest_mtime,
                "total_blocks": total_blocks,
                "total_bytes": total_bytes,
                "trigger": "force" if force else ("changed" if recently_changed else "bloat"),
            })

    candidates.sort(key=lambda item: (item["trigger"] == "changed", item["latest_mtime"]), reverse=True)
    return candidates[: max(0, int(max_targets))], skipped_unchanged


def _list_entries_for_target(target, storage_root):
    all_entries = []
    seen_ids = set()
    for kind in TIDY_KINDS:
        result = subprocess.run(
            [
                sys.executable,
                str(CAPTURE_SCRIPT),
                "list-entries",
                "--repo", target["repo_root"],
                "--branch", target["branch"],
                "--context-dir", str(storage_root),
                "--kind", kind,
                "--limit", "200",
            ],
            cwd=target["repo_root"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        for entry in data.get("entries") or []:
            eid = entry.get("id")
            text = (entry.get("full_text") or entry.get("text") or entry.get("first_line") or "").strip()
            if not eid or not text or eid in seen_ids:
                continue
            seen_ids.add(eid)
            all_entries.append({
                "id": eid,
                "kind": kind,
                "section": entry.get("section") or "",
                "text": text,
            })
    return all_entries


def _backup_branch_files(target, stamp):
    backup_dir = Path(target["branch_dir"]) / "artifacts" / "history" / f"auto-tidy-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for fn in BRANCH_BACKUP_FILES:
        src = Path(target["branch_dir"]) / fn
        if src.is_file():
            shutil.copy2(src, backup_dir / fn)
    repo_backup = backup_dir / "repo"
    repo_backup.mkdir(parents=True, exist_ok=True)
    for fn in REPO_SEMANTIC_FILES:
        src = Path(target["repo_dir"]) / "repo" / fn
        if src.is_file():
            shutil.copy2(src, repo_backup / fn)
    return backup_dir


_GROUP_RULES = {
    "decisions": """重点执行「同主题归并 + 去流水账」：
- 同主题归并（核心）：如果同一项目/模块/机制（如同一系统、工具或流程）在不同日期记录了多条演进决策，必须把当前最终有效的完整规则合并精炼写入其中最新的一条（放入 rewrites），并将被覆盖或过时的旧版本条目放入 deletes。
- 删除（deletes）：一次性测试/排查计划、单次发布版本号/commit hash 流水账、阶段性进度快照、已被后续决策明确放弃的旧方案。
- 保留：仍然生效的长期架构决策、职责边界、协作纪律和硬性约定。""",
    "risks": """重点执行「淘汰已解决历史告警 + 合并同类坑点」：
- 删除（deletes）：特定日期已解决/已验证通过/已缓解的一次性故障记录、临时环境阻塞。
- 改写归并（rewrites + deletes）：反复出现的同类踩坑经验，合并成一条精炼的长期避坑规则，旧的分散条目放入 deletes。
- 保留：未来开发仍容易踩中的架构/工具/权限/环境硬坑。""",
    "glossary": """重点执行「清理过期临时入口，只保留长期资产」：
- 删除（deletes）：距今超过 30 天且属于一次性任务/旧迭代/临时排查的群 ID（如 oc_xxx）、旧 PR/MR 链接、单次日志/截图入口、已结束的调研笔记、与当前工作无关的临时备忘。
- 改写归并（rewrites + deletes）：指向同一平台/仓库/文档的重复入口合并精炼为一条。
- 保留：长期有效的系统文档入口、核心仓库路径、常用命令、稳定的业务术语和架构概念。""",
    "pending": """重点执行「去重 + 淘汰过期/非共享候选」：
- 删除（deletes）：属于一次性任务细节、距今超过 30 天已过时、或与同类条目高度重复的候选；只保留真正具备跨分支复用价值的稳定规则。
- 改写归并（rewrites + deletes）：相似候选合并为简洁的一条规则。""",
    "shared": """重点执行「跨分支共享层高密度精炼」：
- 仅保留对所有分支长期有效的通用决策与入口；删除分支级临时细节或重复条目，把过长条目精炼压缩（rewrites）。""",
}


def _auto_tidy_batch_prompt(target, group_name, batch_entries, today_str, internal_marker):
    rules = _GROUP_RULES.get(group_name, _GROUP_RULES["decisions"])
    compact_entries = [
        {"id": item["id"], "kind": item["kind"], "text": item["text"]}
        for item in batch_entries
    ]
    return f"""{internal_marker}
你是 dev-memory 的每日自动整理器（auto-tidy）。今天日期是 {today_str}。
不要调用任何工具，直接基于下方提供的本批记忆条目（分组={group_name}，共 {len(compact_entries)} 条）输出整理结果。

{rules}

通用要求：
- 所有 id 必须严格来自下方 ENTRIES_JSON，不得编造；同一 id 不能同时出现在 rewrites 和 deletes。
- 合并同主题条目时，优先保留最新或信息最完整的一条放入 rewrites（写清合并后的完整最新规则），其余被吸收的旧条目放入 deletes。
- 只输出一个纯 JSON 对象，不要 markdown fence 或任何解释文字：
  1. 有整理动作：{{"rewrites":[{{"id":"...","content":"合并/精炼后的完整内容","reason":"简短原因"}}],"deletes":[{{"id":"...","reason":"简短原因"}}]}}
  2. 无需整理：{{"skip_reason":"当前条目均有效且紧凑"}}
  空数组请省略，不要输出空字段或额外字段。

ENTRIES_JSON:
{json.dumps(compact_entries, ensure_ascii=False)}
"""


def _overview_refresh_prompt(target, overview_text, recent_decisions, today_str, internal_marker):
    recent_preview = [item["text"][:240] for item in recent_decisions[-12:]]
    return f"""{internal_marker}
你是 dev-memory 的分支概览校准器。今天日期是 {today_str}。
不要调用任何工具，直接判断当前分支 `overview.md` 中的「当前目标」是否已经明显过时（例如停留在 30 天前的旧任务、与最近决策严重脱节）。

当前 overview.md 内容：
{overview_text}

最近最新的分支决策摘录（用于判断当前真实工作重心）：
{json.dumps(recent_preview, ensure_ascii=False)}

要求：
- 如果「当前目标」仍然准确反映当前仓库/分支的长期定位或近期重心，输出：{{"skip_reason":"当前目标仍然有效"}}
- 如果「当前目标」明显过期，请将其更新为简洁的 3~5 条要点（概括当前长期定位与近期核心方向，不写过时日期流水账），输出：
  {{"upserts":[{{"kind":"overview","content":"- 要点1\\n- 要点2\\n- 要点3"}}]}}
- 只输出一个纯 JSON 对象，不要 markdown fence 或额外解释。
"""


def _validate_overview_payload(payload):
    if not isinstance(payload, dict):
        return ["overview output must be a JSON object"]
    allowed = {"upserts", "skip_reason"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return [f"unsupported fields: {', '.join(unknown)}"]
    upserts = payload.get("upserts") or []
    skip_reason = payload.get("skip_reason")
    has_skip = isinstance(skip_reason, str) and bool(skip_reason.strip())
    if not upserts and not has_skip:
        return ["overview output must contain upserts or skip_reason"]
    errors = []
    for i, item in enumerate(upserts):
        if not isinstance(item, dict) or item.get("kind") != "overview":
            errors.append(f"upserts[{i}].kind must be 'overview'")
        elif not isinstance(item.get("content"), str) or not item["content"].strip():
            errors.append(f"upserts[{i}].content must be non-empty")
    return errors


def _validate_auto_tidy_payload(payload, valid_ids):
    if not isinstance(payload, dict):
        return ["tidy output must be a JSON object"]
    allowed = {"rewrites", "deletes", "skip_reason"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        return [f"unsupported fields: {', '.join(unknown)}"]
    errors = []
    rewrites = payload.get("rewrites") or []
    deletes = payload.get("deletes") or []
    if "rewrites" in payload and not isinstance(rewrites, list):
        errors.append("rewrites must be an array")
        rewrites = []
    if "deletes" in payload and not isinstance(deletes, list):
        errors.append("deletes must be an array")
        deletes = []
    seen_ids = set()
    for i, item in enumerate(rewrites):
        if not isinstance(item, dict):
            errors.append(f"rewrites[{i}] must be an object")
            continue
        eid = item.get("id")
        content = item.get("content")
        if not isinstance(eid, str) or eid not in valid_ids:
            errors.append(f"rewrites[{i}].id is invalid: {eid}")
        elif eid in seen_ids:
            errors.append(f"duplicate id in tidy output: {eid}")
        else:
            seen_ids.add(eid)
        if not isinstance(content, str) or not content.strip():
            errors.append(f"rewrites[{i}].content must be non-empty")
    for i, item in enumerate(deletes):
        if not isinstance(item, dict):
            errors.append(f"deletes[{i}] must be an object")
            continue
        eid = item.get("id")
        if not isinstance(eid, str) or eid not in valid_ids:
            errors.append(f"deletes[{i}].id is invalid: {eid}")
        elif eid in seen_ids:
            errors.append(f"duplicate id in tidy output: {eid}")
        else:
            seen_ids.add(eid)
    skip_reason = payload.get("skip_reason")
    has_skip = isinstance(skip_reason, str) and bool(skip_reason.strip())
    if not rewrites and not deletes and not has_skip:
        errors.append("tidy output must contain rewrites/deletes or skip_reason")
    # Safety guard: refuse pure mass-deletion without any merge/rewrite.
    if (
        valid_ids
        and not rewrites
        and len(deletes) >= 15
        and (len(deletes) / len(valid_ids)) > MAX_PURE_DELETE_RATIO
    ):
        errors.append(
            f"safety guard: refused pure deletion of {len(deletes)}/{len(valid_ids)} entries "
            f"(>{int(MAX_PURE_DELETE_RATIO * 100)}%)"
        )
    return errors


def _entry_sort_key(eid):
    parts = str(eid or "").split("::")
    try:
        return (parts[0], int(parts[1]), int(parts[2]))
    except (IndexError, ValueError):
        return (str(eid), 0, 0)


def _apply_auto_tidy(target, payload, storage_root):
    normalized = {}
    if payload.get("upserts"):
        normalized["upserts"] = payload["upserts"]
    if payload.get("rewrites"):
        normalized["rewrites"] = payload["rewrites"]
    if payload.get("deletes"):
        # Delete in reverse index order per section so earlier deletes do not shift later indices.
        normalized["deletes"] = sorted(
            payload["deletes"],
            key=lambda item: _entry_sort_key(item.get("id")),
            reverse=True,
        )
    result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE_SCRIPT),
            "apply-summary-output",
            "--repo", target["repo_root"],
            "--branch", target["branch"],
            "--context-dir", str(storage_root),
            "--json", json.dumps(normalized, ensure_ascii=False),
        ],
        cwd=target["repo_root"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError((result.stderr or result.stdout or "apply-summary-output failed").strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip()}


def tidy_single_target(
    scanner_module,
    config,
    target,
    storage_root,
    *,
    run_id,
    executor_name=None,
    preset=None,
    dry_run=False,
):
    """Run batched inline auto-tidy + overview refresh for one repo+branch target."""
    entries = _list_entries_for_target(target, storage_root)
    row = {
        "repo_key": target["repo_key"],
        "branch": target["branch"],
        "repo_root": target["repo_root"],
        "entry_count": len(entries),
        "status": "dry_run" if dry_run else "pending",
        "rewrites_count": 0,
        "deletes_count": 0,
        "upserts_count": 0,
        "batches": [],
        "summary_usage": None,
        "error": None,
    }
    if not entries:
        row["status"] = "skipped_summary"
        row["skip_reason"] = "no_mutable_entries"
        return row, [], {}
    if dry_run:
        return row, [], {}

    today_str = dt.datetime.now().strftime("%Y-%m-%d")
    invocations = []
    merged_rewrites = []
    merged_deletes = []
    merged_upserts = []
    seen_mutation_ids = set()
    batch_errors = []

    batch_jobs = []
    for group_name, kinds in TIDY_GROUPS:
        group_entries = [e for e in entries if e["kind"] in kinds]
        if len(group_entries) < 3:
            continue
        for batch_idx in range(0, len(group_entries), TIDY_BATCH_SIZE):
            batch = group_entries[batch_idx : batch_idx + TIDY_BATCH_SIZE]
            batch_jobs.append((group_name, batch_idx, batch))

    def _run_batch(job):
        group_name, batch_idx, batch = job
        batch_ids = {e["id"] for e in batch}
        prompt = _auto_tidy_batch_prompt(
            target, group_name, batch, today_str, scanner_module.INTERNAL_MARKER
        )
        inv_name = f"daily:{target['repo_key']}:{target['branch_key']}:{group_name}:{batch_idx // TIDY_BATCH_SIZE}"
        payload, records = scanner_module.run_executor_with_retries(
            executor_name,
            preset,
            prompt,
            target["repo_root"],
            run_id,
            inv_name,
            int(config.get("max_attempts", 2)),
            lambda p, vids=batch_ids: _validate_auto_tidy_payload(p, vids),
        )
        return group_name, batch_idx, len(batch), payload, records

    max_workers = max(1, min(4, int(config.get("daily_tidy_concurrency", 4) or 4), len(batch_jobs) or 1))
    if max_workers > 1 and len(batch_jobs) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            batch_outputs = list(pool.map(_run_batch, batch_jobs))
    else:
        batch_outputs = [_run_batch(job) for job in batch_jobs]

    for group_name, batch_idx, batch_len, payload, records in batch_outputs:
        invocations.extend(records)
        if payload is None:
            err = records[-1].get("error", "batch tidy failed") if records else "batch tidy failed"
            batch_errors.append(f"{group_name}[{batch_idx}]: {err}")
            row["batches"].append({"group": group_name, "count": batch_len, "status": "failed", "error": err})
            continue
        b_rew = 0
        b_del = 0
        for item in payload.get("rewrites") or []:
            eid = item.get("id")
            if eid and eid not in seen_mutation_ids:
                seen_mutation_ids.add(eid)
                merged_rewrites.append(item)
                b_rew += 1
        for item in payload.get("deletes") or []:
            eid = item.get("id")
            if eid and eid not in seen_mutation_ids:
                seen_mutation_ids.add(eid)
                merged_deletes.append(item)
                b_del += 1
        row["batches"].append({
            "group": group_name,
            "count": batch_len,
            "status": "done" if (b_rew or b_del) else "skipped",
            "rewrites": b_rew,
            "deletes": b_del,
        })

    # Optional overview.md staleness refresh when branch has active decisions.
    overview_path = Path(target["branch_dir"]) / "overview.md"
    decision_entries = [e for e in entries if e["kind"] == "decision"]
    if overview_path.is_file() and len(decision_entries) >= 5:
        overview_text = overview_path.read_text(encoding="utf-8", errors="ignore").strip()
        if overview_text:
            ov_prompt = _overview_refresh_prompt(
                target, overview_text, decision_entries, today_str, scanner_module.INTERNAL_MARKER
            )
            ov_payload, ov_records = scanner_module.run_executor_with_retries(
                executor_name,
                preset,
                ov_prompt,
                target["repo_root"],
                run_id,
                f"daily:{target['repo_key']}:{target['branch_key']}:overview",
                int(config.get("max_attempts", 2)),
                _validate_overview_payload,
            )
            invocations.extend(ov_records)
            if ov_payload and ov_payload.get("upserts"):
                merged_upserts.extend(ov_payload["upserts"])
                row["batches"].append({"group": "overview", "count": 1, "status": "done", "upserts": len(ov_payload["upserts"])})
            elif ov_payload:
                row["batches"].append({"group": "overview", "count": 1, "status": "skipped"})

    usage, _ = scanner_module._sum_usage(invocations)
    row["summary_usage"] = usage

    combined_payload = {}
    if merged_upserts:
        combined_payload["upserts"] = merged_upserts
    if merged_rewrites:
        combined_payload["rewrites"] = merged_rewrites
    if merged_deletes:
        combined_payload["deletes"] = merged_deletes

    if not combined_payload:
        if batch_errors and not row["batches"]:
            row["status"] = "failed"
            row["error"] = "; ".join(batch_errors)
        elif batch_errors and all(b["status"] == "failed" for b in row["batches"]):
            row["status"] = "failed"
            row["error"] = "; ".join(batch_errors)
        else:
            row["status"] = "skipped_summary"
            row["skip_reason"] = "no_changes"
        return row, invocations, combined_payload

    try:
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_dir = _backup_branch_files(target, stamp)
        row["backup_dir"] = str(backup_dir)
        row["apply_result"] = _apply_auto_tidy(target, combined_payload, storage_root)
        row["rewrites_count"] = len(merged_rewrites)
        row["deletes_count"] = len(merged_deletes)
        row["upserts_count"] = len(merged_upserts)
        row["status"] = "done"
    except Exception as exc:
        row["status"] = "failed"
        row["error"] = str(exc)
    return row, invocations, combined_payload


def run_daily_housekeeping(
    scanner_module,
    config,
    *,
    storage_root=None,
    dry_run=False,
    scheduled=False,
    force=False,
    skip_prune=False,
    skip_tidy=False,
    max_targets=None,
    executor_override=None,
):
    """Run daily maintenance: prune empty repos, detect drift, and auto-tidy changed branches."""
    import tempfile

    started = time.time()
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-daily-{os.getpid()}"
    root = Path(storage_root) if storage_root else _storage_root()
    scan_root = scanner_module.SCAN_ROOT
    tidy_state_path = scan_root / "daily-tidy-state.json"
    tidy_state = _read_json(tidy_state_path, {}) or {}

    prune_report = (
        {"skipped": True, "pruned_count": 0, "pruned": [], "skipped_grace": []}
        if skip_prune
        else prune_empty_repos(
            root,
            grace_hours=config.get("empty_repo_grace_hours", 24),
            dry_run=dry_run,
        )
    )
    placeholder_report = cleanup_placeholder_entries(root, dry_run=dry_run)
    drift_reports = detect_repo_key_drift(root)

    limit = max_targets if max_targets is not None else int(config.get("daily_tidy_max_targets", 5))
    targets, skipped_unchanged = (
        ([], 0)
        if skip_tidy
        else discover_changed_tidy_targets(
            root,
            tidy_state,
            lookback_hours=24 if not force else 24 * 365,
            max_targets=limit,
            force=force,
        )
    )

    executor_name = None
    preset = None
    if targets and not dry_run:
        executor_config = {**config, "executor": executor_override} if executor_override else config
        executor_name, preset = scanner_module.choose_executor(executor_config)
        preset = dict(preset)
        preset["_timeout_seconds"] = int(config.get("invocation_timeout_seconds", 600))
        preset["_event_log_path"] = str(scan_root / "logs" / "executor-events.jsonl")

    invocations = []
    tidy_results = []
    done_count = 0
    summary_skipped_count = 0
    failed_count = 0

    for target in targets:
        row, target_invocations, _combined = tidy_single_target(
            scanner_module,
            config,
            target,
            root,
            run_id=run_id,
            executor_name=executor_name,
            preset=preset,
            dry_run=dry_run,
        )
        invocations.extend(target_invocations)
        if row["status"] == "done":
            done_count += 1
            new_fp, _, _, _ = _branch_semantic_fingerprint(Path(target["repo_dir"]), Path(target["branch_dir"]))
            tidy_state[target["target_key"]] = {
                "fingerprint": new_fp,
                "tidied_at": now_iso(),
                "status": "done",
                "rewrites_count": row["rewrites_count"],
                "deletes_count": row["deletes_count"],
                "upserts_count": row.get("upserts_count", 0),
            }
        elif row["status"] == "skipped_summary":
            summary_skipped_count += 1
            if not dry_run:
                tidy_state[target["target_key"]] = {
                    "fingerprint": target["fingerprint"],
                    "tidied_at": now_iso(),
                    "status": "skipped_summary",
                }
        elif row["status"] == "failed":
            failed_count += 1
        tidy_results.append(row)

    if not dry_run:
        _atomic_json(tidy_state_path, tidy_state)

    total_usage, unavailable = scanner_module._sum_usage(invocations)
    finished = now_iso()
    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_kind": "daily_housekeeping",
        "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
        "finished_at": finished,
        "duration_ms": int((time.time() - started) * 1000),
        "dry_run": bool(dry_run),
        "scheduled": bool(scheduled),
        "executor": executor_name,
        "model": preset.get("model") if preset else None,
        "prune": prune_report,
        "placeholder_cleanup": placeholder_report,
        "drift_count": len(drift_reports),
        "drifts": drift_reports,
        "tidy": {
            "target_count": len(targets),
            "skipped_unchanged_count": skipped_unchanged,
            "done_count": done_count,
            "summary_skipped_count": summary_skipped_count,
            "failed_count": failed_count,
            "results": tidy_results,
        },
        # Compatible top-level fields so dev-memory-cli ui dashboard counts daily runs & tokens.
        "session_count": len(targets),
        "candidate_count": len(targets),
        "done_count": done_count,
        "summary_skipped_count": summary_skipped_count,
        "failed_count": failed_count,
        "skipped_count": skipped_unchanged + summary_skipped_count,
        "discovery_skipped_count": skipped_unchanged,
        "raw_bytes": 0,
        "new_bytes": 0,
        "observed_new_bytes": 0,
        "eligible_new_bytes": 0,
        "skipped_new_bytes": 0,
        "semantic_messages": 0,
        "semantic_chars": 0,
        "summary_usage": total_usage,
        "usage_unavailable_invocations": unavailable,
        "invocations": invocations,
        "sessions": [
            {
                "session_id": f"daily:{r['repo_key']}:{r['branch']}",
                "repo_key": r["repo_key"],
                "branch": r["branch"],
                "cwd": r["repo_root"],
                "status": r["status"],
                "error": r.get("error"),
                "summary_usage": r.get("summary_usage"),
                "semantic_action_count": r.get("rewrites_count", 0) + r.get("deletes_count", 0),
                "last_scanned_at": finished,
            }
            for r in tidy_results
        ],
    }

    if not dry_run:
        _atomic_json(scan_root / "housekeeping" / f"{run_id}.json", report)
        _atomic_json(scan_root / "last-housekeeping.json", report)
        _atomic_json(scan_root / "runs" / f"{run_id}.json", report)
        _atomic_json(scan_root / "daily-housekeeping-state.json", {
            "schema_version": SCHEMA_VERSION,
            "last_run_date": dt.datetime.now().strftime("%Y-%m-%d"),
            "last_run_id": run_id,
            "updated_at": finished,
        })
    return report
