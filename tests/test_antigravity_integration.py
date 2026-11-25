import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_JS = REPO_ROOT / "bin" / "dev-memory.js"
sys.path.insert(0, str(REPO_ROOT / "lib"))

import dev_memory_session_scan as scanner


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _init_memory(repo: Path, storage_root: Path):
    env = {**os.environ, "DEV_MEMORY_ROOT": str(storage_root)}
    subprocess.run(
        ["node", str(CLI_JS), "init", "--repo", str(repo)],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _create_ag_db(ag_home: Path, rows: list[tuple]):
    db_path = ag_home / "conversation_summaries.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE conversation_summaries (
            conversation_id text PRIMARY KEY,
            title text NOT NULL DEFAULT "",
            workspace_uris text NOT NULL,
            status text NOT NULL DEFAULT "",
            source text NOT NULL DEFAULT "",
            parent_conversation_id text NOT NULL DEFAULT "",
            nesting_depth integer NOT NULL DEFAULT 0,
            not_fully_idle numeric NOT NULL DEFAULT 0,
            killed numeric NOT NULL DEFAULT 0
        )
        """
    )
    conn.executemany(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_install_hooks_antigravity_preserves_existing(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    hooks_path = repo / ".agents" / "hooks.json"
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_path.write_text(
        json.dumps({"other-hook": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": "echo ok"}]}]}}),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", str(CLI_JS), "install-hooks", "antigravity", "--repo", str(repo)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["agent"] == "antigravity"
    assert report["target"] == str(hooks_path)
    assert set(report["events"]) == {"SessionStart", "Stop"}

    saved = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert "other-hook" in saved
    assert "dev-memory" in saved
    assert saved["dev-memory"]["SessionStart"][0]["command"] == "dev-memory-cli hook session-start --client antigravity"
    assert saved["dev-memory"]["Stop"][0]["command"] == "dev-memory-cli hook stop --client antigravity"


def test_antigravity_session_start_and_stop_hooks(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    storage_root = tmp_path / "storage"
    dev_home = tmp_path / "dev-home"
    hook_cwd = tmp_path / "global-config-dir"
    hook_cwd.mkdir()
    _init_memory(repo, storage_root)

    logs_dir = tmp_path / "ag" / "brain" / "conv-123" / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    compact = logs_dir / "transcript.jsonl"
    full = logs_dir / "transcript_full.jsonl"
    compact.write_text('{"type":"USER_INPUT","content":"hi"}\n', encoding="utf-8")
    full.write_text('{"type":"USER_INPUT","content":"hi full"}\n', encoding="utf-8")

    payload = {
        "conversationId": "conv-123",
        "workspacePaths": [str(repo)],
        "transcriptPath": str(compact),
        "artifactDirectoryPath": str(tmp_path / "ag" / "brain" / "conv-123"),
        "hookEventName": "SessionStart",
        "cwd": str(repo),
    }
    env = {
        **os.environ,
        "DEV_MEMORY_ROOT": str(storage_root),
        "DEV_MEMORY_HOME": str(dev_home),
    }

    # Run from global config dir (not repo) to verify stdin cwd resolution.
    first = subprocess.run(
        ["node", str(CLI_JS), "hook", "session-start", "--client", "antigravity"],
        cwd=hook_cwd,
        env=env,
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
    )
    first_json = json.loads(first.stdout)
    assert "injectSteps" in first_json
    msg = first_json["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "dev-memory" in msg
    assert "main" in msg

    # Second call for same conversationId is idempotent.
    second = subprocess.run(
        ["node", str(CLI_JS), "hook", "session-start", "--client", "antigravity"],
        cwd=hook_cwd,
        env=env,
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(second.stdout) == {}

    # Stop hook registers candidate pointing to transcript_full.jsonl and returns decision=allow.
    stop_payload = {**payload, "hookEventName": "Stop", "terminationReason": "NO_TOOL_CALL"}
    stop = subprocess.run(
        ["node", str(CLI_JS), "hook", "stop", "--client", "antigravity"],
        cwd=hook_cwd,
        env=env,
        input=json.dumps(stop_payload),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(stop.stdout) == {"decision": "allow"}

    candidates = list((dev_home / "jobs" / "session-scan" / "candidates").glob("*.json"))
    assert len(candidates) == 1
    cand = json.loads(candidates[0].read_text(encoding="utf-8"))
    assert cand["session_id"] == "conv-123"
    assert cand["client"] == "antigravity"
    assert cand["transcript_path"] == str(full)
    assert cand["cwd"] == str(repo.resolve())


def test_parse_and_discover_antigravity_sessions(tmp_path, monkeypatch):
    repo = _init_git_repo(tmp_path / "repo")
    storage_root = tmp_path / "storage"
    dev_home = tmp_path / "dev-home"
    ag_home = tmp_path / "antigravity"
    codex_home = tmp_path / "codex-empty"
    _init_memory(repo, storage_root)

    main_conv = "11111111-1111-1111-1111-111111111111"
    sub_conv = "22222222-2222-2222-2222-222222222222"

    for conv_id in (main_conv, sub_conv):
        logs_dir = ag_home / "brain" / conv_id / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        lines = [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "created_at": "2026-11-25T08:00:00Z",
                "content": "<USER_REQUEST>\n我们决定统一用 PostgreSQL 16。\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nnow\n</ADDITIONAL_METADATA>",
            },
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-25T08:00:05Z",
                "input_tokens": 100,
                "cache_read_tokens": 50,
                "output_tokens": 20,
                "tool_calls": [{"name": "read_file", "args": {}}],
                "content": "中间步骤说明（应跳过）",
            },
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "GENERIC",
                "content": "tool output noise",
            },
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-25T08:00:10Z",
                "input_tokens": 120,
                "cache_read_tokens": 80,
                "output_tokens": 30,
                "content": "好的，已经确认切换到 PostgreSQL 16。",
            },
        ]
        tpath = logs_dir / "transcript_full.jsonl"
        tpath.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
        old_time = time.time() - 3600
        os.utime(tpath, (old_time, old_time))

    uri = "file://" + quote(str(repo.resolve()))
    _create_ag_db(
        ag_home,
        [
            (main_conv, "PG Decision", json.dumps([uri]), "CASCADE_RUN_STATUS_IDLE", "", "", 0, 0, 0),
            (sub_conv, "Subagent", json.dumps([uri]), "CASCADE_RUN_STATUS_IDLE", "", main_conv, 1, 0, 0),
        ],
    )

    monkeypatch.setenv("DEV_MEMORY_ROOT", str(storage_root))
    monkeypatch.setenv("DEV_MEMORY_HOME", str(dev_home))
    monkeypatch.setenv("DEV_MEMORY_ANTIGRAVITY_HOMES", str(ag_home))
    monkeypatch.setattr(scanner, "DEV_MEMORY_HOME", dev_home)
    monkeypatch.setattr(scanner, "SCAN_ROOT", dev_home / "jobs" / "session-scan")
    monkeypatch.setattr(scanner, "CODEX_HOME", codex_home)

    config = scanner.default_scan_config()
    sessions = scanner.discover(config)
    by_id = {s["session_id"]: s for s in sessions}

    assert by_id[sub_conv]["status"] == "skipped"
    assert by_id[sub_conv]["reason"] == "excluded_subagent"

    main_s = by_id[main_conv]
    assert main_s["status"] == "candidate"
    assert len(main_s["messages"]) == 2
    assert main_s["messages"][0]["role"] == "user"
    assert main_s["messages"][0]["text"] == "我们决定统一用 PostgreSQL 16。"
    assert main_s["messages"][1]["role"] == "assistant"
    assert main_s["messages"][1]["text"] == "好的，已经确认切换到 PostgreSQL 16。"
    assert main_s["session_usage"] == {
        "input_tokens": 220,
        "cached_input_tokens": 130,
        "output_tokens": 50,
        "total_tokens": 270,
    }


def test_traecli_executor_args_and_output_parsing():
    preset = scanner.default_scan_config()["executors"]["traecli"]
    args = scanner._executor_args("traecli", preset, "hello")
    assert args[:7] == [
        "traecli",
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
    ]
    assert args[-1] == "hello"

    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t-123"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"skip_reason":"ok"}'}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}}),
    ])
    payload, usage, session_id = scanner._parse_executor_output("traecli", stdout, "")
    assert payload == {"skip_reason": "ok"}
    assert session_id == "t-123"
    assert usage["total_tokens"] == 110
