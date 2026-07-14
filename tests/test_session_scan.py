import json
import sys
from argparse import Namespace
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


def test_maintenance_agent_marker_is_detected(tmp_path):
    transcript = tmp_path / "maintenance.jsonl"
    _codex_transcript(transcript, [("user", scan.MAINTENANCE_MARKER), ("assistant", "开始整理")])
    assert scan.parse_codex_session(transcript)["internal_marker"] is True


def test_config_defaults_include_three_executors():
    config = scan.default_scan_config()
    assert config["executor"] == "auto"
    assert config["order"] == ["coco", "codex", "claude"]
    assert set(config["executors"]) == {"coco", "codex", "claude"}
    assert config["schedule_times"] == ["03:00", "13:00"]
    assert config["skip_when_computer_active"] is True
    assert config["active_within_minutes"] == 10
    assert config["invocation_timeout_seconds"] == 360
    assert scan.validate_config(config)["valid"] is True


def test_calendar_intervals_support_multiple_configured_times():
    config = {**scan.default_scan_config(), "schedule_times": ["13:00", "03:00", "13:00"]}

    assert scan._calendar_intervals(config) == [
        {"Hour": 3, "Minute": 0},
        {"Hour": 13, "Minute": 0},
    ]


def test_computer_activity_uses_configured_idle_threshold(monkeypatch):
    config = {**scan.default_scan_config(), "active_within_minutes": 10}
    monkeypatch.setattr(scan, "_mac_idle_seconds", lambda: 90)
    assert scan.computer_activity(config)["status"] == "active"
    assert scan.computer_activity(config)["skip"] is True

    monkeypatch.setattr(scan, "_mac_idle_seconds", lambda: 900)
    assert scan.computer_activity(config)["status"] == "idle"
    assert scan.computer_activity(config)["skip"] is False


def test_scheduled_scan_skips_before_discovery_when_computer_is_active(tmp_path, monkeypatch, capsys):
    config = scan.default_scan_config()
    monkeypatch.setattr(scan, "SCAN_ROOT", tmp_path / "scan")
    monkeypatch.setattr(scan, "load_config", lambda: config)
    monkeypatch.setattr(scan, "computer_activity", lambda _config: {
        "status": "active",
        "idle_seconds": 5,
        "threshold_seconds": 600,
        "skip": True,
    })
    monkeypatch.setattr(scan, "discover", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("discover must not run")))

    code = scan.run_scan(Namespace(scheduled=True, dry_run=False, json=True, since=None))

    assert code == 0
    last_run = json.loads((scan.SCAN_ROOT / "last-run.json").read_text(encoding="utf-8"))
    assert last_run["status"] == "skipped_active"
    assert last_run["session_count"] == 0
    assert not (scan.SCAN_ROOT / "scan-origin.json").exists()
    assert "skipped_active" in capsys.readouterr().out


def test_set_schedule_reloads_installed_launch_agent(tmp_path, monkeypatch):
    config = scan.default_scan_config()
    plist = tmp_path / "session-scan.plist"
    plist.write_text("installed", encoding="utf-8")
    saved = {}
    reloaded = []
    monkeypatch.setattr(scan, "PLIST_PATH", plist)
    monkeypatch.setattr(scan, "load_config", lambda: config)
    monkeypatch.setattr(scan, "save_scan_config", lambda value: saved.update(value))
    monkeypatch.setattr(scan, "command_install", lambda _args: reloaded.append(True))

    scan.command_config(Namespace(config_command="set-schedule", times=["03:00", "13:00", "18:30"]))

    assert saved["schedule_times"] == ["03:00", "13:00", "18:30"]
    assert reloaded == [True]


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


def _candidate_session(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("abc", encoding="utf-8")
    branch_dir = tmp_path / "memory" / "branches" / "main"
    repo_dir = tmp_path / "memory"
    branch_dir.mkdir(parents=True)
    return {
        "session_id": "session-1",
        "path": str(transcript),
        "size": 3,
        "end_offset": 3,
        "mtime": "2026-07-01T00:00:00Z",
        "meta": {"cwd": str(tmp_path), "originator": "Codex Desktop"},
        "messages": [{"role": "user", "text": "记住这个稳定决策", "start_offset": 0, "end_offset": 3}],
        "session_usage": None,
        "internal_marker": False,
        "cursor_before": 0,
        "new_bytes": 3,
        "target": {
            "repo_root": str(tmp_path),
            "repo_key": "repo-key",
            "repo_dir": str(repo_dir),
            "branch": "main",
            "branch_key": "main",
            "branch_dir": str(branch_dir),
            "storage_root": str(tmp_path / "storage"),
        },
        "status": "candidate",
        "reason": None,
    }


def test_single_chunk_uses_one_final_invocation_and_rejects_empty_output(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "SCAN_ROOT", tmp_path / "scan")
    monkeypatch.setattr(scan, "choose_executor", lambda _config: ("fake", {"model": None, "profile": None}))
    calls = []

    def fake_executor(_name, _preset, prompt, _cwd, _run_id, invocation, _max_attempts):
        calls.append((prompt, invocation))
        return {}, [{"invocation": invocation, "returncode": 0, "usage": {"total_tokens": 10}}]

    monkeypatch.setattr(scan, "run_executor_with_retries", fake_executor)
    monkeypatch.setattr(scan, "_apply_summary", lambda *_args: (_ for _ in ()).throw(AssertionError("must not apply")))

    code = scan._execute_sessions(
        scan.default_scan_config(),
        Namespace(dry_run=False, json=False, scheduled=False),
        [_candidate_session(tmp_path)],
        "run-1",
        0,
    )

    assert code == 1
    assert len(calls) == 1
    assert calls[0][1].endswith(":final")
    assert "直接生成最终 summary-output" in calls[0][0]
    run = json.loads((scan.SCAN_ROOT / "runs" / "run-1.json").read_text(encoding="utf-8"))
    assert run["sessions"][0]["status"] == "failed"
    assert "no memory mutations or skip_reason" in run["sessions"][0]["error"]
    assert not (scan.SCAN_ROOT / "state" / "session-1.json").exists()


def test_explicit_summary_skip_advances_cursor_without_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "SCAN_ROOT", tmp_path / "scan")
    monkeypatch.setattr(scan, "choose_executor", lambda _config: ("fake", {"model": None, "profile": None}))
    monkeypatch.setattr(
        scan,
        "run_executor_with_retries",
        lambda *_args: (
            {"skip_reason": "existing memory already covers this session"},
            [{"returncode": 0, "usage": {"total_tokens": 10}}],
        ),
    )
    monkeypatch.setattr(scan, "_apply_summary", lambda *_args: (_ for _ in ()).throw(AssertionError("must not apply")))

    code = scan._execute_sessions(
        scan.default_scan_config(),
        Namespace(dry_run=False, json=False, scheduled=False),
        [_candidate_session(tmp_path)],
        "run-2",
        0,
    )

    assert code == 0
    run = json.loads((scan.SCAN_ROOT / "runs" / "run-2.json").read_text(encoding="utf-8"))
    audit = run["sessions"][0]
    assert audit["status"] == "skipped_summary"
    assert audit["summary_output"]["skip_reason"] == "existing memory already covers this session"
    assert run["summary_skipped_count"] == 1
    state = json.loads((scan.SCAN_ROOT / "state" / "session-1.json").read_text(encoding="utf-8"))
    assert state["processed_offset"] == 3


