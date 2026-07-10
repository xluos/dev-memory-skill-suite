#!/usr/bin/env python3

import argparse
import atexit
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CAPTURE_SCRIPT,
    REPO_ROOT,
    _atomic_write_json,
    _write_summary_input,
    build_summary_input,
    build_summary_prompt,
    now_iso,
)


ALLOWED_KEYS = {
    "title",
    "decisions",
    "risks",
    "glossary",
    "file_map",
    "shared_decisions",
    "shared_context",
    "shared_sources",
    "upserts",
    "appends",
    "rewrites",
    "deletes",
    "skip_reason",
}


def _extract_json_object(text):
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fenced:
        return json.loads(fenced.group(1))
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in agent output")


def _ensure_str(value, field):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string")


def validate_summary_output(payload):
    if not isinstance(payload, dict):
        raise ValueError("summary output must be a JSON object")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    for field in ("title", "skip_reason"):
        _ensure_str(payload.get(field), field)
    for field in ("risks", "glossary", "shared_context", "shared_sources"):
        value = payload.get(field)
        if value is not None and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            raise ValueError(f"{field} must be a list of strings")
    for field in ("decisions", "shared_decisions"):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValueError(f"{field} must be a list")
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"{field}[{idx}] must be an object")
            summary = item.get("summary") or item.get("decision")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError(f"{field}[{idx}] requires summary")
            for sub in ("reason", "impact"):
                _ensure_str(item.get(sub), f"{field}[{idx}].{sub}")
    file_map = payload.get("file_map")
    if file_map is not None:
        if not isinstance(file_map, list):
            raise ValueError("file_map must be a list")
        for idx, item in enumerate(file_map):
            if not isinstance(item, dict):
                raise ValueError(f"file_map[{idx}] must be an object")
            if not isinstance(item.get("label"), str) or not item["label"].strip():
                raise ValueError(f"file_map[{idx}] requires label")
            paths_val = item.get("paths") or ([item["path"]] if item.get("path") else [])
            if not paths_val or not all(isinstance(p, str) for p in paths_val):
                raise ValueError(f"file_map[{idx}] requires paths (list of strings) or path (string)")
    for field in ("upserts", "appends"):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValueError(f"{field} must be a list")
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"{field}[{idx}] must be an object")
            _ensure_str(item.get("kind"), f"{field}[{idx}].kind")
            _ensure_str(item.get("content"), f"{field}[{idx}].content")
            if not item.get("kind") or not item.get("content"):
                raise ValueError(f"{field}[{idx}] requires kind and content")
    for field in ("rewrites", "deletes"):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise ValueError(f"{field} must be a list")
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"{field}[{idx}] must be an object")
            _ensure_str(item.get("id"), f"{field}[{idx}].id")
            if not item.get("id"):
                raise ValueError(f"{field}[{idx}] requires id")
            if field == "rewrites":
                _ensure_str(item.get("content"), f"{field}[{idx}].content")
                if not item.get("content"):
                    raise ValueError(f"{field}[{idx}] requires content")
            _ensure_str(item.get("reason"), f"{field}[{idx}].reason")
    return payload


def _agent_args(command, *, prompt, job_path, summary_input_path, summary_session_id, summary_session_uuid):
    args = []
    for arg in shlex.split(command):
        args.append(
            arg.replace("{prompt}", prompt)
            .replace("{job}", str(job_path))
            .replace("{summary_input}", str(summary_input_path))
            .replace("{summary_session_id}", summary_session_id)
            .replace("{summary_session_uuid}", summary_session_uuid)
        )
    if "{prompt}" not in command:
        args.append(prompt)
    executable = Path(args[0]).name if args else ""
    if executable == "codex" and "exec" in args and "--ephemeral" not in args:
        args.insert(args.index("exec") + 1, "--ephemeral")
    return args


