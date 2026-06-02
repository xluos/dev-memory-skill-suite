import argparse
import json
import sys
import uuid
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from session_summary_worker import run_worker  # noqa: E402


def _write_job(branch, transcript):
    repo_dir = Path(branch["branch_dir"]).parents[1]
    queue_dir = repo_dir / "jobs" / "session-summary"
    pending = queue_dir / "pending"
    pending.mkdir(parents=True)
    job = {
        "schema_version": 1,
        "job_id": "job1",
        "status": "pending",
        "repo_root": str(branch["repo_root"]),
        "repo_key": "repo-key",
        "branch": branch["branch_name"],
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch["branch_dir"]),
        "transcript_path": str(transcript),
        "transcript_state": {"size": transcript.stat().st_size, "mtime_ms": 1},
    }
    job_path = pending / "job1.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return queue_dir, job_path


def test_worker_retries_invalid_json_and_moves_done(branch_dir, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "把当前进展记成 worker 重试成功"}],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    queue_dir, job_path = _write_job(branch_dir, transcript)
    counter = tmp_path / "count.txt"
    sessions = tmp_path / "sessions.txt"
    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        """
import json
import sys
from pathlib import Path
counter = Path(sys.argv[1])
sessions = Path(sys.argv[2])
session_id = sys.argv[3]
session_uuid = sys.argv[4]
count = int(counter.read_text() or "0") if counter.exists() else 0
counter.write_text(str(count + 1))
sessions.write_text((sessions.read_text() if sessions.exists() else "") + session_id + "|" + session_uuid + "\\n")
if count == 0:
    print("not json")
else:
    print(json.dumps({"title": "ok", "progress": "worker 重试成功"}, ensure_ascii=False))
""",
        encoding="utf-8",
    )

    code = run_worker(
        argparse.Namespace(
            job=str(job_path),
            queue_dir=str(queue_dir),
            job_id="job1",
            agent_command=f"python3 {fake_agent} {counter} {sessions} {{summary_session_id}} {{summary_session_uuid}} {{prompt}}",
            summary_session_id="summary-session-1",
            max_attempts=3,
        )
    )

    assert code == 0
    assert not job_path.exists()
    done_path = queue_dir / "done" / "job1.json"
    done = json.loads(done_path.read_text(encoding="utf-8"))
    assert done["summary_session_id"] == "summary-session-1"
    expected_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "dev-memory-summary:summary-session-1"))
    assert done["summary_session_uuid"] == expected_uuid
    assert len(done["summary_attempts"]) == 2
    assert done["summary_attempts"][0]["valid"] is False
    assert done["summary_attempts"][1]["valid"] is True
    assert sessions.read_text().splitlines() == [
        f"summary-session-1|{expected_uuid}",
        f"summary-session-1|{expected_uuid}",
    ]
    progress = branch_dir["paths"]["progress"].read_text(encoding="utf-8")
    assert "worker 重试成功" in progress