def test_semantic_apply_is_done_and_advances_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "SCAN_ROOT", tmp_path / "scan")
    monkeypatch.setattr(scan, "choose_executor", lambda _config: ("fake", {"model": None, "profile": None}))
    monkeypatch.setattr(
        scan,
        "run_executor_with_retries",
        lambda *_args: (
            {"decisions": [{"summary": "稳定决策"}]},
            [{"returncode": 0, "usage": {"total_tokens": 10}}],
        ),
    )
    monkeypatch.setattr(
        scan,
        "_apply_summary",
        lambda *_args: {
            "touched_targets": [{"file": "decisions.md"}],
            "actions": [{"op": "append", "kind": "decision"}],
            "skip_reason": None,
        },
    )

    code = scan._execute_sessions(
        scan.default_scan_config(),
        Namespace(dry_run=False, json=False, scheduled=False),
        [_candidate_session(tmp_path)],
        "run-3",
        0,
    )

    assert code == 0
    run = json.loads((scan.SCAN_ROOT / "runs" / "run-3.json").read_text(encoding="utf-8"))
    assert run["done_count"] == 1
    assert run["sessions"][0]["semantic_action_count"] == 1
    assert run["sessions"][0]["summary_output"]["field_counts"] == {"decisions": 1}


def test_replay_reconstructs_historical_cursor_slice(tmp_path, monkeypatch):
    transcript = tmp_path / "rollout.jsonl"
    _codex_transcript(transcript, [("user", "第一段"), ("assistant", "第二段")])
    end_offset = transcript.stat().st_size
    target = {
        "repo_root": str(tmp_path),
        "repo_key": "repo-key",
        "repo_dir": str(tmp_path / "memory"),
        "branch": "main",
        "branch_key": "main",
        "branch_dir": str(tmp_path / "memory" / "branches" / "main"),
        "storage_root": str(tmp_path / "storage"),
    }
    monkeypatch.setattr(scan, "resolve_target", lambda _cwd: (target, None))

    replay = scan._replay_session("old-run", {
        "session_id": "session-1",
        "path": str(transcript),
        "cwd": str(tmp_path),
        "cursor_before": 0,
        "cursor_after": end_offset,
        "raw_size": end_offset,
        "status": "done",
        "apply_result": {"touched_targets": []},
    })

    assert replay["status"] == "candidate"
    assert [item["text"] for item in replay["messages"]] == ["第一段", "第二段"]
    assert replay["replay_source"]["previous_apply_empty"] is True
    assert replay["end_offset"] == end_offset


def test_covered_bytes_deduplicates_repeated_observations():
    assert scan._covered_bytes([(0, 100), (0, 100), (100, 140), (120, 160)]) == 160


def test_executor_timeout_is_a_single_failed_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        scan.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(scan.subprocess.TimeoutExpired("fake", 30)),
    )

    payload, records = scan.run_executor_with_retries(
        "fake",
        {"command": "fake", "_timeout_seconds": 30},
        "prompt",
        str(tmp_path),
        "run-timeout",
        "session-1:final",
        2,
    )

    assert payload is None
    assert len(records) == 1
    assert records[0]["timed_out"] is True
    assert records[0]["timeout_seconds"] == 30


def test_replay_parser_accepts_one_shot_executor_override():
    args = scan.build_parser().parse_args([
        "replay",
        "--run-id",
        "run-1",
        "--session-id",
        "session-1",
        "--executor",
        "codex",
    ])

    assert args.executor == "codex"


def test_summary_payload_validation_rejects_schema_placeholders():
    errors = scan._summary_payload_validation_errors({
        "decisions": [{"summary": "", "reason": "真实原因"}],
        "risks": ["risk\nmitigation"],
        "glossary": ["term\ndefinition"],
        "shared_sources": ["name\nurl\nnote"],
    })

    assert errors == [
        "decisions[0] requires a non-empty summary",
        "risks[0] contains schema placeholder text",
        "glossary[0] contains schema placeholder text",
        "shared_sources[0] contains schema placeholder text",
    ]
