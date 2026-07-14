#!/usr/bin/env python3

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dev_memory_common import get_branch_paths, list_repos_in_workspace, now_iso


SCHEMA_VERSION = 1
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("DEV_MEMORY_CONFIG_PATH", "~/.dev-memory/config.json")).expanduser()
DEV_MEMORY_HOME = Path(os.environ.get("DEV_MEMORY_HOME", "~/.dev-memory")).expanduser()
SCAN_ROOT = Path(os.environ.get("DEV_MEMORY_SCAN_ROOT", DEV_MEMORY_HOME / "jobs" / "session-scan")).expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
PLIST_PATH = Path(os.environ.get(
    "DEV_MEMORY_SCAN_PLIST",
    "~/Library/LaunchAgents/com.dev-memory.session-scan.plist",
)).expanduser()
INTERNAL_MARKER = "DEV_MEMORY_INTERNAL_SESSION_SUMMARY_V1"
MAINTENANCE_MARKER = "DEV_MEMORY_INTERNAL_MAINTENANCE_AGENT_V1"
INTERNAL_MARKERS = (INTERNAL_MARKER, MAINTENANCE_MARKER)
SUMMARY_MUTATION_FIELDS = (
    "file_map",
    "decisions",
    "risks",
    "glossary",
    "shared_decisions",
    "shared_context",
    "shared_sources",
    "upserts",
    "appends",
    "rewrites",
    "deletes",
)
SUMMARY_PLACEHOLDER_WORDS = {
    "decision",
    "summary",
    "reason",
    "impact",
    "risk",
    "mitigation",
    "term",
    "definition",
    "name",
    "url",
    "note",
    "label",
    "path",
    "content",
}


DEFAULT_EXECUTORS = {
    "coco": {
        "enabled": True,
        "command": "coco",
        "model": None,
        "profile": None,
        "extra_args": [],
        "env": {},
    },
    "codex": {
        "enabled": True,
        "command": "codex",
        "model": None,
        "profile": None,
        "extra_args": [],
        "env": {},
    },
    "claude": {
        "enabled": True,
        "command": "claude",
        "model": None,
        "profile": None,
        "extra_args": [],
        "env": {},
    },
}


def _read_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _deep_merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def default_scan_config():
    return {
        "executor": "auto",
        "order": ["coco", "codex", "claude"],
        "executors": json.loads(json.dumps(DEFAULT_EXECUTORS)),
        "schedule_times": ["03:00", "13:00"],
        "skip_when_computer_active": True,
        "active_within_minutes": 10,
        "activity_check_fail_closed": True,
        "chunk_chars": 60000,
        "idle_minutes": 60,
        "first_lookback_days": 3,
        "max_attempts": 2,
        "invocation_timeout_seconds": 360,
    }


def load_config():
    root = _read_json(CONFIG_PATH, {})
    root = root if isinstance(root, dict) else {}
    section = root.get("session_scan")
    return _deep_merge(default_scan_config(), section if isinstance(section, dict) else {})


def save_scan_config(section):
    root = _read_json(CONFIG_PATH, {})
    root = root if isinstance(root, dict) else {}
    root["session_scan"] = section
    _atomic_json(CONFIG_PATH, root)


def validate_config(config):
    errors = []
    warnings = []
    selected = config.get("executor", "auto")
    executors = config.get("executors")
    if not isinstance(executors, dict) or not executors:
        errors.append("session_scan.executors must be a non-empty object")
        executors = {}
    if selected != "auto" and selected not in executors:
        errors.append(f"executor '{selected}' has no preset")
    order = config.get("order")
    if not isinstance(order, list) or not order:
        errors.append("session_scan.order must be a non-empty list")
    for name, preset in executors.items():
        if not isinstance(preset, dict):
            errors.append(f"executor '{name}' must be an object")
            continue
        command = preset.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"executor '{name}' requires command")
        if name == "codex" and "--ephemeral" in (preset.get("extra_args") or []):
            warnings.append("codex.extra_args does not need --ephemeral; the scanner enforces it")
    try:
        if int(config.get("chunk_chars", 0)) < 1000:
            errors.append("chunk_chars must be at least 1000")
    except (TypeError, ValueError):
        errors.append("chunk_chars must be an integer")
    schedules = config.get("schedule_times")
    if not isinstance(schedules, list) or not schedules:
        errors.append("schedule_times must be a non-empty list")
    else:
        for value in schedules:
            try:
                _parse_schedule_time(value)
            except ValueError as exc:
                errors.append(str(exc))
    try:
        if int(config.get("active_within_minutes", 0)) < 1:
            errors.append("active_within_minutes must be at least 1")
    except (TypeError, ValueError):
        errors.append("active_within_minutes must be an integer")
    try:
        if int(config.get("invocation_timeout_seconds", 0)) < 30:
            errors.append("invocation_timeout_seconds must be at least 30")
    except (TypeError, ValueError):
        errors.append("invocation_timeout_seconds must be an integer")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _parse_schedule_time(value):
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
    if not match:
        raise ValueError(f"invalid schedule time '{value}', expected HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid schedule time '{value}', expected HH:MM")
    return hour, minute


def _calendar_intervals(config):
    unique = sorted({_parse_schedule_time(value) for value in config.get("schedule_times", [])})
    return [{"Hour": hour, "Minute": minute} for hour, minute in unique]


