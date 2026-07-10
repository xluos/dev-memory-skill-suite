#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _text_from_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        typ = item.get("type")
        if typ in ("tool_use", "tool_result", "function_call", "function_call_output"):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_claude(obj):
    typ = obj.get("type")
    if typ not in ("user", "assistant"):
        return None
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    role = msg.get("role") or typ
    text = _text_from_content(msg.get("content"))
    if not text:
        return None
    return {
        "source": "claude",
        "role": role,
        "timestamp": obj.get("timestamp"),
        "uuid": obj.get("uuid"),
        "text": text,
    }


def _extract_codex(obj):
    if obj.get("type") != "response_item":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None
    text = _text_from_content(payload.get("content"))
    if not text:
        return None
    return {
        "source": "codex",
        "role": role,
        "timestamp": obj.get("timestamp"),
        "text": text,
    }


def _iter_core_messages(transcript_path, since_size=0):
    if not transcript_path:
        return []
    path = Path(transcript_path).expanduser()
    if not path.exists():
        return []
    out = []
    with path.open("rb") as f:
        while True:
            start = f.tell()
            raw = f.readline()
            if not raw:
                break
            end = f.tell()
            if end <= since_size:
                continue
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = _extract_claude(obj) or _extract_codex(obj)
            if msg:
                msg["start_offset"] = start
                msg["end_offset"] = end
                out.append(msg)
    return out


def _truncate(text, max_chars):
    text = (text or "").strip()
    if not max_chars or max_chars < 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _is_nonsemantic_user_text(text):
    stripped = (text or "").strip()
    if not stripped:
        return True
    markers = (
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "Your tool call was malformed",
    )
    return any(marker in stripped for marker in markers)


def _memory_file(path, max_chars, name=None):
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    item = {
        "name": name or p.name,
        "content": _truncate(text, max_chars),
    }
    if len(text) > max_chars:
        item["truncated"] = True
    return item


def _summary_job(job):
    transcript_state = job.get("transcript_state") if isinstance(job.get("transcript_state"), dict) else {}
    previous_job = job.get("previous_job") if isinstance(job.get("previous_job"), dict) else {}
    return {
        "repo_root": job.get("repo_root"),
        "transcript_state": {
            key: transcript_state.get(key)
            for key in ("size", "mtime_ms")
            if transcript_state.get(key) is not None
        },
        "previous_processed": previous_job.get("processed") if isinstance(previous_job.get("processed"), dict) else None,
    }


def extract_core_payload(
    job,
    *,
    max_messages=0,
    max_message_chars=0,
    max_memory_chars=6000,
    since_size=0,
    include_message_metadata=False,
):
    branch_dir = Path(job["branch_dir"])
    repo_dir = Path(job["repo_dir"])
    memory_paths = [
        ("branch/progress.md", branch_dir / "progress.md"),
        ("branch/risks.md", branch_dir / "risks.md"),
        ("branch/decisions.md", branch_dir / "decisions.md"),
        ("branch/glossary.md", branch_dir / "glossary.md"),
        ("branch/overview.md", branch_dir / "overview.md"),
        ("repo/decisions.md", repo_dir / "repo" / "decisions.md"),
        ("repo/glossary.md", repo_dir / "repo" / "glossary.md"),
    ]
    messages = _iter_core_messages(job.get("transcript_path"), since_size=since_size)
    messages = [m for m in messages if not _is_nonsemantic_user_text(m["text"])]
    recent = messages[-max_messages:] if max_messages and max_messages > 0 else messages
    core_messages = []
    for m in recent:
        item = {
            "role": m["role"],
            "text": _truncate(m["text"], max_message_chars),
        }
        if include_message_metadata:
            item = {**m, "text": item["text"]}
        core_messages.append(item)

    return {
        "job": _summary_job(job),
        "existing_memory": [
            item
            for item in (
                _memory_file(path, max_memory_chars, name=name)
                for name, path in memory_paths
            )
            if item is not None
        ],
        "core_messages": core_messages,
        "stats": {
            "core_message_count": len(messages),
            "returned_core_message_count": len(recent),
        },
    }


def command_extract_core(args):
    job = _read_json(args.job)
    payload = extract_core_payload(
        job,
        max_messages=args.max_messages,
        max_message_chars=args.max_message_chars,
        max_memory_chars=args.max_memory_chars,
        since_size=args.since_size,
        include_message_metadata=args.include_message_metadata,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Session summary helper commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("extract-core", help="Extract core transcript messages and current memory for a summary job")
    p.add_argument("job", help="Path to a session-summary pending job JSON")
    p.add_argument("--max-messages", type=int, default=0, help="0 keeps every semantic message")
    p.add_argument("--max-message-chars", type=int, default=0, help="0 keeps complete message text")
    p.add_argument("--max-memory-chars", type=int, default=6000)
    p.add_argument("--since-size", type=int, default=0, help="Read JSONL records ending after this byte offset")
    p.add_argument(
        "--include-message-metadata",
        action="store_true",
        help="Include source/timestamp/uuid fields in core_messages for debugging",
    )

    args = parser.parse_args()
    if args.command == "extract-core":
        command_extract_core(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
