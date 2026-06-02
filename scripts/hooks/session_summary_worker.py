#!/usr/bin/env python3

import argparse
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
    run_python,
)


ALLOWED_KEYS = {
    "title",
    "progress",
    "next",
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
    for field in ("title", "progress", "next", "skip_reason"):
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


def _move_job(job_path, dest_dir, patch):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    job.update(patch)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(job_path).name
    _atomic_write_json(dest, job)
    try:
        Path(job_path).unlink()
    except FileNotFoundError:
        pass
    return dest


def run_worker(args):
    job_path = Path(args.job)
    queue_dir = Path(args.queue_dir)
    job_id = args.job_id or job_path.stem
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
        apply_result = run_python(
            CAPTURE_SCRIPT,
            "apply-summary-output",
            "--repo",
            summary_input["job"]["repo_root"],
            "--json",
            json.dumps(valid_payload, ensure_ascii=False),
            cwd=summary_input["job"]["repo_root"],
        )
        apply_payload = json.loads(apply_result or "{}")
    except Exception as exc:
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
                "summary_output": valid_payload,
                "error": f"apply-summary-output failed: {exc}",
            },
        )
        print(f"[dev-memory] summary apply failed -> {dest}")
        return 1
    job = json.loads(job_path.read_text(encoding="utf-8"))
    ts = job.get("transcript_state") or {}
    actions = [
        f"{item.get('op')}:{item.get('kind') or item.get('id') or item.get('source')}"
        for item in apply_payload.get("actions", [])
    ]
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