def _mac_idle_seconds():
    if sys.platform != "darwin":
        return None
    result = subprocess.run(
        ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', result.stdout or "")
    if not match:
        return None
    return int(match.group(1)) / 1_000_000_000


def computer_activity(config):
    threshold_seconds = int(config.get("active_within_minutes", 10)) * 60
    idle_seconds = _mac_idle_seconds()
    if idle_seconds is None:
        return {
            "status": "unknown",
            "idle_seconds": None,
            "threshold_seconds": threshold_seconds,
            "skip": bool(config.get("activity_check_fail_closed", True)),
        }
    return {
        "status": "active" if idle_seconds < threshold_seconds else "idle",
        "idle_seconds": round(idle_seconds, 3),
        "threshold_seconds": threshold_seconds,
        "skip": idle_seconds < threshold_seconds,
    }


def choose_executor(config):
    executors = config.get("executors", {})
    selected = config.get("executor", "auto")
    names = config.get("order", []) if selected == "auto" else [selected]
    for name in names:
        preset = executors.get(name)
        if not isinstance(preset, dict) or preset.get("enabled") is False:
            continue
        command = preset.get("command", name)
        executable = shlex.split(command)[0] if command else ""
        if executable and (Path(executable).exists() or shutil.which(executable)):
            return name, preset
    raise RuntimeError(f"no available session-scan executor in {names}")


def _content_text(content):
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"tool_use", "tool_result", "function_call", "function_call_output"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _semantic_message(obj):
    if obj.get("type") != "response_item":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in {"user", "assistant"}:
        return None
    text = _content_text(payload.get("content"))
    if not text:
        return None
    return {"role": role, "text": text, "timestamp": obj.get("timestamp")}


def _usage_dict(value):
    if not isinstance(value, dict):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens", "cache_read_input_tokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    out = {}
    for target, keys in aliases.items():
        for key in keys:
            if isinstance(value.get(key), (int, float)):
                out[target] = int(value[key])
                break
    if out and "total_tokens" not in out:
        out["total_tokens"] = out.get("input_tokens", 0) + out.get("output_tokens", 0)
    return out or None


def parse_codex_session(path, since_offset=0):
    path = Path(path)
    meta = {}
    messages = []
    total_usage = None
    internal_marker = False
    end_offset = since_offset
    with path.open("rb") as stream:
        if since_offset:
            while True:
                raw = stream.readline()
                if not raw:
                    break
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                    meta = obj["payload"]
                    break
            stream.seek(since_offset)
        while True:
            start = stream.tell()
            raw = stream.readline()
            if not raw:
                break
            end = stream.tell()
            end_offset = end
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                meta = obj["payload"]
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                usage = _usage_dict(info.get("total_token_usage"))
                if usage:
                    total_usage = usage
            message = _semantic_message(obj)
            if message and any(marker in message["text"] for marker in INTERNAL_MARKERS):
                internal_marker = True
            if message and end > since_offset:
                message.update({"start_offset": start, "end_offset": end})
                messages.append(message)
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
        "end_offset": end_offset,
        "meta": meta,
        "messages": messages,
        "session_usage": total_usage,
        "internal_marker": internal_marker,
    }


def _codex_session_meta(path):
    with Path(path).open("rb") as stream:
        for raw in stream:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                return obj["payload"]
    return {}


