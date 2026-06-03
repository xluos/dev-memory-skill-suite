import json
import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _common import build_summary_input, build_summary_prompt, enqueue_session_summary_job, session_summary_command  # noqa: E402


def _payload(tmp_path, branch="main"):
    repo_dir = tmp_path / "repo-memory"
    branch_dir = repo_dir / "branches" / branch
    repo_dir.mkdir(parents=True)
    branch_dir.mkdir(parents=True)
    return {
        "repo_root": str(tmp_path / "repo"),
        "repo_key": "repo-key",
        "branch": branch,
        "storage_root": str(tmp_path),
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch_dir),
        "last_seen_head": "abc123",
    }


def test_enqueue_session_summary_job_debounces_same_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    payload = _payload(tmp_path)
    hook_input = {
        "session_id": "s1",
        "transcript_path": "/Users/example/.claude/projects/repo/s1.jsonl",
    }

    first = enqueue_session_summary_job(payload, hook_input)
    second = enqueue_session_summary_job(payload, hook_input)

    assert first["job_id"] == second["job_id"]
    job_path = Path(first["job_path"])
    assert job_path.exists()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["session_id"] == "s1"
    assert job["transcript_hints"]["format"] == "claude-jsonl"

    pending = list((Path(payload["repo_dir"]) / "jobs" / "session-summary" / "pending").glob("*.json"))
    assert len(pending) == 1


def test_enqueue_session_summary_job_separates_distinct_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    payload = _payload(tmp_path)

    a = enqueue_session_summary_job(payload, {"session_id": "s1"})
    b = enqueue_session_summary_job(payload, {"session_id": "s2"})

    assert a["job_id"] != b["job_id"]
    pending = list((Path(payload["repo_dir"]) / "jobs" / "session-summary" / "pending").glob("*.json"))
    assert len(pending) == 2


def test_build_summary_prompt_embeds_job_path():
    prompt = build_summary_prompt(
        "/tmp/job.json",
        summary_input={
            "job": {"repo_root": "/tmp/repo"},
            "existing_memory": [],
            "core_messages": [],
            "stats": {"core_message_count": 0},
        },
        summary_input_path="/tmp/input.json",
    )

    assert "/tmp/job.json" in prompt
    assert "/tmp/input.json" in prompt
    assert "SUMMARY_INPUT_JSON" in prompt
    assert "不要调用 `dev-memory-cli summary extract-core`" in prompt
    assert "dev-memory-cli summary extract-core \"" not in prompt
    assert "工具调用" in prompt
    assert "现有 dev-memory" in prompt
    assert "不要追加" in prompt


def test_enqueue_session_summary_job_starts_worker_session(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", raising=False)
    monkeypatch.setenv("DEV_MEMORY_SESSION_SUMMARY_CMD", "true {prompt}")
    started = {}

    class FakeProcess:
        def __init__(self, args, **kwargs):
            started["args"] = args
            started["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakeProcess)
    payload = _payload(tmp_path)

    queued = enqueue_session_summary_job(payload, {"session_id": "s1"})

    assert queued["summary_session_id"] == f"dev-memory-summary-{queued['job_id']}"
    assert queued["agent_log"]
    assert "session_summary_worker.py" in started["args"][1]
    assert "--summary-session-id" in started["args"]


def test_session_summary_command_reads_config(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"session_summary": {"command": "coco -p --yolo {prompt}"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEV_MEMORY_CONFIG_PATH", str(config))
    monkeypatch.delenv("DEV_MEMORY_SESSION_SUMMARY_CMD", raising=False)
    import _common
    monkeypatch.setattr(_common, "DEFAULT_CONFIG_PATH", config)

    assert session_summary_command() == "coco -p --yolo {prompt}"
    monkeypatch.setenv("DEV_MEMORY_SESSION_SUMMARY_CMD", "override {prompt}")
    assert session_summary_command() == "override {prompt}"


def test_summary_input_core_messages_are_role_text_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "uuid": "u1",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "需要记住这个核心需求"}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    payload = _payload(tmp_path)

    queued = enqueue_session_summary_job(payload, {"session_id": "s1", "transcript_path": str(transcript)})
    extracted = build_summary_input(queued["job_path"])
    assert extracted["core_messages"] == [
        {"role": "user", "text": "需要记住这个核心需求"}
    ]
    assert set(extracted["core_messages"][0]) == {"role", "text"}
    assert set(extracted["stats"]) == {"core_message_count", "returned_core_message_count"}


def test_summary_input_memory_entries_are_named_content_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    payload = _payload(tmp_path)
    progress = Path(payload["branch_dir"]) / "progress.md"
    progress.write_text("# 当前进展\n\n有效内容\n", encoding="utf-8")

    queued = enqueue_session_summary_job(payload, {"session_id": "s1"})
    extracted = build_summary_input(queued["job_path"])
    progress_entry = next(item for item in extracted["existing_memory"] if item["name"] == "branch/progress.md")
    assert progress_entry == {
        "name": "branch/progress.md",
        "content": "# 当前进展\n\n有效内容",
    }


def test_enqueue_session_summary_job_carries_previous_processed_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    payload = _payload(tmp_path)
    hook_input = {"session_id": "s1"}
    first = enqueue_session_summary_job(payload, hook_input)
    first_path = Path(first["job_path"])
    done_dir = Path(payload["repo_dir"]) / "jobs" / "session-summary" / "done"
    done_dir.mkdir(parents=True)
    done_path = done_dir / first_path.name
    job = json.loads(first_path.read_text(encoding="utf-8"))
    job["status"] = "done"
    job["processed"] = {
        "transcript_size": 123,
        "transcript_mtime_ms": 456,
        "processed_at": "2026-06-02T00:00:00+00:00",
    }
    first_path.unlink()
    done_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    second = enqueue_session_summary_job(payload, hook_input)
    queued = json.loads(Path(second["job_path"]).read_text(encoding="utf-8"))

    assert queued["job_id"] == first["job_id"]
    assert queued["previous_job"]["state"] == "done"
    assert queued["previous_job"]["processed"]["transcript_size"] == 123


def test_enqueue_session_summary_job_carries_previous_skipped_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT", "1")
    payload = _payload(tmp_path)
    hook_input = {"session_id": "s1"}
    first = enqueue_session_summary_job(payload, hook_input)
    first_path = Path(first["job_path"])
    skipped_dir = Path(payload["repo_dir"]) / "jobs" / "session-summary" / "skipped"
    skipped_dir.mkdir(parents=True)
    skipped_path = skipped_dir / first_path.name
    job = json.loads(first_path.read_text(encoding="utf-8"))
    job["status"] = "skipped"
    job["processed"] = {
        "transcript_size": 123,
        "transcript_mtime_ms": 456,
        "processed_at": "2026-06-02T00:00:00+00:00",
    }
    first_path.unlink()
    skipped_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    second = enqueue_session_summary_job(payload, hook_input)
    queued = json.loads(Path(second["job_path"]).read_text(encoding="utf-8"))

    assert queued["job_id"] == first["job_id"]
    assert queued["previous_job"]["state"] == "skipped"
    assert queued["previous_job"]["processed"]["transcript_size"] == 123
