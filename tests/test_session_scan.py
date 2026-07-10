import json
import sys
from pathlib import Path


LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import dev_memory_session_scan as scan


def _line(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _codex_transcript(path, messages):
    rows = [
        _line({
            "timestamp": "2026-07-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "session_id": "session-1",
                "cwd": str(path.parent),
                "originator": "Codex Desktop",
                "source": "vscode",
                "thread_source": "user",
            },
        })
    ]
    for role, text in messages:
        rows.append(_line({
            "timestamp": "2026-07-01T00:01:00Z",
            "type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": "text", "text": text}]},
        }))
    rows.append(_line({
        "timestamp": "2026-07-01T00:02:00Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}},
        },
    }))
    path.write_text("".join(rows), encoding="utf-8")


def test_parser_keeps_all_message_text_and_byte_cursor(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    long_text = "前" * 5000
    _codex_transcript(transcript, [("user", "第一轮"), ("assistant", long_text), ("user", "最后一轮")])

    parsed = scan.parse_codex_session(transcript)

    assert [item["text"] for item in parsed["messages"]] == ["第一轮", long_text, "最后一轮"]
    assert parsed["messages"][0]["start_offset"] < parsed["messages"][0]["end_offset"]
    assert parsed["end_offset"] == transcript.stat().st_size
    assert parsed["session_usage"]["total_tokens"] == 120

    cursor = parsed["messages"][0]["end_offset"]
    incremental = scan.parse_codex_session(transcript, cursor)
    assert [item["text"] for item in incremental["messages"]] == [long_text, "最后一轮"]


def test_chunks_cover_every_message_without_truncation():
    messages = [
        {"role": "user", "text": "a" * 700, "start_offset": 0, "end_offset": 1},
        {"role": "assistant", "text": "b" * 700, "start_offset": 1, "end_offset": 2},
        {"role": "user", "text": "c" * 2000, "start_offset": 2, "end_offset": 3},
    ]
    chunks = scan._chunk_messages(messages, 1000)
    flattened = [message for chunk in chunks for message in chunk]
    assert "".join(item["text"] for item in flattened) == "a" * 700 + "b" * 700 + "c" * 2000
    assert len(flattened) == 4
    assert flattened[-1]["segment_count"] == 2


def test_codex_executor_is_ephemeral_and_configurable():
    preset = {
        "command": "codex",
        "model": "gpt-test",
        "profile": "provider-a",
        "extra_args": ["--color", "never"],
    }
    args = scan._executor_args("codex", preset, "prompt")
    assert args[:2] == ["codex", "exec"]
    assert "--ephemeral" in args
    assert args[args.index("--model") + 1] == "gpt-test"
    assert args[args.index("--profile") + 1] == "provider-a"
    assert args[-1] == "prompt"


def test_codex_jsonl_parser_uses_agent_message_not_thread_event():
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "internal-1"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"decisions":[{"summary":"保留"}]}'}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 10}}),
    ])
    payload, usage, session_id = scan._parse_executor_output("codex", stdout, "")
    assert payload["decisions"][0]["summary"] == "保留"
    assert usage["total_tokens"] == 60
    assert session_id == "internal-1"


def test_internal_marker_is_detected(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    _codex_transcript(transcript, [("user", scan.INTERNAL_MARKER), ("assistant", "{}")])
    assert scan.parse_codex_session(transcript)["internal_marker"] is True


def test_config_defaults_include_three_executors():
    config = scan.default_scan_config()
    assert config["executor"] == "auto"
    assert config["order"] == ["coco", "codex", "claude"]
    assert set(config["executors"]) == {"coco", "codex", "claude"}
    assert scan.validate_config(config)["valid"] is True


def test_discovery_deduplicates_active_and_archived_copy(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    active = codex_home / "sessions" / "2026" / "07" / "01" / "rollout-a.jsonl"
    archived = codex_home / "archived_sessions" / "rollout-a.jsonl"
    active.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    _codex_transcript(active, [("user", "active")])
    archived.write_text(active.read_text(encoding="utf-8") + _line({
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "archived"}]},
    }), encoding="utf-8")
    monkeypatch.setattr(scan, "CODEX_HOME", codex_home)
    monkeypatch.setattr(scan, "SCAN_ROOT", tmp_path / "scan")
    monkeypatch.setattr(scan, "resolve_target", lambda _cwd: ({"repo_key": "repo", "branch": "main"}, None))

    sessions = scan.discover({**scan.default_scan_config(), "idle_minutes": 0})

    assert len(sessions) == 1
    assert sessions[0]["path"] == str(archived)
    assert [item["text"] for item in sessions[0]["messages"]][-1] == "archived"