def _session_files(lookback_days=None, cutoff_epoch=None):
    roots = [CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions"]
    cutoff = cutoff_epoch
    if cutoff is None and lookback_days is not None:
        cutoff = time.time() - (lookback_days * 86400)
    found = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                if cutoff is not None and path.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            found.append(path)
    return sorted(found, key=lambda p: (p.stat().st_mtime, str(p)))


def _state_path(session_id):
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", session_id)
    return SCAN_ROOT / "state" / f"{safe}.json"


def _session_audit_path(session_id):
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", session_id)
    return SCAN_ROOT / "sessions" / f"{safe}.json"


def _workspace_primary(cwd):
    for name in (".dev-memory-workspace.json", ".dev-assets-workspace.json"):
        data = _read_json(Path(cwd) / name, {})
        if isinstance(data, dict) and data.get("primary_repo"):
            return data["primary_repo"]
    return None


def resolve_target(cwd):
    if not cwd:
        return None, "missing_cwd"
    root = Path(cwd).expanduser()
    if not root.exists() or not root.is_dir():
        return None, "cwd_not_found"
    repos = list_repos_in_workspace(str(root))
    if repos:
        primary = _workspace_primary(root)
        if primary:
            root = next((repo for repo in repos if repo.name == primary), None)
            if root is None:
                return None, "workspace_primary_not_found"
        elif len(repos) == 1:
            root = repos[0]
        else:
            return None, "workspace_primary_required"
    try:
        repo_root, branch, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(str(root))
    except Exception as exc:
        return None, f"repo_resolution_failed:{exc}"
    if not branch_dir.exists():
        return None, "memory_not_initialized"
    return {
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "repo_dir": str(repo_dir),
        "branch": branch,
        "branch_key": branch_key,
        "branch_dir": str(branch_dir),
        "storage_root": str(storage_root),
    }, None


def _chunk_messages(messages, max_chars):
    expanded = []
    for message in messages:
        text = message["text"]
        if len(text) <= max_chars:
            expanded.append(message)
            continue
        segment_count = (len(text) + max_chars - 1) // max_chars
        for index in range(segment_count):
            expanded.append({
                **message,
                "text": text[index * max_chars:(index + 1) * max_chars],
                "segment_index": index + 1,
                "segment_count": segment_count,
            })
    chunks = []
    current = []
    current_chars = 0
    for message in expanded:
        text = message["text"]
        if current and current_chars + len(text) > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(message)
        current_chars += len(text)
        if current_chars >= max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
    if current:
        chunks.append(current)
    return chunks


def _existing_memory(target):
    paths = [
        Path(target["branch_dir"]) / name
        for name in ("overview.md", "decisions.md", "risks.md", "glossary.md")
    ] + [
        Path(target["repo_dir"]) / "repo" / name
        for name in ("decisions.md", "glossary.md")
    ]
    return [
        {"path": str(path), "content": path.read_text(encoding="utf-8")}
        for path in paths if path.exists()
    ]


def _partial_prompt(target, chunk, index, total):
    material = [{"role": item["role"], "text": item["text"]} for item in chunk]
    return f"""{INTERNAL_MARKER}
你是 dev-memory 的后台会话总结器。下面是仓库 {target['repo_key']} 分支 {target['branch']} 的第 {index}/{total} 个连续会话分块。
完整阅读本分块，不要忽略前部内容。只提炼在未来开发会话中仍有价值的决策、约束、风险、术语、命令、外部入口和功能文件定位；不要记录聊天流水账、普通进展或可从 Git 直接恢复的历史。
只输出 JSON 对象，允许字段：decisions、risks、glossary、file_map、shared_decisions、shared_context、shared_sources、skip_reason。字段内容沿用 summary-output 语义。
MESSAGES_JSON:
{json.dumps(material, ensure_ascii=False)}
"""


def _single_prompt(target, chunk):
    payload = {
        "existing_memory": _existing_memory(target),
        "messages": [{"role": item["role"], "text": item["text"]} for item in chunk],
    }
    return f"""{INTERNAL_MARKER}
你是 dev-memory 的后台会话总结器。阅读完整会话与 existing_memory，直接生成最终 summary-output，不要先输出中间摘要。
只保留未来开发会话中仍有价值、且 existing_memory 尚未覆盖的决策、约束、风险、术语、命令、外部入口和功能文件定位；不要记录聊天流水账、普通进展或可从 Git 直接恢复的历史。
旧结论失效时使用 rewrites/deletes，不要追加矛盾条目。如果没有任何需要写入、改写或删除的内容，必须返回非空 skip_reason；禁止只返回 title 或空对象。
字段 schema：decisions/shared_decisions 是对象数组，每项使用 summary（完整结论）、可选 reason、可选 impact；risks/glossary/shared_context/shared_sources 是完整自然语言字符串数组；file_map 每项为 {{"label":"功能说明","paths":["真实相对路径"]}}；upserts/appends 每项必须有 kind 和 content；rewrites 每项必须有 id/content/reason；deletes 每项必须有 id/reason。
所有值必须来自会话事实并可独立理解。禁止输出 decision/summary/reason/impact/risk/mitigation/term/definition/name/url/note/path/content 等 schema 占位词，禁止留空 summary。
只输出一个 JSON 对象，不要 markdown fence。允许字段：title、file_map、decisions、risks、glossary、shared_decisions、shared_context、shared_sources、upserts、appends、rewrites、deletes、skip_reason。
INPUT_JSON:
{json.dumps(payload, ensure_ascii=False)}
"""


def _final_prompt(target, partials):
    payload = {"existing_memory": _existing_memory(target), "partial_summaries": partials}
    return f"""{INTERNAL_MARKER}
你是 dev-memory 的后台会话总结器。把所有 partial_summaries 与 existing_memory 合并为一个最终 summary-output。
旧结论失效时使用 rewrites/deletes，不要追加矛盾条目；重复内容跳过。不要写当前进展、下一步或提交历史。
如果没有任何需要写入、改写或删除的内容，必须返回非空 skip_reason；禁止只返回 title 或空对象。
decisions/shared_decisions 每项必须有非空 summary；risks/glossary/shared_context/shared_sources 必须是完整自然语言字符串；file_map 每项必须有 label 和真实 paths。禁止输出 schema 占位词或空 summary。
只输出一个 JSON 对象，不要 markdown fence。允许字段：title、file_map、decisions、risks、glossary、shared_decisions、shared_context、shared_sources、upserts、appends、rewrites、deletes、skip_reason。
INPUT_JSON:
{json.dumps(payload, ensure_ascii=False)}
"""


def _summary_payload_meta(payload):
    payload = payload if isinstance(payload, dict) else {}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    field_counts = {}
    for key in SUMMARY_MUTATION_FIELDS:
        value = payload.get(key)
        if isinstance(value, list):
            field_counts[key] = len(value)
        elif value:
            field_counts[key] = 1
    skip_reason = payload.get("skip_reason")
    skip_reason = skip_reason.strip() if isinstance(skip_reason, str) else None
    return {
        "keys": sorted(payload),
        "field_counts": field_counts,
        "mutation_count": sum(field_counts.values()),
        "skip_reason": skip_reason or None,
        "chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _looks_like_placeholder_text(value):
    if not isinstance(value, str):
        return False
    words = []
    for line in value.splitlines():
        normalized = re.sub(r"^[\s>*+-]+", "", line).strip().strip(":：").lower()
        if normalized:
            words.append(normalized)
    return bool(words) and all(word in SUMMARY_PLACEHOLDER_WORDS for word in words)


def _summary_payload_validation_errors(payload):
    errors = []
    if not isinstance(payload, dict):
        return ["summary output must be an object"]
    for field in ("decisions", "shared_decisions"):
        for index, item in enumerate(payload.get(field) or []):
            if isinstance(item, str):
                summary = item.strip()
            elif isinstance(item, dict):
                summary = str(item.get("summary") or item.get("decision") or "").strip()
            else:
                summary = ""
            if not summary:
                errors.append(f"{field}[{index}] requires a non-empty summary")
            elif _looks_like_placeholder_text(summary):
                errors.append(f"{field}[{index}] contains schema placeholder text")
    for field in ("risks", "glossary", "shared_context", "shared_sources"):
        for index, item in enumerate(payload.get(field) or []):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"{field}[{index}] must be a non-empty string")
            elif _looks_like_placeholder_text(item):
                errors.append(f"{field}[{index}] contains schema placeholder text")
    for index, item in enumerate(payload.get("file_map") or []):
        paths = item.get("paths") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not str(item.get("label") or "").strip() or not paths:
            errors.append(f"file_map[{index}] requires label and paths")
    return errors


def _semantic_action_count(apply_result):
    actions = apply_result.get("actions") if isinstance(apply_result, dict) else []
    return sum(
        1
        for action in (actions or [])
        if isinstance(action, dict) and not str(action.get("op") or "").startswith("prune-")
    )


def _executor_args(name, preset, prompt):
    command = shlex.split(preset.get("command", name))
    model = preset.get("model")
    profile = preset.get("profile")
    extra = [str(item) for item in (preset.get("extra_args") or [])]
    if name == "coco":
        args = command + ["-p", "--yolo", "--output-format", "json"]
        if model:
            args += ["-c", f"model={model}"]
        if profile:
            args += ["-c", f"profile={profile}"]
        args += extra + [prompt]
    elif name == "codex":
        args = command + [
            "exec", "--ephemeral", "--json", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "danger-full-access",
        ]
        if model:
            args += ["--model", str(model)]
        if profile:
            args += ["--profile", str(profile)]
        args += extra + [prompt]
    elif name == "claude":
        args = command + ["-p", "--permission-mode", "bypassPermissions", "--output-format", "json", "--no-session-persistence"]
        if model:
            args += ["--model", str(model)]
        args += extra + [prompt]
    else:
        args = command + extra
        args = [part.replace("{prompt}", prompt) for part in args]
        if not any("{prompt}" in part for part in command + extra):
            args.append(prompt)
    return args


def _find_json_object(text):
    text = (text or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError("executor output contains no JSON object")


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _parse_executor_output(name, stdout, stderr):
    records = []
    for line in stdout.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not records:
        try:
            records = [json.loads(stdout)]
        except json.JSONDecodeError:
            records = []
    usage = None
    session_id = None
    candidates = []
    for record in records:
        for item in _walk_dicts(record):
            for key in ("usage", "token_usage", "total_token_usage"):
                parsed_usage = _usage_dict(item.get(key))
                if parsed_usage:
                    usage = parsed_usage
            session_id = session_id or item.get("thread_id") or item.get("session_id")
            for key in ("result", "text", "output_text"):
                if isinstance(item.get(key), str):
                    candidates.append(item[key])
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                candidates.append(item["text"])
    last_error = None
    for candidate in reversed(candidates):
        try:
            return _find_json_object(candidate), usage, session_id
        except ValueError as exc:
            last_error = exc
    for candidate in (stdout, stderr):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                for key in ("result", "text", "output_text"):
                    if isinstance(value.get(key), str):
                        return _find_json_object(value[key]), usage, session_id
                return value, usage, session_id
        except json.JSONDecodeError:
            try:
                return _find_json_object(candidate), usage, session_id
            except ValueError as exc:
                last_error = exc
    raise last_error or ValueError(f"unable to parse {name} output")


def run_executor(name, preset, prompt, cwd, run_id, invocation):
    args = _executor_args(name, preset, prompt)
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (preset.get("env") or {}).items()})
    started = time.time()
    timeout_seconds = int(preset.get("_timeout_seconds", 360))
    record = {
        "invocation": invocation,
        "stage": "final" if ":final" in invocation else "partial",
        "executor": name,
        "model": preset.get("model"),
        "profile": preset.get("profile"),
        "prompt_chars": len(prompt),
        "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
        "duration_ms": 0,
        "returncode": None,
        "usage_status": "unavailable",
    }
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        record.update({
            "duration_ms": int((time.time() - started) * 1000),
            "returncode": None,
            "timed_out": True,
            "timeout_seconds": timeout_seconds,
            "error": f"executor timed out after {timeout_seconds} seconds",
        })
        return None, record
    record["duration_ms"] = int((time.time() - started) * 1000)
    record["returncode"] = result.returncode
    if result.returncode != 0:
        record["error"] = (result.stderr or result.stdout or "executor failed")[-4000:]
        return None, record
    try:
        payload, usage, session_id = _parse_executor_output(name, result.stdout, result.stderr)
        record["output"] = _summary_payload_meta(payload)
        record["usage"] = usage
        record["usage_status"] = "reported" if usage else "unavailable"
        record["internal_session_id"] = session_id
        if session_id:
            _append_jsonl(SCAN_ROOT / "internal-sessions.jsonl", {
                "at": now_iso(), "run_id": run_id, "executor": name, "session_id": session_id,
            })
        return payload, record
    except Exception as exc:
        record["error"] = str(exc)
        return None, record