def _run_agent(command, *, prompt, job_path, summary_input_path, summary_session_id, summary_session_uuid):
    args = _agent_args(
        command,
        prompt=prompt,
        job_path=job_path,
        summary_input_path=summary_input_path,
        summary_session_id=summary_session_id,
        summary_session_uuid=summary_session_uuid,
    )
    print("[dev-memory] agent command:", " ".join(shlex.quote(a) for a in args[:6]), "...")
    result = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "args0": args[0] if args else None,
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


def _retry_prompt(base_prompt, error, previous_output, attempt):
    return (
        base_prompt
        + "\n\n上一次输出格式校验失败，请只重新输出一个合法 JSON 对象，不要输出解释文字。"
        + f"\n失败次数: {attempt}"
        + f"\n错误信息: {error}"
        + "\n上一次输出:"
        + f"\n{(previous_output or '')[:4000]}"
    )


def _run_apply_summary_output(summary_input, valid_payload):
    result = subprocess.run(
        [
            "python3",
            str(CAPTURE_SCRIPT),
            "apply-summary-output",
            "--repo",
            summary_input["job"]["repo_root"],
            "--json",
            json.dumps(valid_payload, ensure_ascii=False),
        ],
        cwd=summary_input["job"]["repo_root"],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (result.stdout or "").strip()
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    if result.returncode == 0:
        return parsed or {}, None
    if result.returncode == 2 and isinstance(parsed, dict):
        # apply-summary-output uses exit 2 for dedup warnings after writing
        # all non-blocked changes. That is not a worker failure.
        return parsed, None
    error = (result.stderr or raw or f"apply-summary-output exited {result.returncode}").strip()
    return None, error


def _move_job(job_path, dest_dir, patch):
    src = Path(job_path)
    try:
        job = json.loads(src.read_text(encoding="utf-8"))
    except FileNotFoundError:
        job = {"job_id": src.stem}
    job.update(patch)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    _atomic_write_json(dest, job)
    try:
        src.unlink()
    except FileNotFoundError:
        pass
    return dest


def _acquire_lock(queue_dir, job_id):
    """Try to acquire a PID-based lock. Returns lock path on success, None if another worker is alive."""
    locks_dir = Path(queue_dir) / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / f"{job_id}.lock"
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(existing_pid, 0)
                return None
            except (OSError, ProcessLookupError):
                pass
        except (ValueError, OSError):
            pass
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return lock_path


def _release_lock(lock_path):
    try:
        if lock_path and lock_path.exists():
            content = lock_path.read_text(encoding="utf-8").strip()
            if content == str(os.getpid()):
                lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def run_worker(args):
    job_path = Path(args.job)
    queue_dir = Path(args.queue_dir)
    job_id = args.job_id or job_path.stem

    if not job_path.exists():
        for state in ("done", "skipped", "failed"):
            if (queue_dir / state / f"{job_id}.json").exists():
                print(f"[dev-memory] worker exit: job {job_id} already {state}", file=sys.stderr)
                return 0
        print(f"[dev-memory] worker exit: pending file not found for {job_id}", file=sys.stderr)
        return 0

    lock_path = _acquire_lock(queue_dir, job_id)
    if lock_path is None:
        print(f"[dev-memory] worker exit: another worker already running for {job_id}", file=sys.stderr)
        return 0
    atexit.register(_release_lock, lock_path)

    summary_session_id = args.summary_session_id or f"dev-memory-summary-{job_id}"
    summary_session_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dev-memory-summary:{summary_session_id}"))
    summary_input = build_summary_input(job_path)
    summary_input_path = _write_summary_input(queue_dir, job_id, summary_input)
    base_prompt = build_summary_prompt(job_path, summary_input=summary_input, summary_input_path=summary_input_path)
    base_prompt += (
        "\n\n最终要求：只输出 summary-output JSON 对象。"
        "不要调用任何命令，不要写文件，不要移动 job，不要输出 markdown fence 或解释文字。"
    )

    attempts = []
    valid_payload = None
    apply_payload = None
    max_attempts = max(1, args.max_attempts)
    for attempt in range(1, max_attempts + 1):
        prompt = base_prompt if attempt == 1 else _retry_prompt(base_prompt, attempts[-1]["error"], attempts[-1].get("raw_output"), attempt - 1)
        result = _run_agent(
            args.agent_command,
            prompt=prompt,
            job_path=job_path,
            summary_input_path=summary_input_path,
            summary_session_id=summary_session_id,
            summary_session_uuid=summary_session_uuid,
        )
        raw_output = (result["stdout"] or "") + ("\n" + result["stderr"] if result["stderr"] else "")
        record = {
            "attempt": attempt,
            "summary_session_id": summary_session_id,
            "summary_session_uuid": summary_session_uuid,
            "returncode": result["returncode"],
            "raw_output": raw_output[:12000],
        }
        try:
            if result["returncode"] != 0:
                raise ValueError(f"agent command exited {result['returncode']}")
            parsed = _extract_json_object(raw_output)
            valid_payload = validate_summary_output(parsed)
            record["valid"] = True
            record["parsed"] = valid_payload
            apply_payload, apply_error = _run_apply_summary_output(summary_input, valid_payload)
            if apply_error:
                record["apply_error"] = apply_error
                record["error"] = f"apply-summary-output failed: {apply_error}"
                attempts.append(record)
                print(f"[dev-memory] summary apply attempt {attempt} failed: {apply_error}", file=sys.stderr)
                valid_payload = None
                continue
            record["apply_result"] = apply_payload
            attempts.append(record)
            break
        except Exception as exc:
            record["valid"] = False
            record["error"] = str(exc)
            attempts.append(record)
            print(f"[dev-memory] summary attempt {attempt} failed: {exc}", file=sys.stderr)

    if valid_payload is None:
        dest = _move_job(
            job_path,
            queue_dir / "failed",
            {
                "status": "failed",
                "failed_at": now_iso(),
                "summary_session_id": summary_session_id,
                "summary_session_uuid": summary_session_uuid,
                "summary_input_path": str(summary_input_path),
                "summary_attempts": attempts,
                "error": attempts[-1]["error"] if attempts else "summary generation failed",
            },
        )
        print(f"[dev-memory] summary job failed -> {dest}")
        return 1

    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        job = {}
    ts = job.get("transcript_state") or {}
    actions = [
        f"{item.get('op')}:{item.get('kind') or item.get('id') or item.get('source')}"
        for item in apply_payload.get("actions", [])
    ]
    touched_targets = apply_payload.get("touched_targets") or []
    if not touched_targets:
        processed_at = now_iso()
        dest = _move_job(
            job_path,
            queue_dir / "skipped",
            {
                "status": "skipped",
                "skipped_at": processed_at,
                "processed": {
                    "processed_at": processed_at,
                    "transcript_size": ts.get("size"),
                    "transcript_mtime_ms": ts.get("mtime_ms"),
                    "actions": actions,
                    "apply_result": apply_payload,
                },
                "summary_session_id": summary_session_id,
                "summary_session_uuid": summary_session_uuid,
                "summary_input_path": str(summary_input_path),
                "summary_attempts": attempts,
                "summary_output": valid_payload,
                "skip_reason": apply_payload.get("skip_reason")
                or valid_payload.get("skip_reason")
                or "summary output touched no targets",
            },
        )
        print(f"[dev-memory] summary job skipped -> {dest}")
        return 0

    dest = _move_job(
        job_path,
        queue_dir / "done",
        {
            "status": "done",
            "processed": {
                "processed_at": now_iso(),
                "transcript_size": ts.get("size"),
                "transcript_mtime_ms": ts.get("mtime_ms"),
                "actions": actions,
                "apply_result": apply_payload,
            },
            "summary_session_id": summary_session_id,
            "summary_session_uuid": summary_session_uuid,
            "summary_input_path": str(summary_input_path),
            "summary_attempts": attempts,
            "summary_output": valid_payload,
        },
    )
    print(f"[dev-memory] summary job done -> {dest}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Run a queued dev-memory session summary job")
    parser.add_argument("--job", required=True)
    parser.add_argument("--queue-dir", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--agent-command", required=True)
    parser.add_argument("--summary-session-id")
    parser.add_argument("--max-attempts", type=int, default=3)
    return run_worker(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