def run_executor_with_retries(name, preset, prompt, cwd, run_id, invocation, max_attempts):
    records = []
    payload = None
    for attempt in range(1, max(1, max_attempts) + 1):
        attempt_prompt = prompt
        if attempt > 1:
            attempt_prompt += "\n上一次调用失败。请重新阅读输入，只输出一个合法 JSON 对象。"
        payload, record = run_executor(
            name, preset, attempt_prompt, cwd, run_id, f"{invocation}:attempt:{attempt}"
        )
        record["attempt"] = attempt
        records.append(record)
        if payload is not None:
            break
        if record.get("timed_out"):
            break
    return payload, records


def _apply_summary(target, payload):
    result = subprocess.run(
        [sys.executable, str(PACKAGE_ROOT / "lib" / "dev_memory_capture.py"), "apply-summary-output", "--repo", target["repo_root"], "--json", json.dumps(payload, ensure_ascii=False)],
        cwd=target["repo_root"], capture_output=True, text=True, check=False,
    )
    if result.returncode not in (0, 2):
        raise RuntimeError((result.stderr or result.stdout or "apply-summary-output failed").strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip()}


def _sum_usage(invocations):
    total = {}
    unavailable = 0
    for item in invocations:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            unavailable += 1
            continue
        for key, value in usage.items():
            if isinstance(value, int):
                total[key] = total.get(key, 0) + value
    return total or None, unavailable


def _internal_ids():
    ids = set()
    path = SCAN_ROOT / "internal-sessions.jsonl"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value.get("session_id"):
                ids.add(value["session_id"])
    except (OSError, json.JSONDecodeError):
        pass
    return ids


def discover(config, since=None):
    origin = _read_json(SCAN_ROOT / "scan-origin.json", {}) or {}
    first_run = not origin
    lookback = config.get("first_lookback_days", 3) if first_run and since is None else None
    cutoff_epoch = origin.get("initial_cutoff_epoch") if origin and since is None else None
    if since:
        parsed = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
        cutoff_epoch = parsed.astimezone(dt.timezone.utc).timestamp()
        lookback = None
    internal_ids = _internal_ids()
    sessions = []
    idle_seconds = int(config.get("idle_minutes", 60)) * 60
    source_by_session = {}
    for path in _session_files(lookback, cutoff_epoch=cutoff_epoch):
        meta = _codex_session_meta(path)
        session_id = meta.get("session_id") or meta.get("id") or path.stem
        previous = source_by_session.get(session_id)
        if previous is None or (path.stat().st_size, path.stat().st_mtime) > (
            previous[0].stat().st_size, previous[0].stat().st_mtime
        ):
            source_by_session[session_id] = (path, meta)
    for session_id, (path, meta) in sorted(
        source_by_session.items(), key=lambda item: (item[1][0].stat().st_mtime, str(item[1][0]))
    ):
        stat = path.stat()
        state = _read_json(_state_path(session_id), {}) or {}
        cursor = int(state.get("processed_offset", 0))
        if cursor and stat.st_size <= cursor:
            parsed = {
                "path": str(path),
                "size": stat.st_size,
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                "end_offset": cursor,
                "meta": meta,
                "messages": [],
                "session_usage": state.get("session_usage"),
                "internal_marker": bool(state.get("internal_marker")),
            }
        else:
            parsed = parse_codex_session(path, cursor)
        reason = None
        if session_id in internal_ids or str(session_id).startswith("dev-memory-summary-") or parsed["internal_marker"]:
            reason = "excluded_internal"
        elif meta.get("thread_source") == "automation":
            reason = "excluded_automation"
        elif time.time() - path.stat().st_mtime < idle_seconds:
            reason = "not_idle"
        elif parsed["size"] <= cursor:
            reason = "unchanged"
        target, target_error = resolve_target(meta.get("cwd"))
        if not reason and target_error:
            reason = target_error
        sessions.append({
            **parsed,
            "session_id": session_id,
            "cursor_before": cursor,
            "new_bytes": max(0, parsed["size"] - cursor),
            "target": target,
            "status": "candidate" if not reason else "skipped",
            "reason": reason,
        })
    return sessions


def _persist_scan_run(run, event):
    run_id = run["run_id"]
    _atomic_json(SCAN_ROOT / "runs" / f"{run_id}.json", run)
    _atomic_json(SCAN_ROOT / "last-run.json", run)
    _append_jsonl(SCAN_ROOT / "events.jsonl", {"at": now_iso(), "event": event, "run_id": run_id})


def _skipped_activity_run(run_id, started, activity, *, dry_run=False):
    status = "skipped_active" if activity["status"] == "active" else "skipped_activity_unknown"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "skip_reason": activity["status"],
        "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
        "finished_at": now_iso(),
        "duration_ms": int((time.time() - started) * 1000),
        "scheduled": True,
        "dry_run": bool(dry_run),
        "activity": activity,
        "executor": None,
        "model": None,
        "profile": None,
        "session_count": 0,
        "candidate_count": 0,
        "done_count": 0,
        "summary_skipped_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "discovery_skipped_count": 0,
        "raw_bytes": 0,
        "new_bytes": 0,
        "observed_new_bytes": 0,
        "eligible_new_bytes": 0,
        "skipped_new_bytes": 0,
        "semantic_messages": 0,
        "semantic_chars": 0,
        "summary_usage": None,
        "usage_unavailable_invocations": 0,
        "invocations": [],
        "sessions": [],
    }


def _advance_session_state(session):
    state_path = _state_path(session["session_id"])
    existing = _read_json(state_path, {}) or {}
    existing_offset = int(existing.get("processed_offset", 0))
    if existing_offset >= session["end_offset"]:
        return existing_offset
    _atomic_json(state_path, {
        "schema_version": SCHEMA_VERSION,
        "session_id": session["session_id"],
        "path": session["path"],
        "processed_offset": session["end_offset"],
        "raw_size": session["size"],
        "sha256": hashlib.sha256(Path(session["path"]).read_bytes()).hexdigest(),
        "session_usage": session["session_usage"],
        "internal_marker": session["internal_marker"],
        "repo_key": session["target"]["repo_key"],
        "branch": session["target"]["branch"],
        "updated_at": now_iso(),
    })
    return session["end_offset"]


def _execute_sessions(config, args, sessions, run_id, started, *, activity=None, run_kind="scan", replay_source=None):
    candidates = [item for item in sessions if item["status"] == "candidate" and item["messages"]]
    executor_name = None
    preset = None
    if candidates and not args.dry_run:
        executor_override = getattr(args, "executor", None)
        executor_config = {**config, "executor": executor_override} if executor_override else config
        executor_name, preset = choose_executor(executor_config)
        preset = dict(preset)
        preset["_timeout_seconds"] = int(config.get("invocation_timeout_seconds", 360))
    invocations = []
    results = []
    for session in sessions:
        audit = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session["session_id"],
            "path": session["path"],
            "originator": session["meta"].get("originator"),
            "source": session["meta"].get("source"),
            "thread_source": session["meta"].get("thread_source"),
            "cwd": session["meta"].get("cwd"),
            "raw_size": session["size"],
            "cursor_before": session["cursor_before"],
            "new_bytes": session["new_bytes"],
            "semantic_messages": len(session["messages"]),
            "semantic_chars": sum(len(item["text"]) for item in session["messages"]),
            "session_usage": session["session_usage"],
            "repo_key": (session["target"] or {}).get("repo_key"),
            "branch": (session["target"] or {}).get("branch"),
            "status": session["status"],
            "reason": session["reason"],
            "last_scanned_at": now_iso(),
        }
        if session.get("replay_source"):
            audit["replay_source"] = session["replay_source"]
        if session not in candidates:
            if audit["status"] == "candidate":
                audit["status"] = "skipped"
                audit["reason"] = "no_semantic_messages"
            results.append(audit)
            _atomic_json(_session_audit_path(session["session_id"]), audit)
            continue
        chunks = _chunk_messages(session["messages"], int(config.get("chunk_chars", 60000)))
        audit["chunk_count"] = len(chunks)
        audit["chunks"] = [
            {
                "index": index,
                "start_offset": chunk[0]["start_offset"],
                "end_offset": chunk[-1]["end_offset"],
                "messages": len(chunk),
                "chars": sum(len(item["text"]) for item in chunk),
                "sha256": hashlib.sha256("\n".join(item["text"] for item in chunk).encode()).hexdigest(),
            }
            for index, chunk in enumerate(chunks, 1)
        ]
        if args.dry_run:
            audit["status"] = "dry_run"
            results.append(audit)
            _atomic_json(_session_audit_path(session["session_id"]), audit)
            continue
        failed = None
        invocation_start = len(invocations)
        final_payload = None
        if len(chunks) == 1:
            final_payload, attempt_records = run_executor_with_retries(
                executor_name, preset, _single_prompt(session["target"], chunks[0]),
                session["target"]["repo_root"], run_id, f"{session['session_id']}:final",
                int(config.get("max_attempts", 2)),
            )
            invocations.extend(attempt_records)
            if final_payload is None:
                failed = attempt_records[-1].get("error", "final summary failed")
        else:
            partials = []
            for index, chunk in enumerate(chunks, 1):
                payload, attempt_records = run_executor_with_retries(
                    executor_name, preset, _partial_prompt(session["target"], chunk, index, len(chunks)),
                    session["target"]["repo_root"], run_id, f"{session['session_id']}:chunk:{index}",
                    int(config.get("max_attempts", 2)),
                )
                invocations.extend(attempt_records)
                if payload is None:
                    failed = attempt_records[-1].get("error", "chunk summary failed")
                    break
                partials.append(payload)
            if not failed:
                final_payload, attempt_records = run_executor_with_retries(
                    executor_name, preset, _final_prompt(session["target"], partials),
                    session["target"]["repo_root"], run_id, f"{session['session_id']}:final",
                    int(config.get("max_attempts", 2)),
                )
                invocations.extend(attempt_records)
                if final_payload is None:
                    failed = attempt_records[-1].get("error", "final summary failed")
        if not failed:
            summary_output = _summary_payload_meta(final_payload)
            validation_errors = _summary_payload_validation_errors(final_payload)
            if validation_errors:
                summary_output["validation_errors"] = validation_errors
            audit["summary_output"] = summary_output
            if validation_errors:
                failed = "invalid final summary output: " + "; ".join(validation_errors)
            elif not summary_output["mutation_count"] and not summary_output["skip_reason"]:
                failed = "final summary output has no memory mutations or skip_reason"
        if not failed and audit["summary_output"]["mutation_count"]:
            try:
                audit["apply_result"] = _apply_summary(session["target"], final_payload)
                audit["semantic_action_count"] = _semantic_action_count(audit["apply_result"])
                if not audit["semantic_action_count"]:
                    if audit["summary_output"]["skip_reason"]:
                        audit["status"] = "skipped_summary"
                    else:
                        failed = "summary output produced no semantic memory actions or skip_reason"
                else:
                    audit["status"] = "done"
            except Exception as exc:
                failed = str(exc)
        elif not failed:
            audit["status"] = "skipped_summary"
            audit["semantic_action_count"] = 0
            audit["apply_result"] = {
                "mode": "not-applied",
                "touched_targets": [],
                "actions": [],
                "skip_reason": audit["summary_output"]["skip_reason"],
            }
        if not failed and audit["status"] in {"done", "skipped_summary"}:
            audit["cursor_after"] = session["end_offset"]
            audit["state_offset_after"] = _advance_session_state(session)
        if failed:
            audit["status"] = "failed"
            audit["error"] = failed
        audit["summary_usage"], audit["usage_unavailable_invocations"] = _sum_usage(invocations[invocation_start:])
        audit["executor"] = executor_name
        audit["model"] = preset.get("model")
        results.append(audit)
        _atomic_json(_session_audit_path(session["session_id"]), audit)
    usage, unavailable = _sum_usage(invocations)
    run = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_kind": run_kind,
        "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
        "finished_at": now_iso(),
        "duration_ms": int((time.time() - started) * 1000),
        "dry_run": bool(args.dry_run),
        "scheduled": bool(getattr(args, "scheduled", False)),
        "activity": activity,
        "executor": executor_name,
        "model": preset.get("model") if preset else None,
        "profile": preset.get("profile") if preset else None,
        "session_count": len(results),
        "candidate_count": len(candidates),
        "done_count": sum(item["status"] == "done" for item in results),
        "summary_skipped_count": sum(item["status"] == "skipped_summary" for item in results),
        "failed_count": sum(item["status"] == "failed" for item in results),
        "skipped_count": sum(item["status"] in {"skipped", "skipped_summary"} for item in results),
        "discovery_skipped_count": sum(item["status"] == "skipped" for item in results),
        "raw_bytes": sum(item["raw_size"] for item in results),
        "new_bytes": sum(item["new_bytes"] for item in results),
        "observed_new_bytes": sum(item["new_bytes"] for item in results),
        "eligible_new_bytes": sum(item["new_bytes"] for item in results if item["status"] != "skipped"),
        "skipped_new_bytes": sum(item["new_bytes"] for item in results if item["status"] == "skipped"),
        "semantic_messages": sum(item["semantic_messages"] for item in results),
        "semantic_chars": sum(item["semantic_chars"] for item in results),
        "summary_usage": usage,
        "usage_unavailable_invocations": unavailable,
        "invocations": invocations,
        "sessions": results,
    }
    if replay_source:
        run["replay_source"] = replay_source
    _persist_scan_run(run, "scan_completed")
    print(json.dumps(run, ensure_ascii=False, indent=2) if args.json else _format_run(run))
    return 1 if run["failed_count"] else 0


def run_scan(args):
    config = load_config()
    validation = validate_config(config)
    if not validation["valid"]:
        raise RuntimeError("; ".join(validation["errors"]))
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    started = time.time()
    activity = None
    if getattr(args, "scheduled", False) and config.get("skip_when_computer_active", True):
        activity = computer_activity(config)
        if activity["skip"]:
            run = _skipped_activity_run(run_id, started, activity, dry_run=args.dry_run)
            _persist_scan_run(run, run["status"])
            print(json.dumps(run, ensure_ascii=False, indent=2) if args.json else _format_run(run))
            return 0
    origin_path = SCAN_ROOT / "scan-origin.json"
    if not origin_path.exists():
        lookback_days = int(config.get("first_lookback_days", 3))
        _atomic_json(origin_path, {
            "created_at": now_iso(),
            "initial_cutoff_epoch": time.time() - lookback_days * 86400,
            "first_lookback_days": lookback_days,
        })
    sessions = discover(config, args.since)
    return _execute_sessions(config, args, sessions, run_id, started, activity=activity)


def _format_tokens(usage):
    return str((usage or {}).get("total_tokens", "unavailable"))


def _format_run(run):
    if str(run.get("status", "")).startswith("skipped_"):
        activity = run.get("activity") or {}
        return (
            f"run {run['run_id']}: {run['status']}; "
            f"idle_seconds={activity.get('idle_seconds')} threshold={activity.get('threshold_seconds')}"
        )
    return (
        f"run {run['run_id']}: {run['done_count']} done, "
        f"{run.get('summary_skipped_count', 0)} summary-skipped, {run['failed_count']} failed, "
        f"{run.get('discovery_skipped_count', run['skipped_count'])} discovery-skipped; "
        f"{run.get('eligible_new_bytes', run['new_bytes'])} eligible / "
        f"{run.get('observed_new_bytes', run['new_bytes'])} observed new bytes; "
        f"summary tokens {_format_tokens(run.get('summary_usage'))}"
    )


def _runs():
    return [value for path in sorted((SCAN_ROOT / "runs").glob("*.json"), reverse=True) if isinstance((value := _read_json(path)), dict)]


def _covered_bytes(intervals):
    total = 0
    end = None
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def command_stats(args):
    runs = _runs()
    if args.since:
        runs = [item for item in runs if (item.get("started_at") or "") >= args.since]
    repos = {}
    total_usage = {}
    unavailable = 0
    for run in runs:
        usage = run.get("summary_usage") or {}
        for key, value in usage.items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value
        unavailable += int(run.get("usage_unavailable_invocations", 0))
        for session in run.get("sessions", []):
            key = session.get("repo_key")
            if not key or (args.repo and key != args.repo):
                continue
            item = repos.setdefault(key, {
                "repo_key": key,
                "scan_count": 0,
                "sessions": set(),
                "intervals": {},
                "raw_bytes": 0,
                "new_bytes": 0,
                "observed_new_bytes": 0,
                "eligible_new_bytes": 0,
                "skipped_new_bytes": 0,
                "summary_tokens": 0,
                "done_count": 0,
                "summary_skipped_count": 0,
                "failed_count": 0,
                "empty_apply_count": 0,
            })
            item["scan_count"] += 1
            session_id = session.get("session_id")
            item["sessions"].add(session_id)
            item["raw_bytes"] += int(session.get("raw_size", 0))
            observed = int(session.get("new_bytes", 0))
            item["new_bytes"] += observed
            item["observed_new_bytes"] += observed
            if session.get("status") == "skipped":
                item["skipped_new_bytes"] += observed
            else:
                item["eligible_new_bytes"] += observed
            start = int(session.get("cursor_before", 0))
            stop = min(int(session.get("raw_size", start + observed)), start + observed)
            item["intervals"].setdefault(session_id, []).append((start, stop))
            item["summary_tokens"] += int((session.get("summary_usage") or {}).get("total_tokens", 0))
            status = session.get("status")
            item["done_count"] += int(status == "done")
            item["summary_skipped_count"] += int(status == "skipped_summary")
            item["failed_count"] += int(status == "failed")
            item["empty_apply_count"] += int(
                status == "done"
                and not (session.get("apply_result") or {}).get("touched_targets")
            )
    repo_rows = []
    for item in repos.values():
        item["session_count"] = len(item.pop("sessions"))
        item["unique_new_bytes"] = sum(_covered_bytes(value) for value in item.pop("intervals").values())
        repo_rows.append(item)
    payload = {"run_count": len(runs), "summary_usage": total_usage or None, "usage_unavailable_invocations": unavailable, "repos": sorted(repo_rows, key=lambda x: x["repo_key"])}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_history(args):
    runs = _runs()
    if args.repo:
        runs = [run for run in runs if any(item.get("repo_key") == args.repo for item in run.get("sessions", []))]
    runs = runs[:args.limit]
    print(json.dumps(runs, ensure_ascii=False, indent=2) if args.json else "\n".join(_format_run(run) for run in runs))


def command_show(args):
    path = SCAN_ROOT / "runs" / f"{args.run_id}.json"
    value = _read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError(f"run not found: {args.run_id}")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _replay_session(source_run_id, audit):
    session_id = audit.get("session_id")
    path = Path(audit.get("path") or "").expanduser()
    if not path.is_file():
        matches = [
            candidate
            for root in (CODEX_HOME / "sessions", CODEX_HOME / "archived_sessions")
            if root.exists()
            for candidate in root.rglob(f"*{session_id}*.jsonl")
        ]
        if not matches:
            raise RuntimeError(f"session transcript not found: {session_id}")
        path = max(matches, key=lambda item: (item.stat().st_size, item.stat().st_mtime))
    cursor_before = int(audit.get("cursor_before", 0))
    cursor_after = int(audit.get("cursor_after") or audit.get("raw_size") or 0)
    parsed = parse_codex_session(path, cursor_before)
    if cursor_after <= cursor_before or cursor_after > parsed["size"]:
        raise RuntimeError(
            f"invalid replay cursor for {session_id}: {cursor_before}..{cursor_after}, size={parsed['size']}"
        )
    parsed["messages"] = [
        item for item in parsed["messages"]
        if item["start_offset"] >= cursor_before and item["end_offset"] <= cursor_after
    ]
    parsed["size"] = cursor_after
    parsed["end_offset"] = cursor_after
    parsed["session_usage"] = audit.get("session_usage") or parsed.get("session_usage")
    meta = dict(parsed.get("meta") or {})
    if audit.get("cwd"):
        meta["cwd"] = audit["cwd"]
    parsed["meta"] = meta
    target, target_error = resolve_target(meta.get("cwd"))
    return {
        **parsed,
        "session_id": session_id,
        "cursor_before": cursor_before,
        "new_bytes": cursor_after - cursor_before,
        "target": target,
        "status": "candidate" if not target_error else "skipped",
        "reason": target_error,
        "replay_source": {
            "run_id": source_run_id,
            "previous_status": audit.get("status"),
            "previous_apply_empty": not bool((audit.get("apply_result") or {}).get("touched_targets")),
        },
    }


def command_replay(args):
    config = load_config()
    validation = validate_config(config)
    if not validation["valid"]:
        raise RuntimeError("; ".join(validation["errors"]))
    source_path = SCAN_ROOT / "runs" / f"{args.run_id}.json"
    source_run = _read_json(source_path)
    if not isinstance(source_run, dict):
        raise RuntimeError(f"run not found: {args.run_id}")
    requested = list(dict.fromkeys(args.session_id))
    audits = {item.get("session_id"): item for item in source_run.get("sessions", [])}
    missing = [session_id for session_id in requested if session_id not in audits]
    if missing:
        raise RuntimeError(f"sessions not found in run {args.run_id}: {', '.join(missing)}")
    sessions = [_replay_session(args.run_id, audits[session_id]) for session_id in requested]
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-replay-{os.getpid()}"
    return _execute_sessions(
        config,
        args,
        sessions,
        run_id,
        time.time(),
        run_kind="replay",
        replay_source={"run_id": args.run_id, "session_ids": requested},
    )


def _cli_path():
    explicit = os.environ.get("DEV_MEMORY_CLI_PATH", "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    installed = shutil.which("dev-memory-cli")
    return installed or str(PACKAGE_ROOT / "bin" / "dev-memory.js")


def command_install(_args):
    config = load_config()
    save_scan_config(config)
    logs = SCAN_ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.dev-memory.session-scan",
        "ProgramArguments": [_cli_path(), "session-scan", "run", "--scheduled"],
        "StartCalendarInterval": _calendar_intervals(config),
        "RunAtLoad": False,
        "StandardOutPath": str(logs / "launchd.stdout.log"),
        "StandardErrorPath": str(logs / "launchd.stderr.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
    }
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as stream:
        plistlib.dump(plist, stream)
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True, check=False)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "launchctl load failed")
    print(json.dumps({
        "installed": True,
        "plist": str(PLIST_PATH),
        "schedule_times": config["schedule_times"],
        "skip_when_computer_active": config["skip_when_computer_active"],
        "active_within_minutes": config["active_within_minutes"],
        "logs": str(logs),
    }, ensure_ascii=False, indent=2))


def command_uninstall(_args):
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True, check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print(json.dumps({"installed": False, "plist": str(PLIST_PATH)}, ensure_ascii=False, indent=2))


def command_status(_args):
    config = load_config()
    validation = validate_config(config)
    try:
        executor, preset = choose_executor(config)
    except RuntimeError:
        executor, preset = None, None
    print(json.dumps({
        "installed": PLIST_PATH.exists(),
        "plist": str(PLIST_PATH),
        "scan_root": str(SCAN_ROOT),
        "codex_sessions": str(CODEX_HOME / "sessions"),
        "executor": executor,
        "model": preset.get("model") if preset else None,
        "schedule_times": config.get("schedule_times"),
        "skip_when_computer_active": config.get("skip_when_computer_active"),
        "active_within_minutes": config.get("active_within_minutes"),
        "config": validation,
        "last_run": _read_json(SCAN_ROOT / "last-run.json"),
    }, ensure_ascii=False, indent=2))


def command_config(args):
    config = load_config()
    if args.config_command == "show":
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return
    if args.config_command == "validate":
        result = validate_config(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["valid"]:
            raise SystemExit(1)
        return
    if args.config_command == "set-executor":
        if args.executor != "auto" and args.executor not in config["executors"]:
            raise RuntimeError(f"unknown executor preset: {args.executor}")
        config["executor"] = args.executor
    elif args.config_command in {"set-model", "set-profile"}:
        if args.executor not in config["executors"]:
            raise RuntimeError(f"unknown executor preset: {args.executor}")
        field = "model" if args.config_command == "set-model" else "profile"
        config["executors"][args.executor][field] = args.value
    elif args.config_command == "set-schedule":
        for value in args.times:
            _parse_schedule_time(value)
        config["schedule_times"] = args.times
    elif args.config_command == "set-active-minutes":
        if args.minutes < 1:
            raise RuntimeError("active minutes must be at least 1")
        config["active_within_minutes"] = args.minutes
    elif args.config_command == "set-timeout":
        if args.seconds < 30:
            raise RuntimeError("timeout seconds must be at least 30")
        config["invocation_timeout_seconds"] = args.seconds
    elif args.config_command == "set-active-check":
        config["skip_when_computer_active"] = args.enabled == "on"
    save_scan_config(config)
    if args.config_command == "set-schedule" and PLIST_PATH.exists():
        command_install(args)
        return
    print(json.dumps(config, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Scan Codex sessions into dev-memory")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--since")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true")
    run.add_argument("--scheduled", action="store_true", help="Apply computer activity guard before scanning")
    run.add_argument("--executor", help="Override the configured executor for this run only")
    sub.add_parser("install")
    sub.add_parser("status")
    stats = sub.add_parser("stats")
    stats.add_argument("--repo")
    stats.add_argument("--since")
    stats.add_argument("--json", action="store_true")
    history = sub.add_parser("history")
    history.add_argument("--repo")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("run_id")
    replay = sub.add_parser("replay")
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--session-id", action="append", required=True)
    replay.add_argument("--dry-run", action="store_true")
    replay.add_argument("--json", action="store_true")
    replay.add_argument("--executor", help="Override the configured executor for this replay only")
    sub.add_parser("uninstall")
    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show")
    config_sub.add_parser("validate")
    set_executor = config_sub.add_parser("set-executor")
    set_executor.add_argument("executor")
    for command in ("set-model", "set-profile"):
        item = config_sub.add_parser(command)
        item.add_argument("executor")
        item.add_argument("value")
    set_schedule = config_sub.add_parser("set-schedule")
    set_schedule.add_argument("times", nargs="+")
    set_active_minutes = config_sub.add_parser("set-active-minutes")
    set_active_minutes.add_argument("minutes", type=int)
    set_timeout = config_sub.add_parser("set-timeout")
    set_timeout.add_argument("seconds", type=int)
    set_active_check = config_sub.add_parser("set-active-check")
    set_active_check.add_argument("enabled", choices=("on", "off"))
    return parser


def main():
    args = build_parser().parse_args()
    handlers = {
        "run": run_scan,
        "install": command_install,
        "status": command_status,
        "stats": command_stats,
        "history": command_history,
        "show": command_show,
        "replay": command_replay,
        "uninstall": command_uninstall,
        "config": command_config,
    }
    try:
        return handlers[args.command](args) or 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
